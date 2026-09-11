from __future__ import annotations

from dataclasses import replace
import asyncio
import re
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from sqlalchemy import select

from cursor_dashboard.api.app import create_app
from cursor_dashboard.api import models as dto
from cursor_dashboard.domain.core import CoreError, Locked, SecretError, Unauthenticated
from cursor_dashboard.infrastructure.secrets import FileKeyProvider
from cursor_dashboard.infrastructure.persistence.models import Credential, SwitchTicket, User, UserSession
from cursor_dashboard.runtime.backup import backup, restore
from cursor_dashboard.runtime.core import Core
from cursor_dashboard.runtime.remote import RemoteClient
from test_core import data
from test_identity import IdentityFixture
from test_v2_api import APIClient


class ManualWebTest(IdentityFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.core.config = replace(self.config, mode='server')
        self.app = create_app(self.core, public_origin='https://panel.example.test')
        self.client = APIClient(self.app)
        self.row = self.account(snapshot=data(42))
        self.path = f'/api/v1/workspaces/{self.first}/accounts/{self.row.ref.account_id}'

    async def issue(self, client=None):
        return await (client or self.client).request('POST', self.path + '/manual-switch')

    async def consume(self, token, client=None, **fields):
        return await (client or self.client).request('POST', '/api/v1/manual-switch/consume',
            {'token': token, 'platform': 'macos', **fields})

    async def command(self, client=None, platform='macos'):
        status, result, _ = await (client or self.client).request('POST', self.path + '/switch-command',
                                                                {'platform': platform})
        self.assertEqual(status, 200)
        dto.SwitchCommand.model_validate(result)
        self.assertLess(len(result['command']), 400)
        self.assertNotIn('base64', result['command'].lower())
        self.assertNotIn('script', set(result))
        return re.search(r'https://[^\s\'\")]+', result['command']).group(), result

    async def download(self, url, **headers):
        return await APIClient(self.app).request('GET', url,
            headers={'origin': None, 'cookie': None, 'x-csrf-token': None, **headers})

    async def test_short_command_downloads_once_without_browser_credentials(self):
        self.assertEqual((await self.client.request('POST', self.path + '/switch-command', {'platform': 'macos'}))[0], 401)
        await self.client.login()
        self.assertEqual((await self.client.request('POST', self.path + '/switch-command', {'platform': 'macos'},
                                                   headers={'x-csrf-token': None}))[0], 403)
        for platform in ('macos', 'windows'):
            url, result = await self.command(platform=platform)
            with self.core.db.transaction() as session:
                self.assertIsNone(session.scalar(select(SwitchTicket).where(SwitchTicket.consumed_at.is_(None))).consumed_at)
            status, script, headers = await self.download(url)
            self.assertEqual(status, 200)
            self.assertIn('VACUUM INTO', script)
            self.assertNotIn('__SESSION_JSON__', script)
            self.assertTrue(headers['content-type'].startswith('text/plain'))
            self.assertIn('attachment;', headers['content-disposition'])
            self.assertEqual(headers['cache-control'], 'no-store')
            self.assertEqual(headers['referrer-policy'], 'no-referrer')
            self.assertEqual((await self.download(url))[0], 404)
            other = 'windows' if platform == 'macos' else 'macos'
            self.assertEqual((await self.download(url.rsplit('/', 1)[0] + '/' + other))[0], 404)

    async def test_download_is_atomic_and_failed_render_or_cross_origin_does_not_consume(self):
        await self.client.login()
        url, _ = await self.command()
        self.assertEqual((await self.download(url, origin='https://evil.test'))[0], 403)
        self.assertEqual((await self.download(url.rsplit('/', 1)[0] + '/linux'))[0], 422)
        self.assertEqual((await APIClient(self.app).request('HEAD', url))[0], 405)
        with patch('cursor_dashboard.api.app.render_script', side_effect=CoreError('Cannot render script')):
            self.assertEqual((await self.download(url))[0], 409)
        results = await asyncio.gather(*(self.download(url) for _ in range(4)))
        self.assertEqual(sorted(item[0] for item in results), [200, 404, 404, 404])

    async def test_download_rechecks_expiry_session_grant_and_credential_version(self):
        member = self.join()
        self.spaces.set_grant(self.actor, self.first, self.row.ref.account_id, member.user_id, 'use')
        member_client = APIClient(self.app)
        await member_client.login('member@example.test')
        url, _ = await self.command(member_client)
        self.spaces.set_grant(self.actor, self.first, self.row.ref.account_id, member.user_id, None)
        self.assertIn((await self.download(url))[0], {403, 404})
        await self.client.login()
        url, _ = await self.command()
        with self.core.db.transaction(write=True) as session:
            session.get(Credential, (self.first, self.row.ref.account_id)).version += 1
        self.assertEqual((await self.download(url))[0], 404)
        with self.core.db.transaction(write=True) as session:
            session.get(Credential, (self.first, self.row.ref.account_id)).version -= 1
        url, command = await self.command()
        with patch('cursor_dashboard.application.switching.time.time', return_value=command['expires_at']):
            self.assertEqual((await self.download(url))[0], 404)
        url, _ = await self.command()
        await self.client.request('POST', '/api/v1/auth/logout')
        self.assertIn((await self.download(url))[0], {401, 404})

    async def test_download_rejects_device_tickets_and_disabled_users(self):
        await self.client.login()
        url, _ = await self.command()
        actor = self.identity.authenticate(self.client.cookies['__Host-cursor_session'])
        with self.core.db.transaction(write=True) as session:
            session.get(UserSession, actor.session_id).kind = 'device'
        self.assertEqual((await self.download(url))[0], 404)
        with self.core.db.transaction(write=True) as session:
            session.get(UserSession, actor.session_id).kind = 'web'
            session.get(User, actor.user_id).active = False
        self.assertIn((await self.download(url))[0], {401, 403, 404})

    async def test_script_delivery_is_authorized_single_use_and_not_a_raw_credential_route(self):
        self.assertEqual((await self.issue())[0], 401)
        await self.client.login()
        status, ticket, _ = await self.issue()
        self.assertEqual(status, 200)
        status, result, headers = await self.consume(ticket['token'])
        self.assertEqual(status, 200)
        dto.ManualScript.model_validate(result)
        self.assertEqual(set(result), {'script', 'command', 'platform', 'expires_at'})
        self.assertTrue('VACUUM INTO' in result['script'])
        self.assertLessEqual(result['expires_at'], ticket['expires_at'])
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['content-security-policy'])
        self.assertEqual((await self.consume(ticket['token']))[0], 404)
        self.assertEqual((await self.client.request('GET', '/api/v1/manual-switch/consume'))[0], 405)
        self.assertEqual((await self.client.request('GET', self.path + '/credentials'))[0], 404)
        with self.assertRaises(Exception):
            dto.AccountView.model_validate(result)

    async def test_member_view_use_and_revocation_between_ticket_and_delivery(self):
        member = self.join()
        await self.client.login()
        member_client = APIClient(self.app)
        await member_client.login('member@example.test')
        self.assertEqual((await self.issue(member_client))[0], 404)
        self.spaces.set_grant(self.actor, self.first, self.row.ref.account_id, member.user_id, 'view')
        self.assertEqual((await self.issue(member_client))[0], 403)
        self.spaces.set_grant(self.actor, self.first, self.row.ref.account_id, member.user_id, 'use')
        status, ticket, _ = await self.issue(member_client)
        self.assertEqual(status, 200)
        self.assertEqual((await self.consume(ticket['token']))[0], 404)
        self.spaces.set_grant(self.actor, self.first, self.row.ref.account_id, member.user_id, None)
        self.assertIn((await self.consume(ticket['token'], member_client))[0], {403, 404})

    async def test_invalid_platform_csrf_and_render_failure_do_not_consume_ticket(self):
        await self.client.login()
        _, ticket, _ = await self.issue()
        self.assertEqual((await self.consume(ticket['token'], platform='linux'))[0], 422)
        self.assertEqual((await self.client.request('POST', '/api/v1/manual-switch/consume',
            {'token': ticket['token'], 'platform': 'windows'}, headers={'x-csrf-token': None}))[0], 403)
        with patch('cursor_dashboard.api.app.render_script', side_effect=CoreError('Cannot render script')):
            self.assertEqual((await self.consume(ticket['token']))[0], 409)
        self.assertEqual((await self.consume(ticket['token'], platform='windows'))[0], 200)

    async def test_slow_issuance_rechecks_session_and_schema_has_typed_responses(self):
        await self.client.login()
        async def revoke(*args):
            actor = self.identity.authenticate(self.client.cookies['__Host-cursor_session'])
            self.identity.revoke_session(actor, actor.session_id)
        with patch.object(self.core.credentials, 'ensure', side_effect=revoke):
            self.assertEqual((await self.issue())[0], 401)
        schema = self.app.openapi()
        for path, operations in schema['paths'].items():
            for operation in operations.values():
                for status, response in operation['responses'].items():
                    if status.startswith('2') and status != '204':
                        self.assertTrue(all(content['schema'] for content in response['content'].values()), path)
        self.assertEqual((await self.client.request('GET', '/api/v1/health'))[1]['status'], 'ok')


