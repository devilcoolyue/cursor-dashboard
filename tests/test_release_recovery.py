"""P6 offline migration/update/rollback rehearsals using disposable state only."""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from alembic import command, op

from cursor_dashboard.domain.core import Actor, CoreError, Secrets, Unauthenticated
from cursor_dashboard.infrastructure.persistence.database import Database
from cursor_dashboard.infrastructure.persistence.legacy import import_backup
from cursor_dashboard.infrastructure.persistence.repository import Repository
from cursor_dashboard.infrastructure.secrets import Cipher, FileKeyProvider
from cursor_dashboard.runtime.backup import backup, copy_database, restore
from cursor_dashboard.runtime.core import Core
from cursor_dashboard.runtime.settings import CoreConfig
from test_core import CoreFixture, make_legacy
from test_identity import IdentityFixture


class LegacyRollbackTest(CoreFixture, unittest.TestCase):
    def test_legacy_import_edit_and_rollback_keep_old_source_and_isolation(self):
        source = self.root / "legacy-backup.db"
        make_legacy(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = import_backup(self.repo, self.actor, self.first, source)
        self.assertEqual(result["accounts"], 1)
        row = self.core.accounts.list(self.actor, self.first)[0]
        self.repo.edit(self.actor, self.first, row["id"], label="V2 edit")
        self.assertEqual(self.core.accounts.list(self.other, self.second), [])
        self.assertTrue(import_backup(self.repo, self.actor, self.first, source)["already_imported"])
        self.core.close()
        rollback = self.root / "legacy-rollback.db"
        copy_database(source, rollback)
        with closing(sqlite3.connect(rollback)) as connection:
            self.assertEqual(connection.execute("SELECT label FROM accounts").fetchone()[0], "Legacy")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)


class FailedUpgradeTest(unittest.TestCase):
    def test_partial_schema_failure_rolls_back_and_backup_recovers_committed_wal(self):
        with tempfile.TemporaryDirectory(prefix="p6-upgrade-") as temporary:
            root = Path(temporary)
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
                repo = Repository(db, Cipher(FileKeyProvider(key)))
                repo.check_key(initialize=True)
                owner = repo.create_workspace("upgrade@example.test", "Upgrade", "personal")
                row = repo.put_authorization(Actor(owner["user_id"]), owner["workspace_id"],
                    email="account@example.test", subject="fixture", label="Before WAL",
                    secrets=Secrets("synthetic-cookie", "synthetic-at", "synthetic-rt"), expires_at=0)
            finally:
                db.close()
            # Keep a connection alive so the committed label is still in WAL
            # when the new application's upgrade and automatic backup start.
            with closing(sqlite3.connect(config.database)) as wal:
                wal.execute("PRAGMA journal_mode=WAL")
                wal.execute("PRAGMA wal_autocheckpoint=0")
                wal.execute("UPDATE accounts SET label='Committed WAL'")
                wal.commit()
                self.assertGreater(Path(str(config.database) + "-wal").stat().st_size, 0)
                real_create = op.create_table

                def fail_after_ddl(name, *args, **kwargs):
                    result = real_create(name, *args, **kwargs)
                    if name == "device_authorizations":
                        raise RuntimeError("Synthetic migration interruption")
                    return result

                with patch.object(op, "create_table", side_effect=fail_after_ddl):
                    with self.assertRaisesRegex(RuntimeError, "Synthetic migration"):
                        Core(config, upgrade=True)
                self.assertEqual(wal.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0002_identity")
                self.assertNotIn("kind", {row[1] for row in wal.execute("PRAGMA table_info(user_sessions)")})
                self.assertIsNone(wal.execute("SELECT name FROM sqlite_master WHERE name='device_authorizations'").fetchone())
            with self.assertRaisesRegex(CoreError, "schema is not current"):
                Core(config)
            saved = list(config.data_dir.glob("pre-upgrade-*.db"))
            self.assertEqual(len(saved), 1)
            recovered = replace(config, data_dir=root / "recovered")
            recovered.data_dir.mkdir()
            copy_database(saved[0], recovered.database)
            with closing(sqlite3.connect(recovered.database)) as connection:
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "0002_identity")
                self.assertEqual(connection.execute("SELECT label FROM accounts").fetchone()[0], "Committed WAL")
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # After removing the injected failure, the recovered old-schema
            # database upgrades successfully and its original key still works.
            with Core(recovered, upgrade=True) as core:
                self.assertEqual(core.repository.verify()["credentials_decryptable"], 1)
                self.assertEqual(core.repository.authorized(owner["workspace_id"], row.ref.account_id).secrets,
                                 Secrets("synthetic-cookie", "synthetic-at", "synthetic-rt"))
            with Core(config, upgrade=True) as core:
                self.assertEqual(core.repository.verify()["schema"], "0004_retention")


class ServerRollbackTest(IdentityFixture, unittest.TestCase):
    def test_update_backup_restore_discards_later_edits_and_revokes_old_login(self):
        account = self.account()
        login = self.sign_in()
        destination = self.root / "before-update"
        backup(self.core, destination)
        self.repo.edit(self.actor, self.first, account.ref.account_id, label="After update")
        self.core.close()
        recovered = replace(self.config, data_dir=self.root / "rollback")
        self.assertTrue(restore(recovered, destination)["sessions_revoked"])
        with Core(recovered) as core:
            self.assertEqual(core.accounts.list(self.actor, self.first)[0]["label"], account.label)
            self.assertEqual(core.repository.verify()["credentials_decryptable"], 1)
            with self.assertRaises(Unauthenticated):
                core.identity.authenticate(login.token)
