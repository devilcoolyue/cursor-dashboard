from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from cursor_dashboard import snapshot, store
from cursor_dashboard.scheduler import Scheduler


async def _noop(_acc):
    return None


class PickTest(unittest.TestCase):
    def setUp(self) -> None:
        snapshot._snapshots.clear()
        self.accounts = [
            {"label": "甲", "email": "a@example.com", "cookie": "c1"},
            {"label": "乙", "email": "b@example.com", "cookie": "c2"},
            {"label": "丙", "email": "c@example.com", "cookie": "c3"},
        ]

    def tearDown(self) -> None:
        snapshot._snapshots.clear()

    def stamp(self, ident: str, cookie: str, attempted_at: int) -> None:
        snapshot._snapshots[ident] = {
            "fingerprint": store.fingerprint(cookie),
            "data": None, "ok_at": 0, "error": None,
            "failures": 0, "attempted_at": attempted_at,
        }

    def test_picks_the_account_waiting_longest(self) -> None:
        self.stamp("a@example.com", "c1", 300)
        self.stamp("b@example.com", "c2", 100)
        self.stamp("c@example.com", "c3", 200)

        self.assertEqual(Scheduler._pick(self.accounts)["email"], "b@example.com")

    def test_a_never_refreshed_account_goes_first(self) -> None:
        self.stamp("a@example.com", "c1", 300)
        self.stamp("c@example.com", "c3", 200)

        self.assertEqual(Scheduler._pick(self.accounts)["email"], "b@example.com")


class PacingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Scheduler(_noop)

    def test_gap_spreads_the_cycle_across_all_accounts(self) -> None:
        with patch("cursor_dashboard.scheduler.REFRESH_INTERVAL", 900), \
             patch("cursor_dashboard.scheduler.random.uniform", return_value=1.0):
            self.assertAlmostEqual(self.s._gap(42), 900 / 42)

    def test_rate_limits_widen_the_gap_and_success_walks_it_back(self) -> None:
        for _ in range(3):
            self.s._feedback("rate_limited")
        self.assertEqual(self.s._backoff, 8.0)

        for _ in range(10):
            self.s._feedback(None)
        self.assertAlmostEqual(self.s._backoff, 5.6)

    def test_backoff_is_capped(self) -> None:
        with patch("cursor_dashboard.scheduler.REFRESH_MAX_BACKOFF", 4.0):
            for _ in range(20):
                self.s._feedback("rate_limited")

        self.assertEqual(self.s._backoff, 4.0)

    def test_auth_failures_do_not_touch_the_pace(self) -> None:
        """失效是那个账号自己的问题，不该拖慢所有人。"""
        self.s._feedback("expired")
        self.s._feedback("network")

        self.assertEqual(self.s._backoff, 1.0)
        self.assertEqual(self.s._streak, 0)

    def test_gap_never_drops_below_the_floor(self) -> None:
        with patch("cursor_dashboard.scheduler.REFRESH_INTERVAL", 60), \
             patch("cursor_dashboard.scheduler.REFRESH_MIN_GAP", 2.0), \
             patch("cursor_dashboard.scheduler.random.uniform", return_value=1.0):
            self.assertEqual(self.s._gap(500), 2.0)

    def test_idle_slows_down_and_a_visitor_speeds_it_back_up(self) -> None:
        with patch("cursor_dashboard.scheduler.REFRESH_IDLE_AFTER", 0):
            self.assertTrue(self.s.idle)
            with patch("cursor_dashboard.scheduler.REFRESH_INTERVAL", 900), \
                 patch("cursor_dashboard.scheduler.REFRESH_IDLE_FACTOR", 4):
                self.assertEqual(self.s._cycle(), 3600)
            self.s.touch()
            self.assertTrue(self.s._wake.is_set())    # 立刻打断当前的长等待

        self.assertFalse(self.s.idle)


class LoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        snapshot._snapshots.clear()
        self.accounts = [
            {"label": n, "email": f"{n}@example.com", "cookie": "c"}
            for n in ("甲", "乙", "丙")
        ]

    def tearDown(self) -> None:
        snapshot._snapshots.clear()

    async def drive(self, refresh, seconds: float = 0.25) -> Scheduler:
        s = Scheduler(refresh)
        with patch("cursor_dashboard.scheduler.load_accounts", return_value=self.accounts), \
             patch("cursor_dashboard.scheduler.REFRESH_INTERVAL", 0.06), \
             patch("cursor_dashboard.scheduler.REFRESH_MIN_GAP", 0.0), \
             patch.object(snapshot, "save_snapshot"):
            s.start()
            await asyncio.sleep(seconds)
            await s.stop()
        return s

    async def test_rotates_through_every_account_before_repeating(self) -> None:
        touched: list[str] = []

        async def refresh(acc):
            touched.append(acc["email"])
            snapshot.record_success(acc["email"], acc["cookie"], {"email": acc["email"]})
            return None

        await self.drive(refresh)

        self.assertGreaterEqual(len(touched), 3)
        self.assertEqual(set(touched[:3]), {a["email"] for a in self.accounts})

    async def test_a_failing_account_does_not_kill_the_loop(self) -> None:
        """单个账号炸了不能让后台刷新整个停摆——那样所有卡片都会慢慢变旧。"""
        touched: list[str] = []

        async def refresh(acc):
            touched.append(acc["email"])
            snapshot.record_failure(acc["email"], acc["cookie"], "error", "炸了")
            raise RuntimeError("回调自己没兜住")

        s = await self.drive(refresh)

        self.assertGreaterEqual(len(touched), 3)
        self.assertEqual(s._backoff, 1.0)

    async def test_rate_limits_slow_the_loop_down(self) -> None:
        async def refresh(acc):
            snapshot.record_failure(acc["email"], acc["cookie"], "rate_limited", "挡住")
            return "rate_limited"

        s = await self.drive(refresh, seconds=0.2)

        self.assertGreater(s._backoff, 1.0)
        self.assertGreater(s.status()["throttled"], 0)


if __name__ == "__main__":
    unittest.main()