class BackupTest(IdentityFixture, unittest.TestCase):
    def test_consistent_backup_restores_accounts_grants_and_revokes_sessions(self):
        row = self.account(snapshot=data(42))
        member = self.join()
        self.spaces.set_grant(self.actor, self.first, row.ref.account_id, member.user_id, 'use')
        logged = self.sign_in()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup(self.core, root / 'backup')
            config = replace(self.config, data_dir=root / 'restored')
            result = restore(config, root / 'backup')
            self.assertEqual(result['accounts'], 1)
            with Core(config) as recovered:
                self.assertEqual(recovered.repository.verify()['accounts'], 1)
                self.assertEqual(recovered.accounts.list(member, self.first)[0]['id'], row.ref.account_id)
                with self.assertRaises(Unauthenticated):
                    recovered.identity.authenticate(logged.token)
            self.assertEqual(self.core.repository.authorized(self.first, row.ref.account_id).secrets, row.secrets)
            with self.assertRaises(CoreError):
                restore(config, root / 'backup')

    def test_wrong_key_and_busy_restore_do_not_change_target(self):
        self.account(snapshot=data(42))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup(self.core, root / 'backup')
            bad_key = root / 'wrong.json'
            FileKeyProvider.create(bad_key)
            config = replace(self.config, data_dir=root / 'restored', key_file=bad_key)
            with self.assertRaises(SecretError):
                restore(config, root / 'backup')
            self.assertFalse(config.database.exists())
            with self.assertRaises(Locked):
                restore(self.config, root / 'backup')
            with self.assertRaises(FileExistsError):
                backup(self.core, root / 'backup')


class RemoteBoundaryTest(unittest.TestCase):
    def test_rejects_insecure_external_origins_paths_and_credentials(self):
        for origin in ('http://example.test', 'https://user:pass@example.test', 'https://example.test/api',
                       'https://example.test?token=bad', 'https://example.test#bad'):
            with self.assertRaises(CoreError):
                RemoteClient(origin)

    def test_version_and_redirect_are_rejected_and_logout_clears_session(self):
        client = RemoteClient('https://example.test')
        with patch.object(client, 'request', return_value={'api_version': 2, 'mode': 'server'}):
            with self.assertRaises(CoreError):
                client.login('test@example.test', 'synthetic-password')
        client.logged_in = True
        client.http.cookies.set('test', 'synthetic-session')
        with patch.object(client, 'request', side_effect=CoreError('unavailable')):
            with self.assertRaises(CoreError):
                client.close()
        self.assertFalse(client.http.cookies)
        self.assertFalse(client.logged_in)
        client = RemoteClient('https://example.test')
        with patch.object(client.http, 'request') as request:
            request.return_value.status_code = 302
            with self.assertRaises(CoreError):
                client.request('GET', '/me')
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
        client.close()
