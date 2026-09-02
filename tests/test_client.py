from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from cursor_dashboard import client


def response(status: int, content_type: str = "text/html", **headers) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers["Content-Type"] = content_type
    r.headers.update(headers)
    r._content = b""
    return r


class CallClassificationTest(unittest.TestCase):
    """403 到底是"cookie 失效"还是"被临时挡住"，全靠这里区分。"""

    def call(self, r: requests.Response):
        c = client.CursorClient("fake-cookie", "测试账号")
        try:
            with patch.object(c.s, "request", return_value=r):
                return c._call("GET", "/api/auth/me")
        finally:
            c.s.close()

    def test_html_403_is_rate_limited_not_expired(self) -> None:
        with self.assertRaises(client.RateLimited):
            self.call(response(403, "text/html; charset=utf-8"))

    def test_json_403_is_expired(self) -> None:
        with self.assertRaises(client.AuthExpired):
            self.call(response(403, "application/json"))

    def test_401_is_expired(self) -> None:
        with self.assertRaises(client.AuthExpired):
            self.call(response(401, "text/html"))

    def test_429_is_rate_limited_and_honours_retry_after(self) -> None:
        with self.assertRaises(client.RateLimited) as caught:
            self.call(response(429, "text/html", **{"Retry-After": "30"}))
        self.assertEqual(caught.exception.retry_after, 30)

    def test_retry_after_is_capped(self) -> None:
        with self.assertRaises(client.RateLimited) as caught:
            self.call(response(503, "text/html", **{"Retry-After": "99999"}))
        self.assertEqual(caught.exception.retry_after, client.RETRY_AFTER_CAP)

    def test_login_redirect_is_expired(self) -> None:
        r = response(307, "text/html", Location="https://api.workos.com/sso/authorize")
        with self.assertRaises(client.AuthExpired):
            self.call(r)


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
    @patch.object(client.random, "uniform", return_value=0)
    @patch.object(
        client.CursorClient,
        "me",
        side_effect=[client.RateLimited("blocked"), {"ok": True}],
    )
    def test_retries_rate_limits_with_a_longer_backoff(self, me, _uniform, sleep) -> None:
        with patch.object(client, "RATE_LIMIT_RETRIES", 3), patch.object(
            client, "RATE_LIMIT_BASE_DELAY", 2.0
        ):
            result = client.fetch_one("fake-cookie", "测试账号", "me")

        self.assertEqual(result, {"ok": True})
        sleep.assert_called_once_with(2.0)

    @patch.object(client.time, "sleep")
    @patch.object(client.CursorClient, "me")
    def test_rate_limit_retry_waits_for_retry_after(self, me, sleep) -> None:
        me.side_effect = [client.RateLimited("blocked", retry_after=7), {"ok": True}]

        client.fetch_one("fake-cookie", "测试账号", "me")

        sleep.assert_called_once_with(7)

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
        r = requests.Response()
        r.status_code = 400
        me.side_effect = requests.HTTPError("bad request", response=r)

        with patch.object(client, "REQUEST_RETRIES", 2):
            with self.assertRaises(requests.HTTPError):
                client.fetch_one("fake-cookie", "测试账号", "me")

        self.assertEqual(me.call_count, 1)
        sleep.assert_not_called()

    @patch.object(client.time, "sleep")
    @patch.object(client.CursorClient, "_call", side_effect=client.RateLimited("blocked"))
    def test_grok_status_lets_rate_limits_through(self, _call, _sleep) -> None:
        """grok 是非关键接口，但限流必须冒泡，否则调度器不知道该退避。"""
        c = client.CursorClient("fake-cookie", "测试账号")
        try:
            with self.assertRaises(client.RateLimited):
                c.grok_status()
        finally:
            c.s.close()


if __name__ == "__main__":
    unittest.main()
