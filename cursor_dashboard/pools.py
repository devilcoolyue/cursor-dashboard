"""同一套餐的额度池登记表。

`usage.pool_limits` 只能从**没触顶**的百分比反解出池子：用满的账号三个百分比都被
截在 100，方程分母归零，什么都解不出来。可池子大小是套餐决定的常量——42 个 Pro
账号实测全是 $450 / $45 / $495——所以用满的那几个账号完全不必留空，从同套餐里
解得出的账号那儿抄一份就行。

**这不是把 450 写死。** 表里的每个数都来自某个真实账号当场解出来的结果，套餐涨价、
Cursor 改额度、换成 Pro+ 或 Business，跟着就变了。写死才是把今天的数字焊进代码。

按 (套餐, 账号) 存最近一次观测，所以同一个账号反复刷新只占一条，表不会无限长；
取值时对同套餐的所有观测逐档取中位数，个别账号的抖动或脏数据盖不过大多数。
"""

from __future__ import annotations

import threading
from statistics import median

# plan -> ident -> (cursor_models 池, other_models 池, 综合池)，单位美元
_observed: dict[str, dict[str, tuple[float, float, float]]] = {}
_lock = threading.Lock()


def _plan_key(plan: dict | None) -> str:
    plan = plan or {}
    name = (plan.get("name") or plan.get("membership_type") or "").strip()
    return name.lower()


def observe(ident: str, data: dict | None) -> None:
    """账号刷新成功后登记一次。三档都解出来了才算数——缺档的解本身就不可信。"""
    quota = (data or {}).get("quota") or {}
    limits = tuple(
        (quota.get(key) or {}).get("limit_usd")
        for key in ("cursor_models", "other_models", "overall")
    )
    if any(v is None for v in limits):
        return
    key = _plan_key((data or {}).get("plan"))
    if not key:
        return
    with _lock:
        _observed.setdefault(key, {})[ident] = limits


def resolve(plan: dict | None) -> tuple[float | None, float | None, float | None]:
    """这个套餐的额度池。没有任何账号解出来过就是三个 None。"""
    key = _plan_key(plan)
    with _lock:
        seen = list(_observed.get(key, {}).values())
    if not seen:
        return (None, None, None)
    return tuple(round(median(values), 2) for values in zip(*seen))


def fill(data: dict | None) -> dict | None:
    """给触顶而解不出上限的档位补上同套餐的池子，并标 `limit_inferred`。

    只补 `None` 的档位：账号自己解出来的数永远优先于从别人那儿抄来的。
    """
    quota = (data or {}).get("quota") or {}
    if not quota or all(
        (quota.get(key) or {}).get("limit_usd") is not None
        for key in ("cursor_models", "other_models", "overall")
    ):
        return data

    limits = resolve(data.get("plan"))
    if all(v is None for v in limits):
        return data

    patched = dict(quota)
    for key, limit in zip(("cursor_models", "other_models", "overall"), limits):
        slot = quota.get(key) or {}
        if slot.get("limit_usd") is not None or limit is None:
            continue
        used_pct = slot.get("used_pct") or 0
        patched[key] = {
            **slot,
            "limit_usd": limit,
            "used_usd": round(limit * used_pct / 100, 2),
            "remaining_usd": round(limit - limit * used_pct / 100, 2),
            # 前端不区分显示，但排查时要能一眼看出这个数不是本账号自己算出来的
            "limit_inferred": True,
        }
    return {**data, "quota": patched}


def snapshot_state() -> dict[str, int]:
    """/api/status 用：每个套餐现在有几个账号支撑着这张表。"""
    with _lock:
        return {plan: len(rows) for plan, rows in _observed.items()}


def reset() -> None:
    with _lock:
        _observed.clear()
