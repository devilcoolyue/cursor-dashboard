"""后台刷新调度器：一个账号一个账号地慢慢刷，把请求摊平在时间轴上。

面板打开时不再回源，所有常规的 cursor.com 访问都从这里发出。之所以要这样：
过去"刷新全部"会在几秒内打出 N×5 个请求（42 个账号就是 210 个），瞬时几十 QPS，
Vercel 边缘防护直接回 403 HTML 拦截页。摊到 REFRESH_INTERVAL 里之后，同样的账号
数只有 0.2 QPS 左右，差三个数量级。

节奏是自适应的：撞到限流就把间隔翻倍，连续成功再慢慢收回来；长时间没人看面板
就降速——没人看的时候不值得持续打 cursor.com。
"""

from __future__ import annotations

import asyncio
import random
import time

from . import snapshot
from .config import (
    REFRESH_IDLE_AFTER,
    REFRESH_IDLE_FACTOR,
    REFRESH_INTERVAL,
    REFRESH_MAX_BACKOFF,
    REFRESH_MIN_GAP,
)
from .store import AccountsError, account_id, load_accounts

# 连续成功这么多次才把退避收紧一档，避免刚被限流就急着加速
RECOVERY_STREAK = 10
RECOVERY_FACTOR = 0.7
EMPTY_RETRY = 30.0
ERROR_RETRY = 60.0


class Scheduler:
    """`refresh` 是 server 传进来的回调：async (acc) -> 失败类型或 None。"""

    def __init__(self, refresh):
        self._refresh = refresh
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_seen = time.monotonic()
        self._backoff = 1.0
        self._streak = 0
        self._throttled = 0
        self._last_gap = 0.0
        self._last_account = ""

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="refresh-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # ---------- 外部信号 ----------

    def touch(self) -> None:
        """有人在看面板。从降速状态被叫醒时立刻恢复正常节奏。"""
        if self.idle:
            self._wake.set()
        self._last_seen = time.monotonic()

    @property
    def idle(self) -> bool:
        return time.monotonic() - self._last_seen > REFRESH_IDLE_AFTER

    def status(self) -> dict:
        return {
            "enabled": self._task is not None,
            "interval": REFRESH_INTERVAL,
            "cycle_seconds": round(self._cycle()),
            "backoff": round(self._backoff, 2),
            "idle": self.idle,
            "throttled": self._throttled,
            "last_account": self._last_account,
        }

    # ---------- 节奏 ----------

    def _cycle(self) -> float:
        """当前实际的整轮周期，前端用它显示"后台每 X 分钟更新一次"。"""
        cycle = REFRESH_INTERVAL * self._backoff
        return cycle * REFRESH_IDLE_FACTOR if self.idle else cycle

    def _gap(self, count: int) -> float:
        gap = self._cycle() / max(1, count)
        gap = max(REFRESH_MIN_GAP, gap)
        return gap * random.uniform(0.85, 1.15)

    def _feedback(self, kind: str | None) -> None:
        if kind == "rate_limited":
            self._backoff = min(self._backoff * 2, REFRESH_MAX_BACKOFF)
            self._streak = 0
            self._throttled += 1
            return
        if kind is not None:
            return          # 认证失效、网络抖动之类跟节奏无关，别乱动退避
        self._streak += 1
        if self._streak >= RECOVERY_STREAK and self._backoff > 1.0:
            self._backoff = max(1.0, self._backoff * RECOVERY_FACTOR)
            self._streak = 0

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake.clear()

    # ---------- 主循环 ----------

    @staticmethod
    def _pick(accounts: list[dict]) -> dict:
        """挑最久没刷过的那个。从没刷过的 attempted_at 是 0，自然排最前。
        按"最旧优先"而不是固定轮次，账号增删时不用重排队列。"""
        return min(
            accounts,
            key=lambda acc: snapshot.attempted_at(account_id(acc), acc["cookie"]),
        )

    async def _loop(self) -> None:
        while True:
            try:
                accounts = await asyncio.to_thread(load_accounts)
            except AccountsError:
                await self._sleep(ERROR_RETRY)
                continue
            except asyncio.CancelledError:
                raise

            if not accounts:
                await self._sleep(EMPTY_RETRY)
                continue

            acc = self._pick(accounts)
            self._last_account = account_id(acc)
            try:
                self._feedback(await self._refresh(acc))
            except asyncio.CancelledError:
                raise
            except Exception:
                # 回调自己已经把失败写进快照了；循环绝不能因为单个账号退出
                pass

            self._last_gap = self._gap(len(accounts))
            await self._sleep(self._last_gap)
