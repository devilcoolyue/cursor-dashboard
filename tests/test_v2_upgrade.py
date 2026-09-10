from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import select

from cursor_dashboard.domain.core import Actor, CoreError
from cursor_dashboard.infrastructure.persistence.database import Database
from cursor_dashboard.infrastructure.persistence.legacy import import_backup
from cursor_dashboard.infrastructure.persistence.models import AuditEvent, LegacyImport, LegacyMapping, Membership, User
from cursor_dashboard.infrastructure.persistence.repository import Repository
from cursor_dashboard.infrastructure.secrets import Cipher, FileKeyProvider
from cursor_dashboard.runtime.cli import main
from cursor_dashboard.runtime.core import Core
from cursor_dashboard.runtime.settings import CoreConfig
from test_core import make_legacy
from test_identity import IdentityFixture, PASSWORD


class UpgradeTest(unittest.TestCase):
    def test_p4_local_upgrade_keeps_sessions_and_tickets_and_creates_wal_backup(self):
        with tempfile.TemporaryDirectory(prefix="p5-upgrade-") as directory:
            root = Path(directory)
            key = root / "key.json"
            FileKeyProvider.create(key)
            config = CoreConfig(root / "state", key)
            config.data_dir.mkdir()
            db = Database(config.database)
            try:
                with db.engine.begin() as connection:
                    migration = db.alembic_config()
                    migration.attributes["connection"] = connection
                    command.upgrade(migration, "0002_identity")
                    connection.exec_driver_sql("INSERT INTO users VALUES ('u','local@example.test',NULL,1,0)")
                    connection.exec_driver_sql("INSERT INTO workspaces VALUES ('w','personal','Local')")
                    connection.exec_driver_sql("INSERT INTO memberships VALUES ('w','u','owner')")
                    connection.exec_driver_sql("INSERT INTO accounts VALUES ('a','w','cursor','subject','test@example.test','Test',0)")
                    connection.exec_driver_sql("INSERT INTO user_sessions VALUES ('s','u','hash','csrf',0,9999999999,0)")
                    connection.exec_driver_sql("INSERT INTO switch_tickets VALUES ('t','ticket','w','a','u','s','generation',1,9999999999,NULL)")
                Repository(db, Cipher(FileKeyProvider(key))).check_key(initialize=True)
            finally:
                db.close()
            with Core(config, upgrade=True) as core:
                with core.db.engine.connect() as connection:
                    self.assertEqual(connection.exec_driver_sql("SELECT id,kind FROM user_sessions").fetchall(), [("s", "web")])
                    self.assertEqual(connection.exec_driver_sql("SELECT id FROM switch_tickets").scalar_one(), "t")
                    self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall(), [])
            backups = list(config.data_dir.glob("pre-upgrade-*.db"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0002_identity")
                self.assertEqual(backup.execute("SELECT id FROM switch_tickets").fetchone()[0], "t")
            with Core(config, upgrade=True):
                pass
            self.assertEqual(len(list(config.data_dir.glob("pre-upgrade-*.db"))), 1)

    def test_real_p1_schema_upgrades_without_losing_owner_and_supports_server_init(self):
        with tempfile.TemporaryDirectory(prefix="p2-upgrade-") as directory:
            root = Path(directory)
            key = root / "key.json"
            FileKeyProvider.create(key)
            config = CoreConfig(root / "state", key, mode="server")
            config.data_dir.mkdir()
            db = Database(config.database)
            try:
                with db.engine.begin() as connection:
                    migration = db.alembic_config()
                    migration.attributes["connection"] = connection
                    command.upgrade(migration, "0001_core")
                    connection.exec_driver_sql("INSERT INTO users VALUES ('old-user', 'owner@example.test', NULL, 1)")
                    connection.exec_driver_sql("INSERT INTO workspaces VALUES ('old-space', 'team', 'Imported')")
                    connection.exec_driver_sql("INSERT INTO memberships VALUES ('old-space', 'old-user', 'owner')")
                Repository(db, Cipher(FileKeyProvider(key))).check_key(initialize=True)
            finally:
                db.close()
            with self.assertRaisesRegex(CoreError, "schema is not current"):
                Core(config)
            with Core(config, upgrade=True) as core:
                self.assertEqual(core.repository.verify()["schema"], "0003_devices")
                result = core.identity.initialize_server("owner@example.test", PASSWORD)
                self.assertEqual(result["user_id"], "old-user")
                me = core.identity.me(Actor("old-user"))
                self.assertEqual({s["kind"] for s in me["workspaces"]}, {"team", "personal"})
                self.assertTrue(me["instance_admin"])
            with Core(config) as reopened:
                self.assertEqual(reopened.identity.login("owner@example.test", PASSWORD).actor.user_id, "old-user")

    def test_cli_initialization_and_recovery_use_hidden_password_input(self):
        with tempfile.TemporaryDirectory(prefix="p2-cli-") as directory:
            root = Path(directory)
            key = root / "key.json"
            FileKeyProvider.create(key)
            args = ["--data-dir", str(root / "state"), "--key-file", str(key)]
            with patch("cursor_dashboard.runtime.cli.getpass.getpass", return_value=PASSWORD), patch("builtins.print") as output:
                self.assertEqual(main([*args, "server-init", "--login", "owner@example.test"]), 0)
                self.assertEqual(main([*args, "recover-password", "--login", "owner@example.test"]), 0)
                self.assertNotIn(PASSWORD, str(output.call_args_list))
            with Core(CoreConfig(root / "state", key)) as core:
                self.assertTrue(core.identity.initialized())
                core.identity.login("owner@example.test", PASSWORD)


class ImportedWorkspaceTest(IdentityFixture, unittest.TestCase):
    def test_owner_can_delete_imported_workspace_atomically(self):
        source = self.root / "backup.db"
        make_legacy(source)
        import_backup(self.repo, self.actor, self.first, source)
        self.spaces.delete(self.actor, self.first)
        with self.core.db.transaction() as session:
            self.assertIsNone(session.scalar(select(LegacyImport)))
            self.assertIsNone(session.scalar(select(LegacyMapping)))
            self.assertIsNone(session.get(Membership, (self.first, self.actor.user_id)))
            self.assertIsNotNone(session.get(User, self.actor.user_id))
            self.assertIsNotNone(session.scalar(select(AuditEvent).where(AuditEvent.action == "workspace.delete")))
