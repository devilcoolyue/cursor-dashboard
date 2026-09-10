from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sqlalchemy import select

from cursor_dashboard.api.app import create_app
from cursor_dashboard.application.switching import SwitchDelivery
from cursor_dashboard.domain.core import Conflict, CoreError, Locked, SecretError, Secrets, Unauthenticated
from cursor_dashboard.infrastructure.persistence.models import Credential
from cursor_dashboard.local.api import create_local_app
from cursor_dashboard.local.archive import export_archive, import_archive, read_archive, seal
from cursor_dashboard.local.keys import DesktopKeys
from cursor_dashboard.local.runtime import DesktopRuntime
from cursor_dashboard.local.switching import SwitchExecutor, validate_delivery
from cursor_dashboard.runtime.lock import RuntimeLock
from test_v2_api import APIClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop/sidecar'))
from desktop_fixture import FixtureInstallation, PreviewGateway, seed, token


class MemoryStore:
    def __init__(self):
        self.keys = None
        self.unavailable = False
        self.saves = 0

    def read(self):
        if self.unavailable:
            raise SecretError('Store is locked')
        return self.keys

    def save(self, keys):
        if self.unavailable:
            raise SecretError('Store is locked')
        self.keys = keys
        self.saves += 1


class DesktopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cursor-p4-test-')
        self.directory = Path(self.temporary.name)
        self.store = MemoryStore()
        self.installation = FixtureInstallation(self.directory)
        self.runtime = self.open()
        self.addAsyncCleanup(self.cleanup)
        await seed(self.runtime)
        self.core = self.runtime.core
        self.actor = self.runtime.identity.actor()
        self.workspace = self.core.identity.me(self.actor)['workspaces'][0]['id']
        self.accounts = self.core.accounts.list(self.actor, self.workspace)
        self.client = APIClient(create_local_app(self.runtime, 'a' * 64, 19441))

    def open(self, directory=None, store=None):
        return DesktopRuntime(directory or self.directory, store=store or self.store,
            gateway=PreviewGateway(), installation=self.installation)

    async def cleanup(self):
        await self.runtime.shutdown()
        self.temporary.cleanup()

    async def request(self, method, path, body=None, headers=None):
        return await self.client.request(method, path, body, headers={
            'host': '127.0.0.1:19441', 'origin': None, 'authorization': 'Bearer ' + 'a' * 64, **(headers or {})})

    async def test_local_identity_persists_and_old_private_session_is_revoked(self):
        previous_token = self.runtime.identity.login.token
        key = self.store.keys.document
        await self.runtime.shutdown()
        self.runtime = self.open()
        self.assertEqual(self.runtime.identity.actor().user_id, self.actor.user_id)
        self.assertEqual(self.store.keys.document, key)
        self.assertEqual(self.store.saves, 1)
        with self.assertRaises(Unauthenticated):
            self.runtime.core.identity.authenticate(previous_token)
        self.assertEqual(len(self.runtime.core.accounts.list(self.runtime.identity.actor(), self.workspace)), 2)

    async def test_server_cannot_adopt_local_identity(self):
        with self.assertRaises(CoreError):
            create_app(self.core, public_origin='http://127.0.0.1')
        self.core.config = replace(self.core.config, mode='server')
        with self.assertRaises(CoreError):
            create_app(self.core, public_origin='http://127.0.0.1', _local=self.runtime)
        self.core.config = replace(self.core.config, mode='local')

    async def test_private_channel_rejects_browser_wrong_host_and_missing_key(self):
        for headers, expected in (({'authorization': None}, 401), ({'authorization': 'Bearer bad'}, 401),
            ({'origin': 'http://127.0.0.1:19441'}, 403), ({'sec-fetch-mode': 'cors'}, 403), ({'host': 'evil.test'}, 403)):
            self.assertEqual((await self.request('GET', '/api/v1/bootstrap', headers=headers))[0], expected)
        status, result, headers = await self.request('GET', '/api/v1/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(result['mode'], 'local')
        self.assertFalse(result['capabilities']['manual_switch'])
        self.assertEqual(headers['cache-control'], 'no-store')

    async def test_local_route_allowlist_and_no_credential_response(self):
        for method, path in (('POST', '/api/v1/auth/login'), ('POST', '/api/v1/workspaces'),
            ('GET', '/api/v1/instance/users'), ('POST', '/api/v1/manual-switch/consume'), ('GET', '/')):
            self.assertEqual((await self.request(method, path))[0], 404)
        status, result, _ = await self.request('GET', f'/api/v1/workspaces/{self.workspace}/accounts')
        self.assertEqual(status, 200)
        self.assertEqual(result['total'], 2)
        rendered = json.dumps(result)
        for secret in ('fixture-cookie', 'access_token', 'refresh_token', 'preview-user_desktop'):
            self.assertNotIn(secret, rendered)

    async def test_single_directory_lock_and_missing_key_never_regenerates(self):
        with self.assertRaises(Locked):
            self.open()
        await self.runtime.shutdown()
        self.store.keys = None
        self.runtime = self.open()
        self.assertEqual(self.runtime.phase, 'locked')
        self.assertEqual(self.store.saves, 1)
        self.assertIsNone(self.runtime.core)

    async def test_key_store_failure_locks_without_creating_database(self):
        store = MemoryStore()
        store.unavailable = True
        runtime = self.open(self.directory / 'new', store)
        try:
            self.assertEqual(runtime.phase, 'locked')
            self.assertFalse(runtime.config.database.exists())
            self.assertEqual(store.saves, 0)
        finally:
            await runtime.shutdown()

    async def test_archive_roundtrip_reencrypts_into_new_identity_and_preserves_snapshot_time(self):
        archive = self.directory / 'portable.cursorarchive'
        export_archive(self.core, self.actor, self.workspace, archive, 'archive password 42', self.runtime.keys)
        raw = archive.read_bytes()
        self.assertNotIn(b'fixture-cookie', raw)
        self.assertNotIn(b'example.test', raw)
        destination = self.open(self.directory / 'destination', MemoryStore())
        try:
            actor = destination.identity.actor()
            workspace = destination.core.identity.me(actor)['workspaces'][0]['id']
            result = import_archive(destination.core, actor, workspace, archive, 'archive password 42')
            self.assertEqual(result['count'], 2)
            rows = destination.core.accounts.list(actor, workspace)
            self.assertEqual({row['ok_at'] for row in rows}, {row['ok_at'] for row in self.accounts})
            self.assertTrue(set(row['id'] for row in rows).isdisjoint(row['id'] for row in self.accounts))
            self.assertNotEqual(destination.keys.active_id, self.runtime.keys.active_id)
            with destination.core.db.transaction() as session:
                self.assertEqual(set(session.scalars(select(Credential.key_id))), {destination.keys.active_id})
            self.assertEqual(destination.core.repository.verify()['accounts'], 2)
            with self.assertRaises(Conflict):
                import_archive(destination.core, actor, workspace, archive, 'archive password 42')
        finally:
            await destination.shutdown()

    async def test_archive_wrong_password_tampering_and_overwrite_rejected(self):
        archive = self.directory / 'portable.cursorarchive'
        export_archive(self.core, self.actor, self.workspace, archive, 'archive password 42', self.runtime.keys)
        with self.assertRaises(SecretError):
            read_archive(archive, 'wrong password 42')
        with self.assertRaises(Conflict):
            export_archive(self.core, self.actor, self.workspace, archive, 'archive password 42', self.runtime.keys)
        data = archive.read_bytes()
        archive.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(SecretError):
            read_archive(archive, 'archive password 42')

    async def test_original_key_recovery_validates_database_before_persisting(self):
        archive = self.directory / 'recovery.cursorarchive'
        export_archive(self.core, self.actor, self.workspace, archive, 'archive password 42', self.runtime.keys)
        saved = self.runtime.keys.document
        await self.runtime.shutdown()
        self.store.keys = DesktopKeys.generate()
        self.runtime = self.open()
        self.assertEqual(self.runtime.phase, 'locked')
        self.assertEqual(self.runtime.recover(archive, 'archive password 42')['phase'], 'ready')
        self.assertEqual(self.runtime.keys.document, saved)
        self.assertEqual(self.runtime.core.repository.verify()['accounts'], 2)

    async def test_archive_duplicate_failure_rolls_back_every_imported_account(self):
        archive = self.directory / 'duplicate.cursorarchive'
        export_archive(self.core, self.actor, self.workspace, archive, 'archive password 42', self.runtime.keys)
        document = read_archive(archive, 'archive password 42')
        document.accounts.append(document.accounts[0])
        archive.write_bytes(seal(document, 'archive password 42'))
        destination = self.open(self.directory / 'destination', MemoryStore())
        try:
            actor = destination.identity.actor()
            workspace = destination.core.identity.me(actor)['workspaces'][0]['id']
            with self.assertRaises(Conflict):
                import_archive(destination.core, actor, workspace, archive, 'archive password 42')
            self.assertEqual(destination.core.accounts.list(actor, workspace), [])
        finally:
            await destination.shutdown()

    async def test_wrong_recovery_key_does_not_replace_the_system_entry(self):
        await self.runtime.shutdown()
        self.store.keys = None
        self.runtime = self.open()
        self.assertEqual(self.runtime.open(DesktopKeys.generate())['phase'], 'locked')
        self.assertIsNone(self.store.keys)
        self.assertEqual(self.store.saves, 1)

    async def test_offline_refresh_preserves_last_snapshot_and_error_state(self):
        before = self.accounts[0]
        with patch.object(self.core.credentials, 'ensure', side_effect=OSError('fixture offline')):
            result = await self.core.accounts.refresh(self.actor, self.workspace, before['id'])
        self.assertEqual(result['data'], before['data'])
        self.assertEqual(result['ok_at'], before['ok_at'])
        self.assertTrue(result['stale'])

    async def test_native_switch_is_explicit_async_and_keeps_secrets_in_executor(self):
        fields = {'workspace_id': self.workspace, 'account_id': self.accounts[0]['id'], 'confirmed': False}
        self.assertEqual((await self.request('POST', '/native/switch', fields))[0], 409)
        fields['confirmed'] = True
        status, state, _ = await self.request('POST', '/native/switch', fields)
        self.assertEqual(status, 200)
        if self.runtime.job:
            await self.runtime.job
        result = self.runtime.executor.status()
        self.assertEqual(result['stage'], 'complete')
        self.assertNotIn('token', json.dumps(state))
        with closing(sqlite3.connect(self.installation.database)) as connection:
            subject = connection.execute("SELECT value FROM ItemTable WHERE key='cursorAuth/stripeMembershipAuthId'").fetchone()[0]
            self.assertTrue(subject.startswith('auth0|'))
        self.assertEqual((await self.request('GET', '/native/backups'))[0], 200)

    async def test_background_preferences_and_wake_stagger_persist(self):
        self.runtime.set_background(True)
        self.runtime.next_refresh = 0
        self.runtime.resume()
        self.assertGreater(self.runtime.next_refresh, time.time() + 25)
        await self.runtime.shutdown()
        self.runtime = self.open()
        self.assertTrue(self.runtime.background)


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cursor-p4-sql-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.installation = FixtureInstallation(self.directory)
        self.executor = SwitchExecutor(self.directory, self.installation)
        self.delivery = SwitchDelivery('ticket', 'account', 'space', time.time() + 200,
            Secrets('', token('user_sql'), 'synthetic-refresh'), 'sql@example.test', 'user_sql')

    def rows(self, path=None):
        with closing(sqlite3.connect(path or self.installation.database)) as connection:
            return dict(connection.execute('SELECT key,value FROM ItemTable'))

    def test_wal_backup_auth_updates_preserve_other_keys_and_restore(self):
        with closing(sqlite3.connect(self.installation.database)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO ItemTable VALUES ('wal-only', 'committed')")
            writer.execute("INSERT INTO ItemTable VALUES ('cursorAuth/teamId', 'stale')")
            writer.commit()
            self.executor.execute(self.delivery)
        backup = self.executor.status()['backup_id']
        original = self.rows(self.executor.backup_path(backup))
        self.assertEqual(original['wal-only'], 'committed')
        self.assertEqual(original['cursorAuth/teamId'], 'stale')
        self.assertNotIn('cursorAuth/teamId', self.rows())
        self.assertEqual(self.rows()['fixture'], 'preserved')
        self.executor.execute(restore_id=backup)
        self.assertEqual(self.rows(), original)
        self.assertEqual(len(self.executor.backups()), 2)

    def test_exit_cancel_and_expired_authorization_never_write(self):
        before = self.rows()
        with patch.object(self.installation, 'quit', side_effect=Conflict('Cancelled')):
            with self.assertRaises(Conflict):
                self.executor.execute(self.delivery)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.executor.backups(), [])
        with self.assertRaises(Conflict):
            self.executor.execute(replace(self.delivery, expires_at=time.time() - 1))
        with self.assertRaises(Conflict):
            validate_delivery(replace(self.delivery, subject='another-user'))
        self.assertEqual(self.rows(), before)

    def test_sql_failure_rolls_back_all_auth_and_preserves_backup(self):
        with closing(sqlite3.connect(self.installation.database)) as connection:
            connection.execute("CREATE TRIGGER fixture_abort BEFORE INSERT ON ItemTable WHEN NEW.key='cursorAuth/refreshToken' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            connection.commit()
        before = self.rows()
        with self.assertRaises(Conflict):
            self.executor.execute(self.delivery)
        self.assertEqual(self.rows(), before)
        self.assertEqual(len(self.executor.backups()), 1)
        self.assertFalse(self.executor.status()['written'])

    def test_restart_failure_preserves_recovery_and_durable_status(self):
        with patch.object(self.installation, 'restart', side_effect=Conflict('Restart failed')):
            with self.assertRaises(Conflict):
                self.executor.execute(self.delivery)
        self.assertTrue(self.executor.status()['written'])
        reloaded = SwitchExecutor(self.directory, self.installation)
        self.assertEqual(reloaded.status()['stage'], 'failed')
        self.assertEqual(len(reloaded.backups()), 1)
        self.executor.update(stage='writing', busy=True)
        reloaded = SwitchExecutor(self.directory, self.installation)
        self.assertEqual(reloaded.status()['stage'], 'interrupted')
        self.assertFalse(reloaded.status()['busy'])

    def test_shared_target_lock_and_backup_path_traversal_rejected(self):
        with RuntimeLock(self.installation.database.parent / '.cursor-panel-switch.lock'):
            with self.assertRaises(Conflict):
                self.executor.execute(self.delivery)
        for value in ('../core.db', '/tmp/secret', 'not-a-uuid'):
            with self.assertRaises(Conflict):
                self.executor.backup_path(value)
        self.assertEqual(self.executor.backups(), [])

    def test_cursor_reopening_before_write_aborts(self):
        before = self.rows()
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise Conflict('Reopened')
        with patch.object(self.installation, 'ensure_stopped', side_effect=check):
            with self.assertRaises(Conflict):
                self.executor.execute(self.delivery)
        self.assertEqual(self.rows(), before)


if __name__ == '__main__':
    unittest.main()
