"""Web 面板服务端——只做编排：取数逻辑在 client/usage，账号落盘在 store。

    cursor-panel                                        # 本机 :8787
    PANEL_TOKEN=xxx cursor-panel --host 0.0.0.0 --no-open   # 部署

账号怎么进来：用户在页面粘贴 WorkosCursorSessionToken，服务端验活后落盘。
服务端本身不碰浏览器，可以跑在无桌面的服务器上，且从不把 cookie 回传给前端。

**页面读的是快照，不是实时回源。** 回源由 scheduler 在后台一个一个账号慢慢做，
页面只负责把最后一次统计结果显示出来。原因见 scheduler 的模块注释。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import secrets
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pools, snapshot
from .client import ENDPOINTS, AuthExpired, RateLimited, fetch_one
from .config import (
    DATABASE_PATH,
    DETAIL_TTL,
    MANUAL_BURST,
    MANUAL_COOLDOWN,
    MAX_WORKERS,
    PANEL_TOKEN,
    REFRESH_ENABLED,
    REQUEST_CONCURRENCY,
    REQUEST_MIN_INTERVAL,
    WEB_DIR,
    WEB_INDEX,
)
from .scheduler import Scheduler
from .store import (
    AccountsError,
    account_id,
    delete_account,
    load_accounts,
    update_account_department,
    upsert_account,
)
from .usage import assemble, assemble_detail, iso_to_dt


# ---------- 出站节流 ----------

_request_slots: asyncio.Semaphore | None = None
_pace_lock: asyncio.Lock | None = None
_next_slot = 0.0


async def _pace() -> None:
    """给每个出站请求分配一个时槽，保证任意两次之间至少隔 REQUEST_MIN_INTERVAL。

    信号量限的是并发，不是速率——接口够快时 3 个并发照样能打出几十 QPS，而边缘
    防护看的就是速率。所以真正的闸门在这里。锁内只算时槽、锁外再睡，避免把等待
    时间叠加到锁的持有上。
    """
    global _next_slot
    if REQUEST_MIN_INTERVAL <= 0:
        return
    async with _pacer_lock():
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + REQUEST_MIN_INTERVAL + random.uniform(
            0, REQUEST_MIN_INTERVAL * 0.2
        )
    delay = slot - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)


def _pacer_lock() -> asyncio.Lock:
    global _pace_lock
    if _pace_lock is None:
        _pace_lock = asyncio.Lock()
    return _pace_lock


async def fetch_cursor(cookie: str, label: str, name: str, *args):
    """所有访问 cursor.com 的路径都过这里：先排队拿时槽，再占并发名额。"""
    await _pace()
    if _request_slots is None:
        return await asyncio.to_thread(fetch_one, cookie, label, name, *args)
    async with _request_slots:
        return await asyncio.to_thread(fetch_one, cookie, label, name, *args)


# ---------- 回源 ----------

def _classify(errors: list[BaseException]) -> tuple[str, str]:
    """把一组接口异常归成一种卡片状态。

    **限流优先于失效**：几个接口里混着 401 和 403 拦截页时按限流处理。宁可多等
    一轮，也不能误报"cookie 失效"——那会让用户去重新粘贴 cookie，而那次粘贴同样
    会被挡住，看起来就像新 cookie 也不管用。
    """
    if any(isinstance(e, RateLimited) for e in errors):
        return "rate_limited", "Cursor 暂时限制了请求，稍后会自动重试"
    if any(isinstance(e, AuthExpired) for e in errors):
        return "expired", "会话已失效，请重新粘贴 cookie"
    if any(isinstance(e, requests.Timeout) for e in errors):
        return "network", "连接 Cursor 超时，稍后会自动重试"
    if any(isinstance(e, requests.ConnectionError) for e in errors):
        return "network", "暂时无法连接 Cursor，稍后会自动重试"
    first = errors[0]
    return "error", f"{type(first).__name__}: {first}"


async def refresh_account(acc: dict) -> str | None:
    """回源刷一个账号并写入快照。返回失败类型，成功返回 None（调度器据此调节节奏）。"""
    label = acc.get("label") or "unnamed"
    ident = account_id(acc)
    cookie = acc["cookie"]

    async with snapshot.lock_for(ident):
        # return_exceptions：让 5 个接口都跑完再判定，否则先抛出的那个说了算，
        # 混合错误时容易把限流认成失效
        raw = await asyncio.gather(
            *(fetch_cursor(cookie, label, name) for name in ENDPOINTS),
            return_exceptions=True,
        )
        errors = [item for item in raw if isinstance(item, BaseException)]
        if errors:
            kind, message = _classify(errors)
            snapshot.record_failure(ident, cookie, kind, message)
            return kind
        snapshot.record_success(ident, cookie, assemble(label, *raw))
        return None


# ---------- 手动刷新的闸门 ----------

_manual_tokens = float(MANUAL_BURST)
_manual_refilled = time.monotonic()
_manual_lock = threading.Lock()


def take_manual_token() -> bool:
    """单卡刷新的令牌桶。保留单卡刷新，但拦住"挨个点一整屏卡片"这种新洪峰。"""
    global _manual_tokens, _manual_refilled
    rate = MANUAL_BURST / MANUAL_COOLDOWN if MANUAL_COOLDOWN > 0 else float("inf")
    with _manual_lock:
        now = time.monotonic()
        _manual_tokens = min(MANUAL_BURST, _manual_tokens + (now - _manual_refilled) * rate)
        _manual_refilled = now
        if _manual_tokens < 1:
            return False
        _manual_tokens -= 1
        return True


# ---------- 按模型明细 ----------
# 明细是点开卡片才拉的，不进后台轮询——42 个账号全量拉一遍就是又一次 42 个请求的
# 洪峰，正是 CLAUDE.md 里反复交代不能造的那种。这里只挡住"同一张卡连点几下"，
# 保证每次点开拿到的都是刚从 cursor.com 取回来的数。
_details: dict[str, tuple[float, dict]] = {}
_details_lock = threading.Lock()


def cached_detail(ident: str, fp: str) -> dict | None:
    with _details_lock:
        entry = _details.get(ident)
    if not entry:
        return None
    stored_at, detail = entry
    # cookie 换过就作废，和快照一个道理：旧会话的数据不能挂在新会话上
    if detail.get("fingerprint") != fp or time.time() - stored_at > DETAIL_TTL:
        return None
    return detail


def store_detail(ident: str, detail: dict) -> dict:
    with _details_lock:
        _details[ident] = (time.time(), detail)
    return detail


# ---------- HTTP ----------

_scheduler: Scheduler | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _request_slots, _pace_lock, _next_slot, _scheduler
    # N 账号 × 4 接口打平成一个任务集，共用这一个池。不要改成嵌套线程池——线程数会乘起来。
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="probe")
    asyncio.get_running_loop().set_default_executor(pool)
    _request_slots = asyncio.Semaphore(REQUEST_CONCURRENCY)
    _pace_lock = None
    _next_slot = 0.0

    restored = snapshot.load()
    _scheduler = Scheduler(refresh_account)
    if REFRESH_ENABLED:
        _scheduler.start()
    print(f"已载入 {restored} 份快照   "
          f"后台刷新: {'开启' if REFRESH_ENABLED else '关闭'}", flush=True)

    yield

    await _scheduler.stop()
    _scheduler = None
    _request_slots = None
    pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cursor 额度面板", lifespan=lifespan)

# 页面全是同源调用，不开 CORS


@app.middleware("http")
async def mark_active(request: Request, call_next):
    """有人在看面板就告诉调度器，别掉进降速档。"""
    if _scheduler is not None and request.url.path.startswith("/api/"):
        _scheduler.touch()
    return await call_next(request)


@app.exception_handler(AccountsError)
async def handle_accounts_error(_req: Request, exc: AccountsError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def require_token(x_panel_token: str | None = Header(default=None)) -> None:
    if PANEL_TOKEN and not secrets.compare_digest(x_panel_token or "", PANEL_TOKEN):
        raise HTTPException(401, "口令不对")


class SaveReq(BaseModel):
    cookie: str
    label: str | None = None
    department: str | None = Field(default=None, max_length=64)


class DepartmentReq(BaseModel):
    department: str = Field(default="", max_length=64)


def accounts_for_department(accounts: list[dict], department: str | None) -> list[dict]:
    if department is None:
        return accounts
    wanted = department.strip()
    return [acc for acc in accounts if (acc.get("department") or "") == wanted]


def account_index(accounts: list[dict]) -> list[dict]:
    return [
        {
            "id": account_id(acc),
            "label": acc.get("label") or "unnamed",
            "email": acc.get("email"),
            "department": acc.get("department") or "",
        }
        for acc in accounts
    ]


def department_counts(accounts: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for acc in accounts:
        department = acc.get("department") or ""
        counts[department] = counts.get(department, 0) + 1
    return [
        {"department": department, "count": count}
        for department, count in counts.items()
    ]


def find_account(account_key: str) -> dict:
    acc = next((a for a in load_accounts() if account_id(a) == account_key), None)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc


@app.get("/")
def index():
    html = WEB_INDEX.read_text(encoding="utf-8")
    revision = hashlib.sha256(html.encode("utf-8"))
    # 每次读取当前文件内容，静态文件部署后无需重启也能换资源地址。
    for asset in sorted(WEB_DIR.rglob("*")):
        if asset.is_file() and asset.suffix in {".css", ".js"}:
            revision.update(asset.relative_to(WEB_DIR).as_posix().encode("utf-8"))
            revision.update(asset.read_bytes())
    return HTMLResponse(
        html.replace("__ASSET_VERSION__", revision.hexdigest()[:16]),
        headers={"Cache-Control": "no-cache"},
    )


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# 样式和脚本走静态托管。**这里不能挂在 "/" 上**——那会把 /api/* 一起吃掉。
# 不鉴权是有意的：CSS/JS 里没有任何账号数据，鉴权只在 /api/* 这一层；
# 真要藏起整个面板，PANEL_TOKEN 拦住 /api/* 就够了，页面本身没东西可看。
app.mount("/static", RevalidatingStaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/config")
def api_config():
    """不鉴权：页面得先知道要不要问口令。"""
    status = _scheduler.status() if _scheduler else {}
    return {
        "needs_token": bool(PANEL_TOKEN),
        "auto_refresh": bool(status.get("enabled")),
        "cycle_seconds": status.get("cycle_seconds", 0),
    }


@app.get("/api/status", dependencies=[Depends(require_token)])
def api_status():
    """后台刷新的运行状况，排查限流时看这个。"""
    status = _scheduler.status() if _scheduler else {"enabled": False}
    # 每个套餐有几个账号在支撑额度池表——用满的卡片显示不出上限时先看这里
    return {**status, "plan_pools": pools.snapshot_state()}


@app.get("/api/account-index", dependencies=[Depends(require_token)])
def api_account_index(
    department: str | None = Query(default=None, max_length=64),
):
    """只要卡片索引和部门人数，不带额度数据。"""
    all_accounts = load_accounts()
    selected = accounts_for_department(all_accounts, department)
    return {
        "accounts": account_index(selected),
        "departments": department_counts(all_accounts),
        "total": len(all_accounts),
    }


@app.get("/api/accounts", dependencies=[Depends(require_token)])
def api_accounts(department: str | None = Query(default=None, max_length=64)):
    """一次返回整组卡片，全部读快照，不访问 cursor.com。"""
    all_accounts = load_accounts()
    selected = accounts_for_department(all_accounts, department)
    return {
        "accounts": [snapshot.view(acc, account_id(acc)) for acc in selected],
        "departments": department_counts(all_accounts),
        "total": len(all_accounts),
    }


@app.get("/api/accounts/{account_key}", dependencies=[Depends(require_token)])
def api_account_one(account_key: str):
    acc = find_account(account_key)
    return {"account": snapshot.view(acc, account_key)}


@app.post("/api/accounts", dependencies=[Depends(require_token)])
async def api_save(req: SaveReq):
    """粘贴进来的 cookie 先验活再落盘。"""
    cookie = req.cookie.strip()
    if not cookie:
        raise HTTPException(400, "cookie 为空")
    label = req.label or ""
    raw = await asyncio.gather(
        *(fetch_cursor(cookie, label, name) for name in ENDPOINTS),
        return_exceptions=True,
    )
    errors = [item for item in raw if isinstance(item, BaseException)]
    if errors:
        kind, message = _classify(errors)
        if kind == "rate_limited":
            # 这里最容易冤枉用户：过去 403 一律当失效，会让人反复重粘好 cookie
            raise HTTPException(503, "Cursor 暂时限制了请求，等一两分钟再保存；cookie 可能是好的")
        if kind == "expired":
            raise HTTPException(400, "这个 cookie 是失效的，请在浏览器重新登录 cursor.com 后再回填")
        raise HTTPException(400, f"校验失败：{message}")

    data = assemble(label, *raw)
    acc = await asyncio.to_thread(
        upsert_account, cookie, data.get("email"), req.label, req.department
    )
    # 顺手把刚拿到的数据存成快照，不用等后台轮到它
    snapshot.record_success(account_id(acc), cookie, data)
    return {
        "ok": True,
        "label": acc["label"],
        "email": acc.get("email"),
        "department": acc.get("department") or "",
    }


@app.post("/api/accounts/{account_key}/refresh", dependencies=[Depends(require_token)])
async def api_refresh_one(account_key: str):
    """卡片上的刷新按钮：立刻回源一次，但要过冷却和令牌桶。"""
    acc = find_account(account_key)
    snap = snapshot.get(account_key, acc["cookie"])

    if (snap["ok_at"] and not snap["error"]
            and time.time() - snap["attempted_at"] < MANUAL_COOLDOWN):
        return {"account": snapshot.view(acc, account_key, snap),
                "notice": "刚更新过，显示的就是最新数据"}
    if not take_manual_token():
        return {"account": snapshot.view(acc, account_key, snap),
                "notice": "刷新太频繁了，等几秒再点；后台本来也在自动更新"}

    await refresh_account(acc)
    return {"account": snapshot.view(acc, account_key)}


@app.get("/api/accounts/{account_key}/usage-detail",
         dependencies=[Depends(require_token)])
async def api_usage_detail(account_key: str):
    """本账单周期内按模型的 token 与花费。点开卡片才会来这儿。

    窗口起点直接用快照里已经算好的 cycle.start，所以只多打一个接口而不是两个。
    """
    acc = find_account(account_key)
    ident = account_id(acc)
    cookie = acc["cookie"]
    snap = snapshot.get(ident, cookie)
    data = snap["data"] or {}
    start = iso_to_dt((data.get("cycle") or {}).get("start"))
    if not start:
        raise HTTPException(409, "还没拿到这个账号的账单周期，等后台刷新到它再看明细")

    cached = cached_detail(ident, snap["fingerprint"])
    if cached:
        return cached
    if not take_manual_token():
        raise HTTPException(429, "查询太频繁了，等几秒再点")

    now = datetime.now(timezone.utc)
    try:
        raw = await fetch_cursor(cookie, acc.get("label") or "unnamed",
                                 "aggregated_usage",
                                 int(start.timestamp() * 1000),
                                 int(now.timestamp() * 1000))
    except AuthExpired:
        raise HTTPException(400, "会话已失效，点卡片右上角的钥匙图标重新粘贴 cookie")
    except RateLimited:
        raise HTTPException(503, "Cursor 暂时限制了请求，等一会儿再看；cookie 可能是好的")
    except requests.RequestException as exc:
        raise HTTPException(502, f"连接 Cursor 失败：{exc}")

    detail = assemble_detail(raw)
    detail.update({
        "id": ident,
        "label": acc.get("label") or "unnamed",
        "email": data.get("email") or acc.get("email"),
        "quota": data.get("quota") or {},
        "cycle_start": (data.get("cycle") or {}).get("start"),
        "fetched_at": now.isoformat(),
        "fingerprint": snap["fingerprint"],
    })
    return store_detail(ident, detail)


@app.patch(
    "/api/accounts/{account_key}/department", dependencies=[Depends(require_token)]
)
async def api_update_department(account_key: str, req: DepartmentReq):
    acc = await asyncio.to_thread(
        update_account_department, account_key, req.department
    )
    if not acc:
        raise HTTPException(404, "账号不存在")
    return {"ok": True, "department": acc.get("department") or ""}


@app.delete("/api/accounts/{account_key}", dependencies=[Depends(require_token)])
def api_delete(account_key: str):
    if not delete_account(account_key):
        raise HTTPException(404, "账号不存在")
    snapshot.drop(account_key)
    with _details_lock:
        _details.pop(account_key, None)
    return {"ok": True}


def main():
    ap = argparse.ArgumentParser(description="Cursor 额度面板")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="部署到公网时才改 0.0.0.0，且必须同时设 PANEL_TOKEN")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost") and not PANEL_TOKEN:
        print("⚠ 对外监听但没设 PANEL_TOKEN：任何人都能读写你的账号库。"
              "先 export PANEL_TOKEN=<随机串> 再启动。", flush=True)

    url = f"http://{args.host}:{args.port}/"
    if not args.no_open and args.host in ("127.0.0.1", "localhost"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"面板: {url}   账号库: {DATABASE_PATH}   "
          f"口令: {'已启用' if PANEL_TOKEN else '未启用'}   "
          f"线程池: {MAX_WORKERS}   Cursor 并发: {REQUEST_CONCURRENCY}   "
          f"出站间隔: {REQUEST_MIN_INTERVAL}s", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
