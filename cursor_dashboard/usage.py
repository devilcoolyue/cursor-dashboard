"""把 4 个接口的原始返回拼成面板 / CLI 共用的结构。

这里全是纯计算，不发请求——面板和命令行共用同一份口径，改字段只需要改这一处。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .client import CursorClient


# ---------- 取值工具 ----------

def ms_to_dt(v):
    if v in (None, "", 0, "0"):
        return None
    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)


def iso_to_dt(v):
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def pct(v):
    return round(float(v or 0), 2)


def remain(v):
    return round(max(0.0, 100.0 - float(v or 0)), 2)


def cents(v):
    return round((v or 0) / 100, 2)


def _int(v) -> int:
    """接口把 token 数当字符串给（"6574701"），偶尔又给 null。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ---------- 额度池 ----------
# 「Cursor Models 一共有多少额度」不是接口里的字段，但能从官方百分比反解出来。
# totalPercentUsed 是两个池按容量加权的平均数：
#     totalPct·(A+B) = autoPct·A + apiPct·B
# 于是
#     总池   T = totalSpend / totalPct
#     池之比 k = A/B = (apiPct − totalPct) / (totalPct − autoPct)
# 实测某 Pro 账号解出 A=$450、B=$45、T=$495 三个整数，且在两个不同时刻都成立
# （autoPct·A + apiPct·B 精确等于 totalSpend）。
#
# **这和「不要拿美元金额去算百分比」那条不冲突**：那条禁的是拿 totalSpend 去除
# includedAmountCents（$20），两者压根不是一个尺度；这里是反过来，用官方百分比
# 去标定池子有多大，百分比仍然是唯一的事实来源。
#
# 三个百分比互相贴得太近时 k 会退化成 0/0（比如刚开始用、或者两类用量比例恰好
# 等于池的比例），这时只报总池，不猜分池——宁可不显示，也不能显示一个瞎猜的数。
POOL_MIN_GAP = 0.05          # 百分点
# 百分比被服务端截顶在 100：一个真用到 110% 的账号照样报 100.00，代进方程就是假数据。
# 42 个账号的实测里，张琛 auto=98.03 / api=100.00 解出 $402 / $93（真值 $450 / $45），
# 全用满的账号解出的"总池"其实是消费额（$495.32），会随着继续消费一直变大。
# 所以任何一档触顶就不用它：**别为了"这样每张卡都能显示金额"把这道闸去掉。**
PCT_CEILING = 99.99


def pool_limits(total_spend_cents, auto_pct, api_pct, total_pct):
    """反解 (Cursor Models 池, Other Models 池, 综合池)，单位美元。解不出的位置给 None。"""
    spend = float(total_spend_cents or 0)
    total_pct = float(total_pct or 0)
    if spend <= 0 or total_pct <= 0 or total_pct >= PCT_CEILING:
        return (None, None, None)
    total_pool = spend / (total_pct / 100)

    auto_pct = float(auto_pct or 0)
    api_pct = float(api_pct or 0)
    # 综合没触顶但某一档触顶：总池仍然可信（实测唐永林、张琛都解出 $495），分池不可信
    if auto_pct >= PCT_CEILING or api_pct >= PCT_CEILING:
        return (None, None, cents(total_pool))
    # total_pct 一定落在 auto_pct 和 api_pct 之间（它是两者的加权平均）
    low, high = total_pct - auto_pct, api_pct - total_pct
    if abs(low) < POOL_MIN_GAP or abs(high) < POOL_MIN_GAP or (high / low) <= 0:
        return (None, None, cents(total_pool))

    ratio = high / low                       # = auto 池 / api 池
    api_pool = total_pool / (ratio + 1)
    auto_pool = total_pool - api_pool
    # 拿解回代一次。代数上必然成立，这道检查挡的是退化点附近的浮点放大。
    rebuilt = auto_pct / 100 * auto_pool + api_pct / 100 * api_pool
    if abs(rebuilt - spend) > max(1.0, spend * 0.01):
        return (None, None, cents(total_pool))
    return (cents(auto_pool), cents(api_pool), cents(total_pool))


def _slot(used_pct, limit_usd) -> dict:
    """一条额度：百分比永远是主口径，美元是反解出来的补充，解不出就留 None。"""
    slot = {"used_pct": pct(used_pct), "remaining_pct": remain(used_pct)}
    slot["limit_usd"] = limit_usd
    if limit_usd is None:
        slot["used_usd"] = slot["remaining_usd"] = None
    else:
        slot["used_usd"] = round(limit_usd * slot["used_pct"] / 100, 2)
        slot["remaining_usd"] = round(limit_usd - slot["used_usd"], 2)
    return slot


# ---------- 按模型的用量明细 ----------
# tier 是聚合接口自己给的分类，2 = Cursor Models（auto 桶），1 = Other Models。
# **别改成按模型名匹配 period_usage 里的 autoBucketModels**：那个列表实测是滞后的
# （只到 cursor-grok-4.5，没有正在跑的 4.6），按它归类会把新模型全丢进 Other。
CURSOR_TIER = 2
GROUP_NAMES = {"cursor_models": "Cursor Models", "other_models": "Other Models"}


