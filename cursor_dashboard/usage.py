"""把 5 个接口的原始返回拼成面板 / CLI 共用的结构。

这里全是纯计算，不发请求——面板和命令行共用同一份口径，改字段只需要改这一处。
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


# ---------- 组装 ----------

def collect(client: CursorClient) -> dict:
    """串行取数，CLI 用。服务端不走它（走 fetch_one + assemble 并发取）。"""
    return assemble(client.label, client.me(), client.plan_info(),
                    client.usage_summary(), client.period_usage(), client.grok_status())


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

    grok_start = iso_to_dt(grok.get("currentPeriodStart"))
    grok_reset = grok_start + timedelta(days=7) if grok_start else None

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
        # 剩余额度（以百分比为准，百分比是官方给的，只做 100 - x）
        "quota": {
            "cursor_models": {"used_pct": pct(pu.get("autoPercentUsed")),
                              "remaining_pct": remain(pu.get("autoPercentUsed"))},
            "other_models":  {"used_pct": pct(pu.get("apiPercentUsed")),
                              "remaining_pct": remain(pu.get("apiPercentUsed"))},
            "overall":       {"used_pct": pct(pu.get("totalPercentUsed")),
                              "remaining_pct": remain(pu.get("totalPercentUsed"))},
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
        "grok_weekly": {
            "used_pct": pct(grok.get("usagePercent")),
            "remaining_pct": remain(grok.get("usagePercent")),
            "reset_at": grok_reset.isoformat() if grok_reset else None,
        } if grok else None,
        "notice": period.get("displayMessage") or None,
    }
