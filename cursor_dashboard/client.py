"""cursor.com 内部接口的薄封装。

这些接口非官方公开（是网页 dashboard 自己调的），字段随时可能变。
认证只靠一个 cookie：WorkosCursorSessionToken，等同登录态。
"""

from __future__ import annotations

import random
import time

import requests

from .config import (
    RATE_LIMIT_BASE_DELAY,
    RATE_LIMIT_RETRIES,
    REQUEST_RETRIES,
    RETRY_BASE_DELAY,
)

BASE = "https://cursor.com"
COOKIE_NAME = "WorkosCursorSessionToken"
TIMEOUT = 20
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 一个账号常规要打的 4 个接口。服务端按这个粒度并发，CLI 仍按顺序串行。
# **aggregated_usage 刻意不在这里**：它是点开卡片才拉的按需明细，加进来会让
# 后台轮询的出站量凭空 +25%，而按 IP 限流是这个项目最大的风险。
ENDPOINTS = ("me", "plan_info", "usage_summary", "period_usage")
RETRYABLE_STATUS = {500, 502, 504}
# 被挡住时的状态码。429 是标准限流；403 只有在返回 HTML 时才算（见 _call）；
# 503 通常是边缘节点在挡，不是接口真的挂了。
THROTTLE_STATUS = {429, 503}
RETRY_AFTER_CAP = 120


class AuthExpired(RuntimeError):
    """会话 Cookie 失效，需要重新导出"""


class RateLimited(RuntimeError):
    """被 Cursor / Vercel 临时挡住，cookie 本身没问题，退避后重试即可。

    **不要把它并进 AuthExpired**：限流返回的是 403 + HTML 安全拦截页，
    过去一律当成失效，结果整屏卡片变红、用户重新粘贴 cookie 还是红的。
    """

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(r: requests.Response) -> float | None:
    """解析 Retry-After（只认整秒写法，HTTP-date 少见就不猜了）。"""
    raw = (r.headers.get("Retry-After") or "").strip()
    if not raw.isdigit():
        return None
    return min(float(raw), RETRY_AFTER_CAP)


def _is_json(r: requests.Response) -> bool:
    return "json" in (r.headers.get("Content-Type") or "").lower()


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
        if r.status_code == 401:
            raise AuthExpired(f"[{self.label}] 会话已失效，请重新登录")
        if r.status_code == 403:
            # 鉴权接口拒绝会带 JSON body；HTML 是边缘防护的拦截页，跟 cookie 无关
            if _is_json(r):
                raise AuthExpired(f"[{self.label}] 会话已失效，请重新登录")
            raise RateLimited(
                f"[{self.label}] Cursor 暂时限制了请求，稍后会自动重试",
                _retry_after(r),
            )
        if r.status_code in THROTTLE_STATUS:
            raise RateLimited(
                f"[{self.label}] Cursor 暂时限制了请求，稍后会自动重试",
                _retry_after(r),
            )
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

    def aggregated_usage(self, start_ms: int, end_ms: int):
        """本周期按模型聚合的 token 与花费。窗口是任意的，传账单周期起点就是"本周期"。

        teamId / userId 传 0 表示"就用这个 cookie 对应的个人账号"——实测传 0、传真实
        id、整个字段不传，返回完全一致；但 teamId 传 -1 会被判成"缺 team id"而 401。
        """
        return self._call("POST", "/api/dashboard/get-aggregated-usage-events", {
            "teamId": 0, "userId": 0,
            "startDate": int(start_ms), "endDate": int(end_ms),
        })


def _backoff(base: float, attempt: int) -> float:
    return base * (2 ** attempt) + random.uniform(0, base)


def fetch_one(cookie: str, label: str, name: str, *args):
    """取单个接口。刻意每次新建 Session —— requests.Session 跨线程共享不安全，
    而这几个请求本来就要并发发出去。瞬时错误和限流会退避重试，退避基数不同：
    连接抖动几百毫秒就够，被挡住则要等几秒。"""
    attempts = max(REQUEST_RETRIES, RATE_LIMIT_RETRIES) + 1
    for attempt in range(attempts):
        client = CursorClient(cookie, label)
        try:
            return getattr(client, name)(*args)
        except AuthExpired:
            raise
        except RateLimited as exc:
            if attempt >= RATE_LIMIT_RETRIES:
                raise
            time.sleep(exc.retry_after or _backoff(RATE_LIMIT_BASE_DELAY, attempt))
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = isinstance(exc, (requests.ConnectionError, requests.Timeout))
            retryable = retryable or status in RETRYABLE_STATUS
            if not retryable or attempt >= REQUEST_RETRIES:
                raise
            time.sleep(_backoff(RETRY_BASE_DELAY, attempt))
        finally:
            client.s.close()

    raise RuntimeError("unreachable")
