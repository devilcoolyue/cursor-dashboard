"""将桌面及旧网页响应组装为面板 / CLI 共用的数据口径。

assemble 系列与额度计算不发请求；collect 仅保留旧网页串行取数兼容入口。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
# 假设总百分比为两池的容量加权平均，反解美元上限；百分比仍使用上游口径。
# 三个百分比过近时分池方程退化，只保留可解的总池。公式及估算边界见维护文档。
POOL_MIN_GAP = 0.05          # 百分点
# 上游百分比可能截顶；综合触顶时丢弃全部估算，分档触顶时仅丢弃分池。
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
    # 综合没触顶但某一档触顶：保留总池估算，不反解分池。
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
    """保留的网页串行取数入口；当前 CLI 和服务端均走桌面凭证路径。"""
    return assemble(client.label, client.me(), client.plan_info(),
                    client.usage_summary(), client.period_usage(), client.grok_status())


def assemble_desktop(label, me, plan, profile, period, grok, hard_limit):
    """Adapt desktop RPC JSON to the existing quota calculation contract."""
    spend = period.get("spendLimitUsage") or {}
    limit = spend.get("overallLimit")
    if limit is None and (hard_limit.get("hardLimit") or 0) > 0:
        limit = hard_limit["hardLimit"] * 100
    summary = {
        "membershipType": profile.get("membershipType"),
        "individualUsage": {"onDemand": {
            "enabled": not hard_limit.get("noUsageBasedAllowed", False)
                       and not hard_limit.get("onDemandSpendDisabledByOrganization", False),
            "used": spend.get("totalSpend", 0), "limit": limit,
        }},
    }
    for key in ("billingCycleStart", "billingCycleEnd"):
        date = ms_to_dt(period.get(key))
        summary[key] = date.isoformat() if date else None
    return assemble(label, {**me, "sub": me.get("authId")}, plan, summary, period, grok)


def assemble(label: str, me, plan_info, usage_summary, period_usage, grok_status) -> dict:
    """纯计算，不发请求。"""
    plan    = (plan_info or {}).get("planInfo", {}) or {}
    summary = usage_summary or {}
    period  = period_usage or {}
    grok    = grok_status or {}
    me      = me or {}

    pu = period.get("planUsage", {}) or {}
    on_demand = (summary.get("individualUsage", {}) or {}).get("onDemand", {}) or {}

    cycle_end = ms_to_dt(plan.get("billingCycleEnd")) or iso_to_dt(summary.get("billingCycleEnd"))
    cycle_start = ms_to_dt(period.get("billingCycleStart")) or iso_to_dt(summary.get("billingCycleStart"))
    days_left = (cycle_end - datetime.now(timezone.utc)).days if cycle_end else None

    # 无额度账号也返回周期和升级提示，不能把缺失用量当成剩余 100%。
    grok_usage = grok.get("usagePercent")
    has_grok_quota = (not grok.get("includedLimitZero") and grok.get("hasNonZeroIncludedLimit") is not False
                      and grok_usage not in (None, ""))
    # 优先使用明确重置时间，缺失时按周期起点加 7 天。
    grok_start = iso_to_dt(grok.get("currentPeriodStart")) if has_grok_quota else None
    grok_reset = (iso_to_dt(grok.get("nextResetTimestampUtc"))
                  or (grok_start + timedelta(days=7) if grok_start else None)) if has_grok_quota else None

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
        # Grok Bot 的周额度，跟上面三条完全独立（不占同一个池子）。
        # 无包含额度、用量缺失或接口失败时整条给 None，面板和 CLI 都不显示。
        "grok_weekly": {
            "used_pct": pct(grok_usage),
            "remaining_pct": remain(grok_usage),
            "reset_at": grok_reset.isoformat() if grok_reset else None,
        } if has_grok_quota else None,
        "notice": period.get("displayMessage") or None,
    }
