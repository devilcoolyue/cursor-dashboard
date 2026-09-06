from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi import HTTPException

from cursor_dashboard import desktop, server, sessions
from cursor_dashboard.client import AuthExpired, CursorClient, RateLimited


def cookie_for(**claims):
    payload = {"sub": "auth0|user_test", "exp": int(time.time()) + 3600, "type": "web", **claims}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"user_test%3A%3AeyJhbGciOiJIUzI1NiJ9.{encoded}.fake_signature"


def session_response(**claims):
    token = cookie_for(type="session", **claims).partition('%3A%3A')[2]
    return {"accessToken": token, "refreshToken": "distinct-desktop-refresh-token"}


def valid_desktop_session():
    return desktop.desktop_session(session_response(), "auth0|user_test")


class SessionTest(unittest.TestCase):
    def test_login_challenge_uses_pkce_s256(self):
        flow, verifier, challenge = desktop.login_challenge()
        self.assertEqual(challenge, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='))
        self.assertNotEqual(flow, desktop.login_challenge()[0])

    def test_desktop_response_must_be_session_for_selected_user(self):
        web = desktop.parse_session(cookie_for()).token
        for data in ({}, {"accessToken": web, "refreshToken": web},
                     session_response(sub='auth0|user_other'),
                     {**session_response(), "refreshToken": ""}):
            with self.subTest(data_keys=list(data)), self.assertRaises(desktop.DesktopSessionError):
                desktop.desktop_session(data, "auth0|user_test")

    def test_accepts_plain_and_encoded_values(self):
        cookie = cookie_for()
        for value in (cookie, cookie.replace("%3A%3A", "::"), "WorkosCursorSessionToken=" + cookie):
            with self.subTest(value=value[:20]):
                self.assertEqual(desktop.parse_session(value).subject, "auth0|user_test")

    def test_rejects_expired_missing_or_invalid_expiry(self):
        for expiry in (0, time.time() - 10, None, True, "2099", float("nan"), float("inf"), 10**400):
            with self.subTest(expiry=expiry), self.assertRaises(desktop.DesktopSessionError):
                desktop.parse_session(cookie_for(exp=expiry))

    def test_rejects_mismatched_account_and_malformed_tokens(self):
        for cookie in (cookie_for(sub="auth0|user_other"), "opaque-cookie", "user_test::a.e30.c",
                       "user_test::a.not-json.c", cookie_for() + "'; touch /tmp/unexpected"):
            with self.subTest(cookie=cookie[:20]), self.assertRaises(desktop.DesktopSessionError):
                desktop.parse_session(cookie)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.session = valid_desktop_session()
        self.result = desktop.build_commands(self.session, "test@example.test")

    def test_commands_decode_to_inspectable_scripts(self):
        for platform, value in self.result["commands"].items():
            encoded = re.search(r"'([A-Za-z0-9+/=]{100,})'", value["command"]).group(1)
            self.assertEqual(base64.b64decode(encoded).decode(), value["script"])
            self.assertNotIn("__ENGINE__", value["script"])
            self.assertNotIn("__SESSION_JSON__", value["script"])
            self.assertNotIn("__EXPIRES_AT__", value["script"])
            if platform == "macos":
                result = subprocess.run(["/bin/bash", "-n"], input=value["script"], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_text_cannot_escape_the_script_here_document(self):
        email = "bad'\nCURSOR_PANEL_JS\n'@\n$(touch /tmp/unexpected)"
        result = desktop.build_commands(self.session, email)
        for item in result["commands"].values():
            self.assertNotIn(email, item["script"])
            encoded_json = item["script"].split("const session = ", 1)[1].split(";\n", 1)[0]
            self.assertEqual(json.loads(encoded_json)["email"], email)

    def test_preview_commands_exit_before_accessing_cursor(self):
        commands = desktop.build_commands(self.session, "preview@example.test", preview=True)["commands"]
        self.assertTrue(commands["macos"]["script"].startswith("exit 1"))
        self.assertTrue(commands["windows"]["script"].startswith("throw 'Preview only'"))

    def test_web_cookie_can_never_be_written_as_desktop_credentials(self):
        web = desktop.parse_session(cookie_for())
        with self.assertRaises(desktop.DesktopSessionError):
            desktop.build_commands(web, 'test@example.test')


class DesktopClientTest(unittest.TestCase):
    def test_callback_accepts_plain_success_without_sending_verifier(self):
        client = CursorClient('web-cookie')
        self.addCleanup(client.s.close)
        response = requests.Response()
        response.status_code = 200
        response._content = b'OK'
        with patch.object(client.s, 'request', return_value=response) as request:
            client.desktop_callback('flow', 'challenge')
        self.assertEqual(request.call_args.kwargs['json'], {'uuid': 'flow', 'challenge': 'challenge'})
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_poll_and_profile_target_desktop_backend(self):
        client = CursorClient('web-cookie')
        self.addCleanup(client.s.close)
        with patch.object(client, '_call', return_value={}) as call:
            client.desktop_poll('flow', 'verifier')
            self.assertEqual(call.call_args.kwargs['base'], 'https://api2.cursor.sh')
            self.assertEqual(call.call_args.kwargs['params'], {'uuid': 'flow', 'verifier': 'verifier'})
            client.desktop_profile('desktop-token')
            self.assertEqual(call.call_args.kwargs['headers']['Authorization'], 'Bearer desktop-token')


class SwitchEndpointTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        session = valid_desktop_session()
        self.account = {"cookie": cookie_for(), "label": "Test", "email": "test@example.test",
                        "access_token": session.token, "refresh_token": session.refresh_token,
                        "auth_subject": session.subject, "token_expires_at": session.expires_at}
        patcher = patch.object(server, "find_account", return_value=self.account)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(server, "take_manual_token", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(server, "require_switch")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(sessions, "ensure_account", return_value=self.account)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_checks_selected_account_and_disables_caching(self):
        with patch.object(server, "fetch_cursor", return_value={"email": self.account["email"], "authId": "auth0|user_test"}) as fetch:
            response = await server.api_switch_command("test@example.test")
        fetch.assert_awaited_once_with('', 'Test', 'desktop_me', self.account['access_token'])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(set(json.loads(response.body)["commands"]), {"macos", "windows"})

    async def test_pending_desktop_login_is_bounded(self):
        response = requests.Response()
        response.status_code = 404
        pending = requests.HTTPError(response=response)
        responses = [{'email': self.account['email'], 'sub':'auth0|user_test'}, {}] + [pending] * 5
        with patch.object(server, 'fetch_cursor', side_effect=responses) as fetch, \
             patch.object(server.asyncio, 'sleep'), self.assertRaises(desktop.DesktopSessionError):
            await sessions.exchange_cookie(self.account['cookie'], 'Test', fetch)
        self.assertEqual(fetch.await_count, 7)

    async def test_rejected_or_unknown_sessions_never_generate_commands(self):
        for error, code in ((AuthExpired("expired"), 400), (RateLimited("limited"), 503),
                            (requests.Timeout("timeout"), 502)):
            with self.subTest(error=type(error)), \
                 patch.object(server, "fetch_cursor", side_effect=error), \
                 patch.object(server, "build_commands") as build, \
                 self.assertRaises(HTTPException) as raised:
                await server.api_switch_command("test@example.test")
            self.assertEqual(raised.exception.status_code, code)
            build.assert_not_called()

    async def test_mismatched_or_empty_identity_is_rejected(self):
        for result in ({}, {"email": "other@example.test"}, {"email": self.account["email"], "sub": "user_other"}):
            with patch.object(server, "fetch_cursor", return_value=result), self.assertRaises(HTTPException) as raised:
                await server.api_switch_command("test@example.test")
            self.assertEqual(raised.exception.status_code, 400)

    async def test_expired_web_cookie_does_not_block_desktop_switch(self):
        self.account["cookie"] = cookie_for(exp=1)
        with patch.object(server, "fetch_cursor", return_value={'email':self.account['email'], 'authId':'auth0|user_test'}) as fetch:
            response = await server.api_switch_command("test@example.test")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(call.args[0] == '' for call in fetch.await_args_list))

    def test_endpoint_uses_panel_authentication(self):
        route = next(route for route in server.app.routes if route.path.endswith("/switch-command"))
        self.assertIn(server.require_token, [dep.call for dep in route.dependant.dependencies])
        with patch.object(server, "PANEL_TOKEN", "expected"), self.assertRaises(HTTPException):
            server.require_token("wrong")


@unittest.skipUnless(shutil.which("node"), "Node is required for the local engine integration test")
class DatabaseEngineTest(unittest.TestCase):
    """Execute the real engine against disposable databases using Node's built-in SQLite."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["node", "-e", "require('node:sqlite')"], capture_output=True)
        if result.returncode:
            raise unittest.SkipTest("Node with built-in SQLite is required (22.13+)")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "state.vscdb"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            db.executemany("INSERT INTO ItemTable VALUES (?, ?)", [
                ("cursorAuth/accessToken", "old-token"), ("cursorAuth/cachedEmail", "old@example.test"),
                ("cursorAuth/stripeMembershipType", "old-plan"), ("editor.untouched", "keep-me"),
            ])
        # Only adapt the asynchronous binding; all backup/transaction SQL is real.
        module = self.root / "node_modules" / "@vscode" / "sqlite3"
        module.mkdir(parents=True)
        module.joinpath("index.js").write_text('''
const { DatabaseSync } = require('node:sqlite');
exports.OPEN_READWRITE = 2;
exports.Database = class {
  constructor(file, mode, callback) {
    try { this.db = new DatabaseSync(file); queueMicrotask(() => callback(null)); }
    catch (error) { queueMicrotask(() => callback(error)); }
  }
  configure() {}
  run(sql, values, callback) {
    try { this.db.prepare(sql).run(...values); callback(null); } catch (error) { callback(error); }
  }
  get(sql, callback) {
    try { callback(null, this.db.prepare(sql).get()); } catch (error) { callback(error); }
  }
  close(callback) { this.db.close(); callback(null); }
};
''', encoding="utf-8")

    def run_engine(self, *, expired=False, fail_backup=False, web_token=False):
        session = valid_desktop_session()
        script = desktop.build_commands(session, "new@example.test")["commands"]["macos"]["script"]
        engine = script.split("<<'CURSOR_PANEL_JS'\n", 1)[1].split("\nCURSOR_PANEL_JS", 1)[0]
        if expired:
            engine = engine.replace('"expiresAt":' + str(session.expires_at), '"expiresAt":1')
        if web_token:
            engine = engine.replace(session.token, desktop.parse_session(cookie_for()).token)
        if fail_backup:
            engine = "require('fs').writeFileSync = () => { throw new Error('disk full'); };\n" + engine
        return subprocess.run(["node", "-", str(self.root), str(self.database)], input=engine,
                              text=True, capture_output=True, timeout=15)

    def rows(self, filename=None):
        with sqlite3.connect(filename or self.database) as db:
            return dict(db.execute("SELECT key, value FROM ItemTable"))

    def test_switch_preserves_other_settings_and_backs_up_old_account(self):
        before = self.rows()
        result = self.run_engine()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.rows()
        self.assertEqual(after["editor.untouched"], "keep-me")
        self.assertEqual(after["cursorAuth/cachedEmail"], "new@example.test")
        self.assertNotEqual(after["cursorAuth/accessToken"], "old-token")
        self.assertEqual(after['cursorAuth/refreshToken'], 'distinct-desktop-refresh-token')
        self.assertNotIn("cursorAuth/stripeMembershipType", after)
        backup, = self.root.glob("*.bak")
        self.assertEqual(self.rows(backup), before)
        if os.name != "nt":
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_write_failure_rolls_back_every_login_field(self):
        before = self.rows()
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TRIGGER reject_email BEFORE INSERT ON ItemTable
                WHEN NEW.key = 'cursorAuth/cachedEmail'
                BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        result = self.run_engine()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rows(), before)

    def test_expired_command_does_not_write_or_create_backup(self):
        before = self.rows()
        self.assertNotEqual(self.run_engine(expired=True).returncode, 0)
        self.assertEqual(self.rows(), before)
        self.assertEqual(list(self.root.glob("*.bak")), [])

    def test_backup_failure_leaves_original_account_unchanged(self):
        before = self.rows()
        self.assertNotEqual(self.run_engine(fail_backup=True).returncode, 0)
        self.assertEqual(self.rows(), before)

    def test_web_token_is_rejected_before_opening_database(self):
        before = self.rows()
        result = self.run_engine(web_token=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('desktop session is required', result.stderr)
        self.assertEqual(self.rows(), before)
        self.assertEqual(list(self.root.glob('*.bak')), [])

    def test_backup_includes_committed_wal_contents(self):
        with sqlite3.connect(self.database) as writer:
            writer.execute('PRAGMA journal_mode = WAL')
            writer.execute('INSERT INTO ItemTable VALUES (?, ?)', ('editor.wal', 'latest'))
            writer.commit()
            self.assertTrue(Path(str(self.database) + '-wal').exists())
            result = self.run_engine()
            self.assertEqual(result.returncode, 0, result.stderr)
            backup, = self.root.glob('*.bak')
            self.assertEqual(self.rows(backup)['editor.wal'], 'latest')


if __name__ == "__main__":
    unittest.main()
