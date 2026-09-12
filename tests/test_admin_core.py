from __future__ import annotations

import base64
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cursor_dashboard import admin, store


def jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return "eyJhbGciOiJIUzI1NiJ9." + payload + ".signature"


class AdminCoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(store, "DATABASE_PATH", Path(self.directory.name) / "accounts.db")
        self.legacy = patch.object(store, "LEGACY_ACCOUNTS_PATH", Path(self.directory.name) / "accounts.json")
        self.environment = patch.dict(os.environ, {"ADMIN_PASSWORD": ""})
        self.database.start()
        self.legacy.start()
        self.environment.start()
        store._initialized.clear()

    def tearDown(self):
        store._initialized.clear()
        self.environment.stop()
        self.legacy.stop()
        self.database.stop()
        self.directory.cleanup()

    def test_first_setup_generates_one_password_and_closes_switching(self):
        generated = admin.initialize_admin()
        self.assertGreaterEqual(len(generated), 24)
        self.assertIsNone(admin.initialize_admin())
        self.assertEqual(admin.get_policy(), {"all_accounts": False, "departments": [], "account_ids": []})
        self.assertIsNotNone(admin.check_session(admin.login(generated, "first")["token"]))
        with closing(sqlite3.connect(store.DATABASE_PATH)) as conn, conn:
            stored = conn.execute("SELECT value FROM metadata WHERE key = 'admin_password_scrypt'").fetchone()[0]
        self.assertNotIn(generated, stored)
        self.assertTrue(stored.startswith("scrypt$"))

    def test_password_override_only_invalidates_sessions_when_changed(self):
        admin.initialize_admin("old-password")
        session = admin.login("old-password", "client")
        admin.initialize_admin("old-password")
        self.assertIsNotNone(admin.check_session(session["token"]))
        admin.initialize_admin("new-password")
        self.assertIsNone(admin.check_session(session["token"]))
        with self.assertRaises(admin.InvalidPassword):
            admin.login("old-password", "client")
        self.assertIsNotNone(admin.login("new-password", "client"))

    def test_environment_password_is_persisted(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "configured-password"}):
            self.assertIsNone(admin.initialize_admin())
        self.assertIsNotNone(admin.login("configured-password", "client"))

    def test_sessions_survive_reinitialization_and_use_absolute_expiry(self):
        admin.initialize_admin("password")
        with patch.object(admin.time, "time", return_value=1000):
            session = admin.login("password", "client")
        store._initialized.clear()
        admin.initialize_admin()
        with patch.object(admin.time, "time", return_value=1000 + admin.SESSION_LIFETIME - 1):
            checked = admin.check_session(session["token"])
        self.assertEqual(checked, {"csrf_token": session["csrf_token"], "expires_at": session["expires_at"]})
        with patch.object(admin.time, "time", return_value=1000 + admin.SESSION_LIFETIME):
            self.assertIsNone(admin.check_session(session["token"]))

    def test_logout_and_no_raw_session_tokens_in_database(self):
        admin.initialize_admin("password")
        session = admin.login("password", "client")
        with closing(sqlite3.connect(store.DATABASE_PATH)) as conn, conn:
            record = conn.execute("SELECT token_hash FROM admin_sessions").fetchone()[0]
        self.assertNotEqual(record, session["token"])
        self.assertEqual(len(record), 64)
        admin.delete_session(session["token"])
        self.assertIsNone(admin.check_session(session["token"]))
        for malformed in (None, "", "x" * 200, "x" * 40 + "\N{SNOWMAN}"):
            self.assertIsNone(admin.check_session(malformed))
            admin.delete_session(malformed)

    def test_login_throttles_persist_and_expire(self):
        admin.initialize_admin("password")
        with patch.object(admin.time, "time", return_value=1000):
            for _ in range(admin.CLIENT_LOGIN_LIMIT):
                with self.assertRaises(admin.InvalidPassword):
                    admin.login("wrong", "client")
            store._initialized.clear()
            with self.assertRaises(admin.LoginThrottled) as raised:
                admin.login("password", "client")
            self.assertEqual(raised.exception.retry_after, admin.LOGIN_WINDOW)
            self.assertIsNotNone(admin.login("password", "other-client"))
        with patch.object(admin.time, "time", return_value=1000 + admin.LOGIN_WINDOW):
            self.assertIsNotNone(admin.login("password", "client"))

    def test_global_throttle_covers_distinct_clients(self):
        admin.initialize_admin("password")
        with patch.object(admin, "GLOBAL_LOGIN_LIMIT", 3):
            for index in range(3):
                with self.assertRaises(admin.InvalidPassword):
                    admin.login("wrong", str(index))
            with self.assertRaises(admin.LoginThrottled):
                admin.login("password", "fourth-client")

    def test_concurrent_login_attempts_cannot_bypass_client_limit(self):
        admin.initialize_admin("password")

        def fail(_):
            try:
                admin.login("wrong", "same-client")
            except admin.InvalidPassword:
                return "invalid"
            except admin.LoginThrottled:
                return "throttled"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fail, range(8)))
        self.assertEqual(results.count("invalid"), admin.CLIENT_LOGIN_LIMIT)
        self.assertEqual(results.count("throttled"), 8 - admin.CLIENT_LOGIN_LIMIT)

    def test_policy_combines_scopes_and_persists(self):
        admin.initialize_admin("password")
        account = {"db_id": 12, "department": "Engineering"}
        self.assertFalse(admin.switch_allowed(account, admin.get_policy()))
        self.assertTrue(admin.switch_allowed(account, admin.get_policy(), is_admin=True))
        policy = admin.save_policy({"all_accounts": False, "departments": ["Engineering", "Engineering"],
                                    "account_ids": [30, 30]})
        store._initialized.clear()
        self.assertEqual(admin.get_policy(), policy)
        self.assertTrue(admin.switch_allowed(account, policy))
        self.assertTrue(admin.switch_allowed({"db_id": 30}, policy))
        self.assertFalse(admin.switch_allowed({"db_id": 31, "department": "Finance"}, policy))
        self.assertTrue(admin.switch_allowed({}, {"all_accounts": True}))
        self.assertEqual(policy["account_ids"], [30])

    def test_policy_rejects_coercions_and_corrupt_storage(self):
        admin.initialize_admin("password")
        for change in ({"all_accounts": "false"}, {"account_ids": [True]}, {"departments": "Engineering"},
                       {"account_ids": [-1]}, {"extra": True}):
            policy = {**admin.get_policy(), **change}
            with self.assertRaises(ValueError):
                admin.save_policy(policy)
        with closing(sqlite3.connect(store.DATABASE_PATH)) as conn, conn:
            conn.execute("UPDATE metadata SET value = 'broken' WHERE key = 'admin_switch_policy'")
        with self.assertRaises(store.AccountsError):
            admin.get_policy()

    def test_credential_view_returns_only_safe_metadata(self):
        now = int(time.time())
        expiry = now + 60 * 86400
        account = {"access_token": jwt(expiry), "refresh_token": jwt(expiry + 100),
                   "cookie": "secret-cookie", "token_expires_at": expiry, "auth_refreshed_at": now}
        view = admin.credential_view(account)
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["access_expires_at"], datetime.fromtimestamp(expiry, timezone.utc).isoformat())
        self.assertEqual(view["refresh_expires_at"], datetime.fromtimestamp(expiry + 100, timezone.utc).isoformat())
        self.assertEqual(view["refresh_due_at"], datetime.fromtimestamp(expiry - admin.TOKEN_REFRESH_MARGIN,
                                                                       timezone.utc).isoformat())
        encoded = json.dumps(view)
        for secret in (account["access_token"], account["refresh_token"], account["cookie"]):
            self.assertNotIn(secret, encoded)

    def test_credential_view_handles_expired_and_opaque_tokens(self):
        now = int(time.time())
        account = {"access_token": jwt(now - 10), "refresh_token": "opaque-refresh-token",
                   "token_expires_at": now - 10, "auth_refreshed_at": now - 1000}
        view = admin.credential_view(account)
        self.assertIsNotNone(view["access_expires_at"])
        self.assertIsNone(view["refresh_expires_at"])
        self.assertEqual(view["status"], "refresh_due")
        self.assertEqual(admin.credential_view({**account, "auth_invalid": True})["status"], "needs_reauthorization")
        self.assertEqual(admin.credential_view({})["status"], "not_authorized")

    def test_credential_dates_reject_invalid_claims_without_throwing(self):
        for value in (True, False, "123", None, -1, 0, float("nan"), float("inf"), 253402300800, 10 ** 400):
            with self.subTest(value=value):
                view = admin.credential_view({"access_token": jwt(value), "refresh_token": jwt(value),
                                              "token_expires_at": value, "auth_refreshed_at": value})
                self.assertIsNone(view["access_expires_at"])
                self.assertIsNone(view["refresh_expires_at"])
                self.assertIsNone(view["refreshed_at"])
        for token in ("a.b.c", "a.%!.c", "a." + "a" * 15000 + ".c", 123):
            self.assertIsNone(admin.credential_view({"access_token": token})["access_expires_at"])

    def test_short_lifetime_uses_same_refresh_margin_as_sessions(self):
        with patch.object(admin.time, "time", return_value=1000):
            view = admin.credential_view({"access_token": jwt(1100), "refresh_token": jwt(1100),
                                          "token_expires_at": 1100, "auth_refreshed_at": 1000})
        self.assertEqual(view["refresh_due_at"], datetime.fromtimestamp(1070, timezone.utc).isoformat())

    def test_database_errors_are_mapped(self):
        with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("simulated")):
            with self.assertRaises(store.AccountsError):
                admin.initialize_admin("password")


if __name__ == "__main__":
    unittest.main()
