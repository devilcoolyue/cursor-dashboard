"""按账号缓存额度结果，外加一把 single-flight 锁。

存在的意义是别把 cursor.com 打爆：一次页面刷新就是 5×N 次请求，
多个访客同时看板时更甚。缓存按账号标识存，cookie 变了立即作废。
"""

from __future__ import annotations

import asyncio
import threading
import time

from .config import CACHE_TTL

# ident -> {"at": 时间戳, "cookie": 当时用的 cookie, "result": {...}}
_cache: dict[str, dict] = {}
_lock = threading.Lock()

# ident -> asyncio.Lock，防止多个访客同时进来把同一个账号打 N 遍
_inflight: dict[str, asyncio.Lock] = {}


def lock_for(ident: str) -> asyncio.Lock:
    return _inflight.setdefault(ident, asyncio.Lock())


def get(ident: str, cookie: str) -> dict | None:
    """取 TTL 内的结果。"""
    with _lock:
        hit = _cache.get(ident)
    if not hit or hit["cookie"] != cookie:      # cookie 换了就作废
        return None
    age = time.time() - hit["at"]
    if age > CACHE_TTL:
        return None
    return {**hit["result"], "age": round(age, 1)}


def since(ident: str, cookie: str, t0: float) -> dict | None:
    """取「t0 之后才写入」的结果，不看 TTL。

    用于等锁期间别人已经回源过的情况——**包括强制刷新**。若这里也走 TTL 判断，
    force=1 会绕过缓存，那把锁就把并发强刷串成了排队，比不加锁还慢。
    """
    with _lock:
        hit = _cache.get(ident)
    if not hit or hit["cookie"] != cookie or hit["at"] < t0:
        return None
    return {**hit["result"], "age": round(time.time() - hit["at"], 1)}


def put(ident: str, cookie: str, result: dict) -> None:
    with _lock:
        _cache[ident] = {"at": time.time(), "cookie": cookie, "result": result}


def drop(ident: str) -> None:
    with _lock:
        _cache.pop(ident, None)
    _inflight.pop(ident, None)
