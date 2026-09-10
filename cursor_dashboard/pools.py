"""用同套餐的可解观测补齐缺失额度上限，仅作估算。

按 (套餐, 账号) 保留最近一次完整观测，各档取中位数，不硬编码额度。
假设同名套餐容量相同；目前没有观测 TTL，也不随删除账号或换套餐清理旧项。
有效观测可能滞后，不能将补齐结果视为远端保证的额度。
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
    return _fill(data, resolve((data or {}).get("plan")))


def fill_visible(data: dict | None, visible: list[dict]) -> dict | None:
    """V2 derives observations only from this authorized query, with no global pool state."""
    key = _plan_key((data or {}).get("plan"))
    values = []
    for item in visible:
        if not key or _plan_key(item.get("plan")) != key:
            continue
        limits = tuple(((item.get("quota") or {}).get(slot) or {}).get("limit_usd")
                       for slot in ("cursor_models", "other_models", "overall"))
        if all(value is not None for value in limits):
            values.append(limits)
    limits = tuple(round(median(column), 2) for column in zip(*values)) if values else (None, None, None)
    return _fill(data, limits)


def _fill(data: dict | None, limits) -> dict | None:
    """给触顶而解不出上限的档位补上同套餐的池子，并标 `limit_inferred`。

    只补 `None` 的档位：账号自己解出来的数永远优先于从别人那儿抄来的。
    """
    quota = (data or {}).get("quota") or {}
    if not quota or all(
        (quota.get(key) or {}).get("limit_usd") is not None
        for key in ("cursor_models", "other_models", "overall")
    ):
        return data

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
