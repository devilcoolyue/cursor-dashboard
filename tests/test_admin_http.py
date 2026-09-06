from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import unittest
from dataclasses import dataclass
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode, urlsplit

from cursor_dashboard import admin, desktop, server, snapshot, store


@dataclass
class Response:
    status_code: int
    headers: dict[str, str]
    text: str

    def json(self):
        return json.loads(self.text)


def credential(marker: str, expires: int) -> desktop.DesktopSession:
    def token(kind: str, expiry: int) -> str:
        claims = {"sub": "auth0|user_test", "type": "session", "exp": expiry,
                  "marker": marker, "kind": kind}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return f"eyJhbGciOiJIUzI1NiJ9.{encoded}.signature"

    return desktop.DesktopSession(token("access", expires), "auth0|user_test", expires,
                                  token("refresh", expires + 3600), "session")


class AdminHTTPTest(unittest.IsolatedAsyncioTestCase):
    password = "test password strong 3bF!"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for target, name, value in (
            (store, "DATABASE_PATH", self.root / "accounts.db"),
            (store, "LEGACY_ACCOUNTS_PATH", self.root / "missing.json"),
            (server, "PANEL_TOKEN", ""),
            (server, "_scheduler", None),
        ):
            handle = patch.object(target, name, value)
            handle.start()
            self.addCleanup(handle.stop)
        store._initialized.clear()
        snapshot._snapshots.clear()
        snapshot._inflight.clear()
        self.addCleanup(store._initialized.clear)
        self.addCleanup(snapshot._snapshots.clear)
        self.addCleanup(snapshot._inflight.clear)
        self.cookies: dict[str, str] = {}
        self.csrf = ""
        self.expiry = int(time.time()) + 7200
        self.accounts = [
            store.upsert_account("private-cookie-alpha", "alpha@example.test", "Alpha", "Research",
                                 session=credential("alpha", self.expiry)),
            store.upsert_account("private-cookie-beta", "beta@example.test", "Beta", "Operations",
                                 session=credential("beta", self.expiry)),
            store.upsert_account("private-cookie-special", "special@example.test", "100%_owner", "Research",
                                 session=credential("special", self.expiry)),
            store.upsert_account("private-cookie-legacy", "legacy@example.test", "Legacy", ""),
        ]
        for account in self.accounts:
            snapshot.record_success(store.account_id(account), account["cookie"], {
                "email": account["email"], "quota": {"overall": {"remaining_pct": 50}},
            })
        admin.initialize_admin(self.password)
        outbound = patch.object(server, "fetch_cursor", new_callable=AsyncMock)
        self.outbound = outbound.start()
        self.outbound.side_effect = AssertionError("HTTP tests must not call Cursor")
        self.addCleanup(outbound.stop)

    async def request(self, method: str, url: str, *, data=None, headers=None,
                      authenticated: bool = True) -> Response:
        parsed = urlsplit(url)
        payload = json.dumps(data).encode() if data is not None else b""
        request_headers = {"host": "testserver", **(headers or {})}
        if data is not None:
            request_headers["content-type"] = "application/json"
        if authenticated and self.cookies:
            request_headers["cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        sent_request = False
        completed = asyncio.Event()
        messages = []

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await completed.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": parsed.path, "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(), "root_path": "",
            "headers": [(key.lower().encode(), value.encode()) for key, value in request_headers.items()],
            "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
        }
        await asyncio.wait_for(server.app(scope, receive, send), timeout=5)
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
        if authenticated:
            for key, value in start["headers"]:
                if key == b"set-cookie":
                    parsed_cookie = SimpleCookie()
                    parsed_cookie.load(value.decode())
                    for name, morsel in parsed_cookie.items():
                        if morsel["max-age"] == "0":
                            self.cookies.pop(name, None)
                        else:
                            self.cookies[name] = morsel.value
        body = b"".join(message.get("body", b"") for message in messages
                        if message["type"] == "http.response.body")
        return Response(start["status"], response_headers, body.decode())

    async def login(self):
        response = await self.request("POST", "/api/admin/login", data={"password": self.password})
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()["csrf_token"]
        return response

    async def save_policy(self, *, all_accounts=False, departments=None, account_ids=None):
        return await self.request("PUT", "/api/admin/switch-policy", headers={"X-Admin-CSRF": self.csrf},
                                  data={"all_accounts": all_accounts, "departments": departments or [],
                                        "account_ids": account_ids or []})

    def assert_no_credentials(self, response: Response):
        for account in self.accounts:
            for key in ("cookie", "access_token", "refresh_token"):
                if account[key]:
                    self.assertNotIn(account[key], response.text)

    async def test_login_cookie_logout_and_replay(self):
        shell = await self.request("GET", "/admin")
        self.assertEqual(shell.status_code, 200)
        self.assert_no_credentials(shell)
        self.assertNotIn(self.accounts[0]["email"], shell.text)
        response = await self.request("GET", "/api/admin/session")
        self.assertFalse(response.json()["authenticated"])
        rejected = await self.request("POST", "/api/admin/login", data={"password": "wrong"})
        self.assertEqual(rejected.status_code, 401)
        self.assertNotIn("cursor_panel_admin", self.cookies)
        accepted = await self.login()
        cookie = accepted.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertGreater(accepted.json()["expires_at"], time.time())
        saved_cookie = self.cookies["cursor_panel_admin"]
        self.assertNotIn(saved_cookie, accepted.text)
        rejected_logout = await self.request("POST", "/api/admin/logout")
        self.assertEqual(rejected_logout.status_code, 403)
        response = await self.request("POST", "/api/admin/logout", headers={"X-Admin-CSRF": self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cursor_panel_admin", self.cookies)
        replay = await self.request("GET", "/api/admin/accounts", authenticated=False,
                                    headers={"cookie": f"cursor_panel_admin={saved_cookie}"})
        self.assertEqual(replay.status_code, 401)

    async def test_login_throttling_and_validation_errors_do_not_echo_secrets(self):
        invalid_password = {"secret": "password-must-not-appear-in-errors"}
        rejected = await self.request("POST", "/api/admin/login", data={"password": invalid_password})
        self.assertEqual(rejected.status_code, 422)
        self.assertNotIn(invalid_password["secret"], rejected.text)
        for _ in range(admin.CLIENT_LOGIN_LIMIT):
            failed = await self.request("POST", "/api/admin/login", data={"password": "wrong"})
            self.assertEqual(failed.status_code, 401)
        throttled = await self.request("POST", "/api/admin/login", data={"password": self.password})
        self.assertEqual(throttled.status_code, 429)
        self.assertGreater(int(throttled.headers["retry-after"]), 0)
        self.assertNotIn(self.password, throttled.text)
        self.assertNotIn("cursor_panel_admin", self.cookies)

    async def test_admin_boundaries_and_panel_token_are_independent(self):
        with patch.object(server, "PANEL_TOKEN", "shared-panel-token"):
            for path in ("/api/admin/accounts", "/api/admin/switch-policy"):
                response = await self.request("GET", path, headers={"X-Panel-Token": "shared-panel-token"})
                self.assertEqual(response.status_code, 401, path)
            await self.login()
            response = await self.request("GET", "/api/admin/accounts")
            self.assertEqual(response.status_code, 200)
            response = await self.request("GET", "/api/accounts")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(account["can_switch"] for account in response.json()["accounts"]))

    async def test_expired_admin_session_loses_access_and_switch_privileges(self):
        await self.login()
        conn = store._connect()
        try:
            conn.execute("UPDATE admin_sessions SET expires_at = 1")
            conn.commit()
        finally:
            conn.close()
        session = await self.request("GET", "/api/admin/session")
        self.assertFalse(session.json()["authenticated"])
        protected = await self.request("GET", "/api/admin/accounts")
        self.assertEqual(protected.status_code, 401)
        public = await self.request("GET", "/api/accounts")
        self.assertFalse(any(account["can_switch"] for account in public.json()["accounts"]))
        command = await self.request("POST", "/api/accounts/alpha@example.test/switch-command",
                                     headers={"X-Admin-CSRF": self.csrf})
        self.assertEqual(command.status_code, 403)
        self.outbound.assert_not_called()

    async def test_foreign_origin_and_csrf_cannot_change_policy(self):
        foreign = await self.request("POST", "/api/admin/login", data={"password": self.password},
                                     headers={"Origin": "https://foreign.example"})
        self.assertEqual(foreign.status_code, 403)
        await self.login()
        enabled = {"all_accounts": True, "departments": [], "account_ids": []}
        for headers in ({}, {"X-Admin-CSRF": "incorrect"},
                        {"X-Admin-CSRF": self.csrf, "Origin": "https://foreign.example"},
                        {"X-Admin-CSRF": self.csrf, "Sec-Fetch-Site": "cross-site"}):
            response = await self.request("PUT", "/api/admin/switch-policy", data=enabled, headers=headers)
            self.assertEqual(response.status_code, 403)
        unchanged = await self.request("GET", "/api/admin/switch-policy")
        self.assertFalse(unchanged.json()["policy"]["all_accounts"])
        same_origin = await self.request("PUT", "/api/admin/switch-policy", data=enabled,
                                         headers={"X-Admin-CSRF": self.csrf, "Origin": "http://testserver"})
        self.assertEqual(same_origin.status_code, 200)

    async def test_admin_account_search_pagination_and_safe_metadata(self):
        await self.login()
        first = await self.request("GET", "/api/admin/accounts?page=1&page_size=2")
        self.assertEqual(first.status_code, 200, first.text)
        page = first.json()
        self.assertEqual(page["total"], 4)
        self.assertEqual(page["page"], 1)
        self.assertEqual(len(page["accounts"]), 2)
        first_ids = {account["db_id"] for account in page["accounts"]}
        last = await self.request("GET", "/api/admin/accounts?page=999&page_size=2")
        self.assertEqual(last.json()["page"], 2)
        self.assertEqual(len(last.json()["accounts"]), 2)
        self.assertFalse(first_ids & {account["db_id"] for account in last.json()["accounts"]})
        for query, expected in (("ALPHA", 1), ("beta@example.test", 1), ("Research", 2),
                                ("%_", 1), ("%", 1), ("_", 1), ("' OR 1=1 --", 0)):
            response = await self.request("GET", "/api/admin/accounts?" + urlencode({"q": query}))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], expected, query)
            self.assert_no_credentials(response)
        filtered = await self.request("GET", "/api/admin/accounts?department=Research&q=Alpha")
        row = filtered.json()["accounts"][0]
        self.assertEqual(filtered.json()["total"], 1)
        timestamp = lambda name: datetime.fromisoformat(row["auth"][name]).timestamp()
        self.assertEqual(timestamp("access_expires_at"), self.expiry)
        self.assertEqual(timestamp("refresh_expires_at"), self.expiry + 3600)
        self.assertLess(timestamp("refresh_due_at"), self.expiry)
        self.assertGreater(timestamp("refreshed_at"), 0)
        self.assert_no_credentials(first)
        self.assert_no_credentials(last)
        self.assert_no_credentials(filtered)
        self.assertIn("no-store", first.headers.get("cache-control", ""))

    async def test_admin_pagination_rejects_unbounded_or_invalid_inputs(self):
        await self.login()
        for query in ("page=0", "page=-1", "page_size=0", "page_size=101", "page=oops"):
            response = await self.request("GET", "/api/admin/accounts?" + query)
            self.assertEqual(response.status_code, 422, query)

    async def test_public_views_hide_auth_metadata_and_default_to_no_switch(self):
        for path in ("/api/accounts", "/api/account-index", "/api/accounts/alpha@example.test"):
            response = await self.request("GET", path)
            self.assertEqual(response.status_code, 200)
            self.assert_no_credentials(response)
            payload = response.json()
            accounts = payload["accounts"] if "accounts" in payload else [payload["account"]]
            for account in accounts:
                self.assertFalse(account["can_switch"])
                self.assertNotIn("auth", account)
                self.assertNotIn("token_expires_at", account)

    async def test_department_and_account_grants_are_inclusive_and_revocable(self):
        await self.login()
        saved = await self.save_policy(departments=["Research"], account_ids=[self.accounts[1]["db_id"]])
        self.assertEqual(saved.status_code, 200, saved.text)
        for path in ("/api/accounts", "/api/account-index"):
            response = await self.request("GET", path, authenticated=False)
            permissions = {account["email"]: account["can_switch"] for account in response.json()["accounts"]}
            self.assertEqual(permissions, {"alpha@example.test": True, "beta@example.test": True,
                                          "special@example.test": True, "legacy@example.test": False})
        await self.save_policy(all_accounts=True)
        response = await self.request("GET", "/api/accounts", authenticated=False)
        self.assertTrue(all(account["can_switch"] for account in response.json()["accounts"]))
        await self.save_policy()
        response = await self.request("GET", "/api/accounts", authenticated=False)
        self.assertFalse(any(account["can_switch"] for account in response.json()["accounts"]))
        admin_view = await self.request("GET", "/api/accounts")
        self.assertTrue(all(account["can_switch"] for account in admin_view.json()["accounts"]))

    async def test_unknown_policy_references_do_not_change_existing_grants(self):
        await self.login()
        self.assertEqual((await self.save_policy(departments=["Research"])).status_code, 200)
        for changes in ({"departments": ["missing-department"]}, {"account_ids": [999999]}):
            response = await self.save_policy(**changes)
            self.assertEqual(response.status_code, 400)
        policy = await self.request("GET", "/api/admin/switch-policy")
        self.assertEqual(policy.json()["policy"]["departments"], ["Research"])
        self.assertFalse(policy.json()["policy"]["all_accounts"])

    async def test_denied_switch_stops_before_credentials_or_outbound_work(self):
        with patch.object(server.sessions, "ensure_account", new_callable=AsyncMock) as ensure, \
             patch.object(server, "build_commands") as build, \
             patch.object(server, "take_manual_token") as take:
            response = await self.request("POST", "/api/accounts/alpha@example.test/switch-command")
        self.assertEqual(response.status_code, 403)
        ensure.assert_not_called()
        build.assert_not_called()
        take.assert_not_called()
        self.outbound.assert_not_called()

    async def test_switch_requires_admin_csrf_and_guest_grant(self):
        await self.login()
        account = self.accounts[0]
        fake_result = {"macos": {"command": "preview-macos"}, "windows": {"command": "preview-windows"}}
        with patch.object(server.sessions, "ensure_account", new_callable=AsyncMock, return_value=account), \
             patch.object(server.sessions, "request_account", new_callable=AsyncMock,
                          return_value={"email": account["email"], "authId": account["auth_subject"]}), \
             patch.object(server, "build_commands", return_value=fake_result), \
             patch.object(server, "take_manual_token", return_value=True):
            denied = await self.request("POST", "/api/accounts/alpha@example.test/switch-command")
            self.assertEqual(denied.status_code, 403)
            accepted = await self.request("POST", "/api/accounts/alpha@example.test/switch-command",
                                          headers={"X-Admin-CSRF": self.csrf})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertIn("no-store", accepted.headers.get("cache-control", ""))
            await self.save_policy(account_ids=[account["db_id"]])
            guest = await self.request("POST", "/api/accounts/alpha@example.test/switch-command", authenticated=False)
            self.assertEqual(guest.status_code, 200)
            foreign = await self.request("POST", "/api/accounts/alpha@example.test/switch-command", authenticated=False,
                                         headers={"Origin": "https://foreign.example"})
            self.assertEqual(foreign.status_code, 403)

    async def test_revocation_during_cursor_request_prevents_command_delivery(self):
        await self.login()
        account = self.accounts[0]
        await self.save_policy(account_ids=[account["db_id"]])

        async def revoke(*_args):
            self.assertEqual((await self.save_policy()).status_code, 200)
            return {"email": account["email"], "authId": account["auth_subject"]}

        with patch.object(server.sessions, "ensure_account", new_callable=AsyncMock, return_value=account), \
             patch.object(server.sessions, "request_account", side_effect=revoke), \
             patch.object(server, "build_commands") as build, \
             patch.object(server, "take_manual_token", return_value=True):
            response = await self.request("POST", "/api/accounts/alpha@example.test/switch-command", authenticated=False)
        self.assertEqual(response.status_code, 403, response.text)
        build.assert_not_called()

    async def test_admin_logout_during_cursor_request_prevents_command_delivery(self):
        account = self.accounts[0]

        async def logout(*_args):
            response = await self.request("POST", "/api/admin/logout", headers={"X-Admin-CSRF": self.csrf})
            self.assertEqual(response.status_code, 200)
            return {"email": account["email"], "authId": account["auth_subject"]}

        for panel_token, public_grant, expected_status in (("", False, 403),
                                                          ("configured-panel-token", True, 401)):
            with self.subTest(panel_token_enabled=bool(panel_token)), \
                 patch.object(server, "PANEL_TOKEN", panel_token), \
                 patch.object(server.sessions, "ensure_account", new_callable=AsyncMock, return_value=account), \
                 patch.object(server.sessions, "request_account", side_effect=logout), \
                 patch.object(server, "build_commands") as build, \
                 patch.object(server, "take_manual_token", return_value=True):
                await self.login()
                self.assertEqual((await self.save_policy(all_accounts=public_grant)).status_code, 200)
                response = await self.request("POST", "/api/accounts/alpha@example.test/switch-command",
                                              headers={"X-Admin-CSRF": self.csrf})
                self.assertEqual(response.status_code, expected_status, response.text)
                build.assert_not_called()

    async def test_guest_department_changes_and_deletion_keep_original_access(self):
        await self.login()
        await self.save_policy(departments=["Research"])
        before = store.get_account("beta@example.test")
        changed = await self.request("PATCH", "/api/accounts/beta@example.test/department",
                                     data={"department": "Research"}, authenticated=False)
        self.assertEqual(changed.status_code, 200)
        saved = store.get_account("beta@example.test")
        self.assertEqual(saved["department"], "Research")
        for key in ("cookie", "access_token", "refresh_token"):
            self.assertEqual(saved[key], before[key])
        row = await self.request("GET", "/api/accounts/beta@example.test", authenticated=False)
        self.assertTrue(row.json()["account"]["can_switch"])

        changed = await self.request("PATCH", "/api/accounts/beta@example.test/department",
                                     data={"department": "Operations"}, authenticated=False)
        self.assertEqual(changed.status_code, 200)
        row = await self.request("GET", "/api/accounts/beta@example.test", authenticated=False)
        self.assertFalse(row.json()["account"]["can_switch"])
        deleted = await self.request("DELETE", "/api/accounts/beta@example.test", authenticated=False)
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(store.get_account("beta@example.test"))
        missing = await self.request("GET", "/api/accounts/beta@example.test", authenticated=False)
        self.assertEqual(missing.status_code, 404)
        self.outbound.assert_not_called()

    async def test_card_mutations_still_require_configured_panel_token(self):
        with patch.object(server, "PANEL_TOKEN", "shared-panel-token"):
            before = store.get_account("beta@example.test")
            for method, path, data in (
                ("PATCH", "/api/accounts/beta@example.test/department", {"department": "Research"}),
                ("DELETE", "/api/accounts/beta@example.test", None),
            ):
                for headers in ({}, {"X-Panel-Token": "wrong"}):
                    rejected = await self.request(method, path, data=data, headers=headers, authenticated=False)
                    self.assertEqual(rejected.status_code, 401)
                    self.assertEqual(store.get_account("beta@example.test"), before)
            changed = await self.request("PATCH", "/api/accounts/beta@example.test/department",
                                         data={"department": "Research"}, authenticated=False,
                                         headers={"X-Panel-Token": "shared-panel-token"})
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(store.get_account("beta@example.test")["department"], "Research")
            deleted = await self.request("DELETE", "/api/accounts/beta@example.test", authenticated=False,
                                         headers={"X-Panel-Token": "shared-panel-token"})
            self.assertEqual(deleted.status_code, 200)
            self.assertIsNone(store.get_account("beta@example.test"))
        self.outbound.assert_not_called()

    async def test_guest_enrollment_honors_selected_department(self):
        await self.login()
        await self.save_policy(departments=["Research"])
        session = credential("reenrolled", self.expiry)
        for email in ("beta@example.test", "new@example.test"):
            data = {
                "desktop_me": {"email": email, "authId": session.subject},
                "desktop_plan": {"planInfo": {"planName": "Free"}},
                "desktop_profile": {"membershipType": "free"},
                "desktop_period": {"planUsage": {"totalSpend": 0}},
                "desktop_grok": {"includedLimitZero": True},
                "desktop_limit": {"noUsageBasedAllowed": True},
            }

            async def fetch(_cookie, _label, name, *_args):
                return data[name]

            with patch.object(server.sessions, "exchange_cookie", new_callable=AsyncMock,
                              return_value=(session, email)), \
                 patch.object(server, "fetch_cursor", side_effect=fetch):
                response = await self.request("POST", "/api/accounts", authenticated=False,
                                              data={"cookie": "submitted-cookie", "department": "Research"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(store.get_account(email)["department"], "Research")
            view = await self.request("GET", "/api/accounts/" + email, authenticated=False)
            self.assertTrue(view.json()["account"]["can_switch"])

    async def test_admin_logout_during_enrollment_rechecks_panel_access(self):
        email = "beta@example.test"
        session = credential("logout-enrollment", self.expiry)
        data = {
            "desktop_me": {"email": email, "authId": session.subject},
            "desktop_plan": {"planInfo": {"planName": "Free"}},
            "desktop_profile": {"membershipType": "free"},
            "desktop_period": {"planUsage": {"totalSpend": 0}},
            "desktop_grok": {"includedLimitZero": True},
            "desktop_limit": {"noUsageBasedAllowed": True},
        }

        async def exchange(*_args):
            response = await self.request("POST", "/api/admin/logout", headers={"X-Admin-CSRF": self.csrf})
            self.assertEqual(response.status_code, 200)
            return session, email

        async def fetch(_cookie, _label, name, *_args):
            return data[name]

        for panel_token, expected_status in (("", 200), ("configured-panel-token", 401)):
            with self.subTest(panel_token_enabled=bool(panel_token)), \
                 patch.object(server, "PANEL_TOKEN", panel_token), \
                 patch.object(server.sessions, "exchange_cookie", side_effect=exchange), \
                 patch.object(server, "fetch_cursor", side_effect=fetch):
                await self.login()
                before = store.get_account(email)
                response = await self.request("POST", "/api/accounts", headers={"X-Admin-CSRF": self.csrf},
                                              data={"cookie": "replacement-cookie-" + panel_token,
                                                    "department": "Research"})
                self.assertEqual(response.status_code, expected_status, response.text)
                saved = store.get_account(email)
                if panel_token:
                    self.assertEqual(saved, before)
                else:
                    self.assertEqual(saved["department"], "Research")


if __name__ == "__main__":
    unittest.main()
