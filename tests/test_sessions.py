from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import requests

from cursor_dashboard import desktop, sessions, snapshot, store, server
from cursor_dashboard.client import AuthExpired, RateLimited


def credential(marker="initial", expires=None):
    expires = int(time.time()) + 7200 if expires is None else expires
    claims = {"sub": "auth0|user_test", "type": "session", "exp": expires, "marker": marker}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return desktop.DesktopSession(f"eyJhbGciOiJIUzI1NiJ9.{body}.signature", claims["sub"], expires,
                                  "refresh-" + marker, "session")


class SessionStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, value in (("DATABASE_PATH", self.root / "accounts.db"),
                            ("LEGACY_ACCOUNTS_PATH", self.root / "missing.json")):
            p = patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        store._initialized.clear()
        snapshot._snapshots.clear()
        snapshot._inflight.clear()
        self.addCleanup(store._initialized.clear)
        self.addCleanup(snapshot._snapshots.clear)
        self.addCleanup(snapshot._inflight.clear)
        self.email = "test@example.test"
        self.account = store.upsert_account("expired-web-cookie", self.email, "Test", session=credential())

    async def test_saved_desktop_session_never_uses_web_cookie(self):
        fetch = AsyncMock(return_value={"planUsage": {"totalSpend": 10}})
        await sessions.request_account(self.account, fetch, "desktop_period")
        fetch.assert_awaited_once_with("", "Test", "desktop_period", self.account["access_token"])

    async def test_concurrent_requests_refresh_only_once_and_persist_rotation(self):
        store.update_session(self.account, credential(expires=int(time.time()) + 20))
        renewed = credential("renewed")
        calls = []
        async def fetch(cookie, label, name, *args):
            calls.append((cookie, name, args))
            await asyncio.sleep(.04)
            return {"access_token": renewed.token, "refresh_token": renewed.refresh_token, "shouldLogout": False}
        results = await asyncio.gather(*(sessions.ensure_account(self.account, fetch) for _ in range(12)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:2], ("", "desktop_refresh"))
        self.assertTrue(all(a["refresh_token"] == renewed.refresh_token for a in results))
        self.assertEqual(store.get_account(self.email)["refresh_token"], renewed.refresh_token)
        self.assertEqual(results[0]["auth_generation"], self.account["auth_generation"])

    async def test_401_forces_one_refresh_then_retries_with_new_at(self):
        renewed = credential("new")
        fetch = AsyncMock(side_effect=[AuthExpired("401"), {"access_token": renewed.token}, {"ok": True}])
        result = await sessions.request_account(self.account, fetch, "desktop_period")
        self.assertEqual(result, {"ok": True})
        self.assertEqual([c.args[2] for c in fetch.await_args_list], ["desktop_period", "desktop_refresh", "desktop_period"])
        self.assertEqual(fetch.await_args_list[-1].args[3], renewed.token)
        self.assertEqual(store.get_account(self.email)["refresh_token"], renewed.token)

    async def test_revoked_session_does_not_retry_with_old_cookie(self):
        fetch = AsyncMock(return_value={"shouldLogout": True})
        with self.assertRaises(AuthExpired), patch.object(store.time, "time", return_value=time.time() + 120):
            await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        with self.assertRaises(AuthExpired):
            await sessions.ensure_account(self.account, fetch)
        fetch.assert_awaited_once()
        self.assertTrue(store.get_account(self.email)["auth_invalid"])
        self.assertEqual(store.get_account(self.email)["auth_refreshed_at"], self.account["auth_refreshed_at"])

    async def test_transient_refresh_error_preserves_credentials_and_releases_lease(self):
        fetch = AsyncMock(side_effect=RateLimited("limited"))
        with self.assertRaises(RateLimited):
            await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        saved = store.get_account(self.email)
        self.assertEqual(saved["refresh_token"], self.account["refresh_token"])
        self.assertFalse(saved["auth_invalid"])
        self.assertTrue(store.claim_auth_lease(self.email, "next"))

    async def test_refresh_cannot_overwrite_concurrent_reauthorization(self):
        replacement = credential("replacement")
        async def fetch(*args):
            store.upsert_account("new-cookie", self.email, None, session=replacement)
            return {"access_token": credential("stale").token}
        result = await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        self.assertEqual(result["access_token"], replacement.token)
        self.assertEqual(store.get_account(self.email)["cookie"], "new-cookie")

    async def test_refresh_failure_cannot_revoke_concurrent_reauthorization(self):
        replacement = credential("replacement")
        async def fetch(*args):
            store.upsert_account("new-cookie", self.email, None, session=replacement)
            return {"shouldLogout": True}
        result = await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        self.assertEqual(result["access_token"], replacement.token)
        self.assertFalse(result["auth_invalid"])

    async def test_deleted_account_is_not_recreated_by_inflight_refresh(self):
        async def fetch(*args):
            store.delete_account(self.email)
            return {"access_token": credential("new").token}
        with self.assertRaises(store.AccountsError):
            await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        self.assertIsNone(store.get_account(self.email))

    async def test_legacy_cookie_migrates_once_then_survives_cookie_failure(self):
        web = "user_test::" + credential().token
        legacy = store.upsert_account(web, self.email, "Test")
        new = credential("migrated")
        identity = {"email": self.email, "sub": new.subject, "authId": new.subject}
        fetch = AsyncMock(side_effect=[identity, {}, {"accessToken": new.token, "refreshToken": new.refresh_token}, identity])
        saved = await sessions.ensure_account(legacy, fetch)
        self.assertEqual(saved["refresh_token"], new.refresh_token)
        self.assertEqual([c.args[2] for c in fetch.await_args_list], ["me", "desktop_callback", "desktop_poll", "desktop_me"])
        fetch.reset_mock()
        await sessions.ensure_account(legacy, fetch)
        fetch.assert_not_called()

    async def test_renewal_keeps_snapshot_and_public_views_do_not_leak_tokens(self):
        snapshot.record_success(self.email, self.account["cookie"], {"email":self.email, "quota":{"overall":{"remaining_pct":50}}})
        fetch = AsyncMock(return_value={"access_token": credential("new").token})
        updated = await sessions.ensure_account(self.account, fetch, rejected_token=self.account["access_token"])
        view = snapshot.view(updated, self.email)
        self.assertTrue(view["ok"])
        self.assertEqual(view["auth"]["mode"], "desktop")
        for secret in (updated["cookie"], updated["access_token"], updated["refresh_token"]):
            self.assertNotIn(secret, repr(view))
            self.assertNotIn(secret, repr(server.account_index([updated])))

    async def test_add_account_commits_credentials_before_future_cookie_is_needed(self):
        new = credential("saved")
        data = {
            "desktop_me": {"email": self.email, "authId": new.subject},
            "desktop_plan": {"planInfo":{"planName":"Free"}},
            "desktop_profile": {"membershipType":"free"},
            "desktop_period": {"planUsage":{"totalSpend":0}},
            "desktop_grok": {"includedLimitZero":True}, "desktop_limit":{"noUsageBasedAllowed":True},
        }
        async def fetch(cookie, label, name, *args):
            self.assertEqual(cookie, "")
            return data[name]
        with patch.object(sessions, "exchange_cookie", return_value=(new, self.email)), \
             patch.object(server, "fetch_cursor", side_effect=fetch):
            result = await server.api_save(server.SaveReq(cookie="new-web-cookie", label="Saved"))
        self.assertTrue(result["ok"])
        saved = store.get_account(self.email)
        self.assertEqual(saved["refresh_token"], new.refresh_token)
        self.assertNotIn(new.token, repr(result))

    async def test_failed_new_authorization_does_not_destroy_existing_credentials(self):
        with patch.object(sessions, "exchange_cookie", side_effect=AuthExpired("invalid")), \
             self.assertRaises(server.HTTPException):
            await server.api_save(server.SaveReq(cookie="bad-cookie"))
        self.assertEqual(store.get_account(self.email)["access_token"], self.account["access_token"])

    async def test_refresh_with_stale_account_record_uses_current_snapshot_identity(self):
        new = store.upsert_account('new-web-cookie', self.email, 'Renamed', session=credential('replacement'))
        responses = {
            'desktop_me': {'email':self.email,'authId':new['auth_subject']},
            'desktop_plan':{'planInfo':{'planName':'Free'}},
            'desktop_profile':{'membershipType':'free'},
            'desktop_period':{'planUsage':{'totalSpend':0}},
            'desktop_grok':{}, 'desktop_limit':{'noUsageBasedAllowed':True},
        }
        async def fetch(cookie, label, name, *args):
            return responses[name]
        with patch.object(server, 'fetch_cursor', side_effect=fetch):
            result = await server.refresh_account(self.account)
        self.assertIsNone(result)
        self.assertTrue(snapshot.view(new, self.email)['ok'])
