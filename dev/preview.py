"""Isolated UI preview with synthetic accounts; no database or outbound requests.

Run: python dev/preview.py --port 8789
Use "demo" as the cookie when adding or renewing a preview account.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WEB = Path(__file__).resolve().parents[1] / "cursor_dashboard" / "web"
app = FastAPI()
app.mount("/static", StaticFiles(directory=WEB), name="static")


def now():
    return datetime.now(timezone.utc)


def make_account(index, label, department):
    remaining = [78.4, 45.8, 24.6, 8.2, 0, 92.1][index % 6]
    quota = {
        key: {"remaining_pct": remaining, "used_pct": 100 - remaining, "limit_usd": limit}
        for key, limit in [("cursor_models", 450), ("other_models", 45), ("overall", 495)]
    }
    email = f"account-{index + 1}@example.test"
    return {
        "id": email, "email": email, "label": label, "department": department,
        "ok": True, "pending": False, "stale": False, "expired": False,
        "ok_at": now().isoformat(), "age": index * 60,
        "data": {
            "email": email, "quota": quota,
            "cycle": {"start": (now() - timedelta(days=20 - index % 10)).isoformat(),
                      "reset_at": (now() + timedelta(days=10 + index % 10)).isoformat()},
            "plan": {"name": "Pro", "membership_type": "pro", "included_usd": 20},
            "spend_usd": {"total": round((100 - remaining) / 100 * 495, 2)},
            "on_demand": {"enabled": False, "used_usd": 0},
            "grok_weekly": {"remaining_pct": 100, "reset_at": (now() + timedelta(days=4)).isoformat()},
        },
    }


NAMES = ["Alex Chen", "Mia Lin", "Evan Wang", "Nora Xu", "Leo Zhang", "Iris Wu",
         "Ryan Liu", "Luna Zhou", "Owen Sun", "Ella Zhao", "Sean Lu", "Aria Tang",
         "Noah Yang", "Ruby Shen", "Eric Gu", "Cora He", "Ian Luo", "Vera Ma"]
DEPARTMENTS = ["Platform", "Product", "Design"]
accounts = [make_account(i, name, DEPARTMENTS[i % 3]) for i, name in enumerate(NAMES)]
next_index = len(accounts)


def find(account_id):
    for account in accounts:
        if account["id"] == account_id:
            return account
    raise HTTPException(404, "Preview account not found")


@app.get("/", response_class=HTMLResponse)
async def index():
    # Version per request: editing CSS/JS should show up on a plain reload, not
    # only after a hard refresh.
    return WEB.joinpath("index.html").read_text().replace(
        "__ASSET_VERSION__", f"preview-{time.time():.0f}"
    ).replace("var el = document.documentElement;", "var el = document.documentElement;\n"
              "    if (!localStorage.getItem('panelSkin')) localStorage.setItem('panelSkin', 'glass');")


@app.get("/api/config")
async def config():
    return {"needs_token": False, "auto_refresh": False, "cycle_seconds": 0}


@app.get("/api/accounts")
@app.get("/api/account-index")
async def account_list(department: str | None = None):
    await asyncio.sleep(.16)
    counts = Counter(a["department"] for a in accounts)
    return {
        "accounts": [a for a in accounts if department is None or a["department"] == department],
        "departments": [{"department": key, "count": count} for key, count in counts.items()],
        "total": len(accounts),
    }


@app.post("/api/accounts/{account_id}/refresh")
async def refresh(account_id: str):
    await asyncio.sleep(1.2)
    account = find(account_id)
    for quota in account["data"]["quota"].values():
        quota["remaining_pct"] = max(0, round(quota["remaining_pct"] - 1.3, 1))
        quota["used_pct"] = 100 - quota["remaining_pct"]
    account["data"]["spend_usd"]["total"] = round(account["data"]["quota"]["overall"]["used_pct"] * 4.95, 2)
    account.update(ok_at=now().isoformat(), age=0)
    return {"account": account}


@app.get("/api/accounts/{account_id}/usage-detail")
async def detail(account_id: str):
    account = find(account_id)
    await asyncio.sleep(.8)
    groups = []
    for key, name, models in [
        # 行数刻意给够：明细弹窗的滚动条让位和"还能往下滚"的提示要有东西才测得出来
        ("cursor_models", "Cursor Models", [
            "Auto", "Composer", "cursor-grok-4.6-high-fast", "cursor-grok-4.6-medium-fast",
            "default", "composer-2.5-fast", "cursor-grok-4.5-high-fast", "cursor-grok-4.6-high"]),
        ("other_models", "Other Models", [
            "claude-opus-5-thinking-high", "claude-sonnet-5", "gpt-5.2-codex",
            "gemini-3-pro", "o4-mini"]),
    ]:
        quota = account["data"]["quota"][key]
        total = round(quota["used_pct"] / 100 * quota["limit_usd"], 2)
        rows = [{"model": model, "input_tokens": 1284000 + i * 220000, "output_tokens": 312000,
                 "cache_write_tokens": 56000, "cache_read_tokens": 4600000,
                 "spend_usd": round(total / len(models), 2)} for i, model in enumerate(models)]
        groups.append({"key": key, "name": name, "models": rows, "spend_usd": total,
                       "total_tokens": sum(sum(row[k] for k in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")) for row in rows)})
    return {"groups": groups, "quota": account["data"]["quota"],
            "cycle_start": account["data"]["cycle"]["start"], "fetched_at": now().isoformat(),
            "totals": {"model_count": sum(len(g["models"]) for g in groups), "total_tokens": sum(g["total_tokens"] for g in groups),
                       "spend_usd": sum(g["spend_usd"] for g in groups)}}


class AddAccount(BaseModel):
    cookie: str
    label: str | None = None
    department: str = ""


@app.post("/api/accounts")
async def add(body: AddAccount):
    global next_index
    if body.cookie != "demo":
        raise HTTPException(400, 'Preview only: use "demo" as the cookie.')
    await asyncio.sleep(.65)
    for existing in accounts:
        if existing["label"] == body.label:
            existing["department"] = body.department
            return {"ok": True, "label": existing["label"], "email": existing["email"], "department": body.department}
    account = make_account(next_index, body.label or "New account", body.department)
    next_index += 1
    accounts.append(account)
    return {"ok": True, "label": account["label"], "email": account["email"], "department": account["department"]}


class Department(BaseModel):
    department: str = ""


@app.patch("/api/accounts/{account_id}/department")
async def move(account_id: str, body: Department):
    account = find(account_id)
    account["department"] = body.department
    return {"ok": True, "department": body.department}


@app.delete("/api/accounts/{account_id}")
async def delete(account_id: str):
    accounts.remove(find(account_id))
    return {"ok": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
