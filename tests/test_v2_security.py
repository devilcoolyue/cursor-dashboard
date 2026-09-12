from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import delete, select
from test_core import CoreFixture
from test_identity import IdentityFixture
from test_v2_api import APIClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cursor_dashboard.api.app import create_app
from cursor_dashboard.domain.core import CoreError
from cursor_dashboard.infrastructure.persistence.models import AuditEvent, UserSession
from cursor_dashboard.local.switching import SwitchExecutor
from cursor_dashboard.runtime.cli import main as maintenance_main
from cursor_dashboard.runtime.retention import prune_backup_files
from cursor_dashboard.runtime.server import trusted_proxies
from cursor_dashboard.runtime.settings import CoreConfig


class ProxyTest(IdentityFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.core.config = replace(self.config, mode="server")
        self.app = create_app(self.core, public_origin="https://panel.example.test")

    def client(self, peer):
        middleware = ProxyHeadersMiddleware(self.app, trusted_hosts=trusted_proxies("172.30.87.2"))
        async def transport(scope, receive, send):
            await middleware({**scope, "client": (peer, 12345)}, receive, send)
        return APIClient(transport)

    async def test_proxy_clients_have_separate_limits_and_spoofed_chain_is_ignored(self):
        client = self.client("172.30.87.2")
        with patch("cursor_dashboard.application.identity.verify_password", return_value=False):
            for index in range(20):
                result = await client.request("POST", "/api/v1/auth/login",
                    {"login": f"missing-{index}@example.test", "password": "wrong"},
                    headers={"x-forwarded-for": f"203.0.113.{index+1}, 198.51.100.10"})
                self.assertEqual(result[0], 401)
        from test_identity import PASSWORD
        body = {"login": "first@example.test", "password": PASSWORD}
        self.assertEqual((await client.request("POST", "/api/v1/auth/login", body,
            headers={"x-forwarded-for": "198.51.100.10"}))[0], 429)
        self.assertEqual((await client.request("POST", "/api/v1/auth/login", body,
            headers={"x-forwarded-for": "198.51.100.20"}))[0], 200)

    async def test_direct_clients_cannot_choose_a_new_source_with_forwarded_headers(self):
        client = self.client("198.51.100.50")
        with patch("cursor_dashboard.application.identity.verify_password", return_value=False):
            for index in range(21):
                result = await client.request("POST", "/api/v1/auth/login",
                    {"login": f"missing-{index}@example.test", "password": "wrong"},
                    headers={"x-forwarded-for": f"203.0.113.{index+1}"})
                self.assertEqual(result[0], 401 if index < 20 else 429)


class AuditRetentionTest(CoreFixture, unittest.IsolatedAsyncioTestCase):
    def prepare(self):
        self.core.retention.config = replace(self.config, audit_retention_days=1, audit_max_events=5)
        self.core.retention.batch_size = 3
        now = time.time()
        with self.core.db.transaction(write=True) as session:
            session.execute(delete(AuditEvent))
            for index in range(12):
                session.add(AuditEvent(id=str(index), workspace_id=self.first if index % 2 else self.second,
                    action="synthetic", created_at=now - (172800 if index < 8 else 100 - index)))
            session.add(UserSession(user_id=self.actor.user_id, token_hash="expired", csrf_hash="test",
                                    created_at=0, expires_at=0))
            session.add(UserSession(user_id=self.actor.user_id, token_hash="live", csrf_hash="test",
                                    created_at=now, expires_at=now + 3600))

    async def test_age_and_size_batches_preserve_new_events_and_live_sessions(self):
        self.prepare()
        for _ in range(6):
            result = self.core.retention.prune()
            self.assertLessEqual(result["audit_events"], 3)
        with self.core.db.transaction() as session:
            self.assertEqual(set(session.scalars(select(AuditEvent.id))), {"8", "9", "10", "11"})
            self.assertEqual(list(session.scalars(select(UserSession.token_hash))), ["live"])
        self.assertEqual(self.core.retention.prune()["audit_events"], 0)

    async def test_recent_events_are_also_bounded_by_total_count(self):
        self.core.retention.config = replace(self.config, audit_max_events=5)
        self.core.retention.batch_size = 3
        with self.core.db.transaction(write=True) as session:
            session.execute(delete(AuditEvent))
            for index in range(12):
                session.add(AuditEvent(id=str(index), action="synthetic", created_at=time.time() + index))
        for _ in range(3):
            self.assertLessEqual(self.core.retention.prune()["audit_events"], 3)
        with self.core.db.transaction() as session:
            self.assertEqual(set(session.scalars(select(AuditEvent.id))), {"7", "8", "9", "10", "11"})

    async def test_server_lifespan_starts_and_stops_retention(self):
        from test_identity import PASSWORD
        self.core.config = replace(self.config, mode="server")
        self.core.identity.initialize_server("first@example.test", PASSWORD)
        self.prepare()
        app = create_app(self.core, public_origin="https://panel.example.test")
        async with app.router.lifespan_context(app):
            for _ in range(100):
                with self.core.db.transaction() as session:
                    count = len(list(session.scalars(select(AuditEvent.id))))
                if count < 12:
                    break
                await asyncio.sleep(.01)
            self.assertLess(count, 12)

    async def test_offline_compaction_requires_lock_and_reclaims_old_audit_pages(self):
        args = ["--data-dir", str(self.config.data_dir), "--key-file", str(self.key), "maintain", "--compact"]
        with patch("builtins.print"):
            self.assertEqual(maintenance_main(args), 1)
        with self.core.db.transaction(write=True) as session:
            for _ in range(100):
                session.add(AuditEvent(action="synthetic", created_at=0, changes={"fields": ["x" * 4000]}))
        self.core.close()
        before = self.config.database.stat().st_size
        with patch("builtins.print"):
            self.assertEqual(maintenance_main(args), 0)
        self.assertLess(self.config.database.stat().st_size, before)


class BackupRetentionTest(unittest.TestCase):
    def test_native_retention_keeps_restore_source_and_new_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            executor = SwitchExecutor(Path(folder), None)
            source = sqlite3.connect(":memory:")
            try:
                source.execute("CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value TEXT)")
                source.commit()
                old = executor.backup_path(executor.backup(source))
                os.utime(old, (0, 0))
                for _ in range(25):
                    newest = executor.backup(source, protected=(old,))
                self.assertEqual(len(list(executor.directory.glob("*.sqlite"))), 20)
                self.assertTrue(old.exists())
                self.assertTrue(executor.backup_path(newest).exists())
            finally:
                source.close()

    def test_byte_budget_preserves_current_backup_and_ignores_links_and_foreign_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = [root / f"{uuid.uuid4()}.db" for _ in range(3)]
            for index, path in enumerate(paths):
                path.write_bytes(b"x" * 100)
                os.utime(path, (index, index))
            foreign = root / "foreign"
            foreign.write_bytes(b"outside")
            link = root / f"{uuid.uuid4()}.db"
            try:
                link.symlink_to(foreign)
            except OSError:
                link = None  # Windows may require an elevated symlink privilege.
            prune_backup_files(root, r"[0-9a-f-]+\.db", keep=3, max_bytes=1, protected=(paths[0],))
            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[2].exists())
            self.assertFalse(paths[1].exists())
            self.assertEqual(foreign.read_bytes(), b"outside")
            if link is not None:
                self.assertTrue(link.is_symlink())

    def test_v2_terminal_backup_retention_uses_only_synthetic_files(self):
        script = Path(__file__).resolve().parents[1] / "cursor_dashboard/scripts/prune-backups.cjs"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(25):
                (root / f"synthetic.db.cursor-panel-{index}-1.bak").write_bytes(b"fixture")
            foreign = root / "synthetic.db.cursor-panel-user.bak"
            foreign.write_bytes(b"preserve")
            source = "const fs=require('fs'),path=require('path');const databasePath=process.argv[1],backup=process.argv[2];\n" + script.read_text()
            current = root / "synthetic.db.cursor-panel-0-1.bak"
            subprocess.run(["node", "-e", source, str(root / "synthetic.db"), str(current)], check=True, capture_output=True)
            self.assertTrue(current.exists())
            self.assertTrue(foreign.exists())
            self.assertEqual(len(list(root.glob("*-1.bak"))), 20)


class ConfigurationTest(unittest.TestCase):
    def test_proxy_and_retention_configuration_fail_closed(self):
        for value in ("*", "0.0.0.0/0", "::/0", "caddy.example", "invalid"):
            with self.assertRaises(CoreError):
                trusted_proxies(value)
        self.assertEqual(trusted_proxies(""), [])
        self.assertEqual(trusted_proxies("127.0.0.1,::1"), ["127.0.0.1/32", "::1/128"])
        for value in ("0", "-1", "invalid"):
            with self.assertRaises(CoreError):
                CoreConfig.from_env({"CURSOR_CORE_DATA_DIR": "/tmp/synthetic-core", "CURSOR_CORE_KEY_FILE": "/tmp/synthetic-key",
                                     "CURSOR_AUDIT_RETENTION_DAYS": value})
