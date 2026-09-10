from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
import random
import time

from ....client import fetch_one


@dataclass(frozen=True)
class RetryPolicy:
    request_retries: int = 2
    rate_limit_retries: int = 3
    retry_base_delay: float = .25
    rate_limit_base_delay: float = 2


class CursorGateway:
    def __init__(self, *, interval=.5, concurrency=3, retry_policy=None):
        self.interval = interval
        self.slots = asyncio.Semaphore(concurrency)
        self.pace = asyncio.Lock()
        self.next_slot = 0.0
        self.retry_policy = retry_policy or RetryPolicy()

    async def __call__(self, cookie, label, name, *args):
        async with self.pace:
            slot = max(time.monotonic(), self.next_slot)
            self.next_slot = slot + self.interval + random.uniform(0, self.interval * .2)
        await asyncio.sleep(max(0, slot - time.monotonic()))
        async with self.slots:
            return await asyncio.to_thread(partial(fetch_one, cookie, label, name, *args,
                                                   retry_policy=self.retry_policy))
