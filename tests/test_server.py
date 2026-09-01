from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from cursor_dashboard import server


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
        try:
            with patch.object(server.asyncio, "to_thread", side_effect=fake_to_thread):
                await asyncio.gather(
                    *(server.fetch_cursor("cookie", "账号", "me") for _ in range(12))
                )
        finally:
            server._request_slots = None

        self.assertEqual(peak, 3)


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