def assemble_detail(aggregated) -> dict:
    """把 aggregated_usage 的返回整理成按分类分组、组内按花费降序的明细。"""
    payload = aggregated or {}
    rows = payload.get("aggregations") or []

    models = []
    for row in rows:
        raw_cents = float(row.get("totalCents") or 0)
        tokens = {key: _int(row.get(field)) for key, field in (
            ("input_tokens", "inputTokens"),
            ("output_tokens", "outputTokens"),
            ("cache_write_tokens", "cacheWriteTokens"),
            ("cache_read_tokens", "cacheReadTokens"),
        )}
        models.append({
            "model": row.get("modelIntent") or "未知模型",
            "group": "cursor_models" if row.get("tier") == CURSOR_TIER else "other_models",
            **tokens,
            "total_tokens": sum(tokens.values()),
            # 小额模型不足 1 分钱，四舍五入会变成 $0.00，所以原始 cents 也带上
            "spend_usd": cents(raw_cents),
            "spend_cents": round(raw_cents, 4),
        })
    models.sort(key=lambda m: (-m["spend_cents"], -m["total_tokens"], m["model"]))

    groups = []
    for key, name in GROUP_NAMES.items():
        picked = [m for m in models if m["group"] == key]
        groups.append({
            "key": key,
            "name": name,
            # 组内先按分求和再转美元，避免每行取整后累积出偏差
            "spend_usd": cents(sum(m["spend_cents"] for m in picked)),
            "total_tokens": sum(m["total_tokens"] for m in picked),
            "models": picked,
        })

    return {
        "groups": groups,
        "totals": {
            "spend_usd": cents(payload.get("totalCostCents")
                               or sum(m["spend_cents"] for m in models)),
            "input_tokens": _int(payload.get("totalInputTokens")),
            "output_tokens": _int(payload.get("totalOutputTokens")),
            "cache_write_tokens": _int(payload.get("totalCacheWriteTokens")),
            "cache_read_tokens": _int(payload.get("totalCacheReadTokens")),
            "total_tokens": sum(m["total_tokens"] for m in models),
            "model_count": len(models),
        },
    }


# ---------- 组装 ----------

def collect(client: CursorClient) -> dict:
    """串行取数，CLI 用。服务端不走它（走 fetch_one + assemble 并发取）。"""
    return assemble(client.label, client.me(), client.plan_info(),
                    client.usage_summary(), client.period_usage())


def assemble(label: str, me, plan_info, usage_summary, period_usage) -> dict:
    """纯计算，不发请求。"""
    plan    = (plan_info or {}).get("planInfo", {}) or {}
    summary = usage_summary or {}
    period  = period_usage or {}
    me      = me or {}

    pu = period.get("planUsage", {}) or {}
    on_demand = (summary.get("individualUsage", {}) or {}).get("onDemand", {}) or {}

    cycle_end = ms_to_dt(plan.get("billingCycleEnd")) or iso_to_dt(summary.get("billingCycleEnd"))
    cycle_start = ms_to_dt(period.get("billingCycleStart")) or iso_to_dt(summary.get("billingCycleStart"))
    days_left = (cycle_end - datetime.now(timezone.utc)).days if cycle_end else None
    auto_pool, api_pool, total_pool = pool_limits(
        pu.get("totalSpend"), pu.get("autoPercentUsed"),
        pu.get("apiPercentUsed"), pu.get("totalPercentUsed"),
    )

    return {
        "label": label,
        "email": me.get("email"),
        "user_id": me.get("sub"),
        "plan": {
            "name": plan.get("planName"),
            "price": plan.get("price"),
            "included_usd": cents(plan.get("includedAmountCents")),
            "membership_type": summary.get("membershipType"),
            "unlimited": summary.get("isUnlimited"),
        },
        # 额度刷新时间
        "cycle": {
            "start": cycle_start.isoformat() if cycle_start else None,
            "reset_at": cycle_end.isoformat() if cycle_end else None,
            "days_left": days_left,
        },
        # 剩余额度（以百分比为准，百分比是官方给的，只做 100 - x；
        # 每档的美元上限由 pool_limits 反解，解不出就是 None，前端不显示）
        "quota": {
            "cursor_models": _slot(pu.get("autoPercentUsed"), auto_pool),
            "other_models":  _slot(pu.get("apiPercentUsed"), api_pool),
            "overall":       _slot(pu.get("totalPercentUsed"), total_pool),
        },
        # 花费口径（参考，不等于剩余额度）
        "spend_usd": {
            "total": cents(pu.get("totalSpend")),
            "from_included": cents(pu.get("includedSpend")),
            "from_bonus": cents(pu.get("bonusSpend")),
            "included_limit": cents(pu.get("limit")),
            "bonus_exhausted": not pu.get("remainingBonus", False),
        },
        "on_demand": {
            "enabled": bool(on_demand.get("enabled")),
            "used_usd": cents(on_demand.get("used")),
            "limit_usd": cents(on_demand.get("limit")) if on_demand.get("limit") else None,
        },
        "notice": period.get("displayMessage") or None,
    }
