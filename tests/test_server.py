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


if __name__ == "__main__":
    unittest.main()
