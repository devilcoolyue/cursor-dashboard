from __future__ import annotations

import unittest
from unittest.mock import patch

from cursor_dashboard import cache


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        cache._cache.clear()
        cache._inflight.clear()

    def tearDown(self) -> None:
        cache._cache.clear()
        cache._inflight.clear()

    def test_custom_error_ttl_expires_quickly(self) -> None:
        with patch.object(cache.time, "time", return_value=100):
            cache.put("one", "cookie", {"ok": False}, ttl=5)

        with patch.object(cache.time, "time", return_value=104):
            self.assertIsNotNone(cache.get("one", "cookie"))
        with patch.object(cache.time, "time", return_value=106):
            self.assertIsNone(cache.get("one", "cookie"))


if __name__ == "__main__":
    unittest.main()
