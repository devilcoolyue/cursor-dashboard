"""cursor.com 内部接口的薄封装。

这些接口非官方公开（是网页 dashboard 自己调的），字段随时可能变。
认证只靠一个 cookie：WorkosCursorSessionToken，等同登录态。
"""

from __future__ import annotations

import random
import time

import requests

from .config import REQUEST_RETRIES, RETRY_BASE_DELAY

BASE = "https://cursor.com"
COOKIE_NAME = "WorkosCursorSessionToken"
TIMEOUT = 20
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 一个账号要打的 5 个接口。服务端按这个粒度并发，CLI 仍按顺序串行。
ENDPOINTS = ("me", "plan_info", "usage_summary", "period_usage", "grok_status")
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AuthExpired(RuntimeError):
    """会话 Cookie 失效，需要重新导出"""


class CursorClient:
    def __init__(self, cookie: str, label: str = ""):
        self.label = label or "unnamed"
        self.s = requests.Session()
        self.s.cookies.set(COOKIE_NAME, cookie, domain="cursor.com", path="/")
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE,
            "Referer": f"{BASE}/dashboard/spending",
        })

    def _call(self, method: str, path: str, payload=None):
        # 不跟跳转：cookie 失效时接口不返回 401，而是 307 去 WorkOS 登录页，
        # 跟过去只会拿到一个和本次请求无关的 404
        r = self.s.request(method, BASE + path, json=payload, timeout=TIMEOUT,
                           allow_redirects=False)
        if r.status_code in (401, 403):
            raise AuthExpired(f"[{self.label}] 会话已失效，请重新登录")
        if r.is_redirect:
            loc = r.headers.get("Location", "")
            if any(h in loc for h in ("/api/auth/login", "workos.com", "/login")):
                raise AuthExpired(f"[{self.label}] 会话已失效，请重新登录")
            raise RuntimeError(f"[{self.label}] {path} 意外跳转: {loc}")
        r.raise_for_status()
        return r.json() if r.content else {}

    def me(self):             return self._call("GET",  "/api/auth/me")
    def plan_info(self):      return self._call("POST", "/api/dashboard/get-plan-info", {})
    def usage_summary(self):  return self._call("GET",  "/api/usage-summary")
    def period_usage(self):   return self._call("POST", "/api/dashboard/get-current-period-usage", {})

    def grok_status(self):
        try:
            return self._call("POST", "/api/dashboard/get-sand-usage-status", {})
        except AuthExpired:
            raise
        except Exception:
            return {}          # 非关键数据，失败就跳过


def fetch_one(cookie: str, label: str, name: str):
    """取单个接口。刻意每次新建 Session —— requests.Session 跨线程共享不安全，
    而 5 个请求本来就要并发发出去。连接类瞬时错误会短退避重试。"""
    for attempt in range(REQUEST_RETRIES + 1):
        client = CursorClient(cookie, label)
        try:
            return getattr(client, name)()
        except AuthExpired:
            raise
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout))
            retryable = retryable or status in RETRYABLE_STATUS
            if not retryable or attempt >= REQUEST_RETRIES:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            time.sleep(delay + random.uniform(0, RETRY_BASE_DELAY))
        finally:
            client.s.close()

    raise RuntimeError("unreachable")
