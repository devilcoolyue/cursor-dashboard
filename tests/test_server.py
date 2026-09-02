from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import requests

from cursor_dashboard import server, snapshot
from cursor_dashboard.client import AuthExpired, RateLimited


class RequestConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_cursor_obeys_global_limit(self) -> None:
        active = 0
        peak = 0

        async def fake_to_thread(_func, *_args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {}

        server._request_slots = asyncio.Semaphore(3)
        server._pace_lock = None
        try:
            with patch.object(server, "REQUEST_MIN_INTERVAL", 0), \
                 patch.object(server.asyncio, "to_thread", side_effect=fake_to_thread):
                await asyncio.gather(
                    *(server.fetch_cursor("cookie", "账号", "me") for _ in range(12))
                )
        finally:
            server._request_slots = None

        self.assertEqual(peak, 3)


class PacingTest(unittest.IsolatedAsyncioTestCase):
    """限并发不够，还得限速率——边缘防护看的是单位时间的请求数。"""

    async def test_requests_are_spaced_out_in_time(self) -> None:
        server._pace_lock = None
        server._next_slot = 0.0
        sent: list[float] = []

        async def record(*_args):
            sent.append(time.monotonic())
            return {}

        with patch.object(server, "REQUEST_MIN_INTERVAL", 0.05), \
             patch.object(server.asyncio, "to_thread", side_effect=record):
            await asyncio.gather(
                *(server.fetch_cursor("cookie", "账号", "me") for _ in range(5))
            )

        self.assertEqual(len(sent), 5)
        gaps = [b - a for a, b in zip(sorted(sent), sorted(sent)[1:])]
        self.assertTrue(all(gap >= 0.04 for gap in gaps), gaps)

    async def test_zero_interval_disables_pacing(self) -> None:
        server._pace_lock = None
        with patch.object(server, "REQUEST_MIN_INTERVAL", 0):
            await server._pace()          # 不该挂住


class ClassifyTest(unittest.TestCase):
    def test_rate_limit_wins_over_auth_expiry(self) -> None:
        """混合错误时按限流处理：误报失效会把人骗去重粘 cookie，而那次也会被挡。"""
        kind, _ = server._classify([AuthExpired("失效"), RateLimited("挡住")])

        self.assertEqual(kind, "rate_limited")

    def test_all_auth_failures_are_reported_as_expired(self) -> None:
        kind, _ = server._classify([AuthExpired("失效"), AuthExpired("失效")])

        self.assertEqual(kind, "expired")

    def test_connection_problems_are_network(self) -> None:
        kind, _ = server._classify([requests.ConnectionError("refused")])

        self.assertEqual(kind, "network")

    def test_unknown_errors_carry_their_type(self) -> None:
        kind, message = server._classify([ValueError("坏了")])

        self.assertEqual(kind, "error")
        self.assertIn("ValueError", message)


class RefreshAccountTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        snapshot._snapshots.clear()
        snapshot._inflight.clear()
        self.acc = {"label": "张三", "email": "zhang@example.com",
                    "department": "", "cookie": "cookie-1"}
        self.saved: list[tuple] = []

    def tearDown(self) -> None:
        snapshot._snapshots.clear()
        snapshot._inflight.clear()

    async def test_rate_limit_keeps_the_previous_data(self) -> None:
        snapshot._snapshots["zhang@example.com"] = {
            "fingerprint": snapshot.fingerprint("cookie-1"),
            "data": {"email": "zhang@example.com"}, "ok_at": 100,
            "error": None, "failures": 0, "attempted_at": 100,
        }

        async def blocked(*_args):
            raise RateLimited("挡住")

        with patch.object(server, "fetch_cursor", side_effect=blocked), \
             patch.object(snapshot, "save_snapshot"):
            kind = await server.refresh_account(self.acc)

        self.assertEqual(kind, "rate_limited")
        view = snapshot.view(self.acc, "zhang@example.com")
        self.assertTrue(view["ok"])
        self.assertTrue(view["stale"])
        self.assertFalse(view["expired"])


class ManualTokenTest(unittest.TestCase):
    def setUp(self) -> None:
        server._manual_tokens = float(server.MANUAL_BURST)
        server._manual_refilled = time.monotonic()

    def test_bucket_runs_dry_then_refills(self) -> None:
        taken = [server.take_manual_token() for _ in range(server.MANUAL_BURST + 3)]

        self.assertEqual(taken.count(True), server.MANUAL_BURST)
        self.assertFalse(taken[-1])

        # 冷却窗口过去之后桶应该重新装满
        server._manual_refilled -= server.MANUAL_COOLDOWN
        self.assertTrue(server.take_manual_token())


class AccountIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.accounts = [
            {
                "label": "张三",
                "email": "zhang@example.com",
                "department": "智慧运维",
                "cookie": "secret-1",
            },
            {
                "label": "李四",
                "email": "li@example.com",
                "department": "研发中心",
                "cookie": "secret-2",
            },
            {
                "label": "旧账号",
                "email": "old@example.com",
                "department": "",
                "cookie": "secret-3",
            },
        ]

    def test_filters_accounts_by_department_including_ungrouped(self) -> None:
        selected = server.accounts_for_department(self.accounts, "智慧运维")
        ungrouped = server.accounts_for_department(self.accounts, "")

        self.assertEqual([item["label"] for item in selected], ["张三"])
        self.assertEqual([item["label"] for item in ungrouped], ["旧账号"])
        self.assertEqual(server.accounts_for_department(self.accounts, None), self.accounts)

    def test_public_index_never_contains_cookie(self) -> None:
        public = server.account_index(self.accounts)

        self.assertEqual(len(public), 3)
        self.assertTrue(all("cookie" not in item for item in public))
        self.assertEqual(public[0]["id"], "zhang@example.com")

    def test_department_counts_include_ungrouped(self) -> None:
        counts = server.department_counts(self.accounts)

        self.assertEqual(
            {item["department"]: item["count"] for item in counts},
            {"智慧运维": 1, "研发中心": 1, "": 1},
        )


if __name__ == "__main__":
    unittest.main()
