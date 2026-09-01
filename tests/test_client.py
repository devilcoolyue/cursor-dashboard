from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from cursor_dashboard import client


class FetchOneTest(unittest.TestCase):
    @patch.object(client.time, "sleep")
    @patch.object(client.random, "uniform", return_value=0)
    @patch.object(
        client.CursorClient,
        "me",
        side_effect=[requests.ConnectionError("refused"), {"ok": True}],
    )
    def test_retries_connection_errors(self, me, _uniform, sleep) -> None:
        with patch.object(client, "REQUEST_RETRIES", 2), patch.object(
            client, "RETRY_BASE_DELAY", 0.25
        ):
            result = client.fetch_one("fake-cookie", "测试账号", "me")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(me.call_count, 2)
        sleep.assert_called_once_with(0.25)

    @patch.object(client.time, "sleep")
    @patch.object(client.CursorClient, "me", side_effect=client.AuthExpired("expired"))
    def test_does_not_retry_expired_auth(self, me, sleep) -> None:
        with patch.object(client, "REQUEST_RETRIES", 2):
            with self.assertRaises(client.AuthExpired):
                client.fetch_one("fake-cookie", "测试账号", "me")

        self.assertEqual(me.call_count, 1)
        sleep.assert_not_called()

    @patch.object(client.time, "sleep")
    @patch.object(client.CursorClient, "me")
    def test_does_not_retry_non_transient_http_errors(self, me, sleep) -> None:
        response = requests.Response()
        response.status_code = 400
        me.side_effect = requests.HTTPError("bad request", response=response)

        with patch.object(client, "REQUEST_RETRIES", 2):
            with self.assertRaises(requests.HTTPError):
                client.fetch_one("fake-cookie", "测试账号", "me")

        self.assertEqual(me.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
