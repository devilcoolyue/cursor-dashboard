"""Web 面板服务端——只做编排：取数逻辑在 client/usage，账号落盘在 store。

    cursor-panel                                        # 本机 :8787
    PANEL_TOKEN=xxx cursor-panel --host 0.0.0.0 --no-open   # 部署

账号怎么进来：用户粘贴 WorkosCursorSessionToken，服务端换取并保存桌面 AT/RT。
服务端本身不碰浏览器，可以跑在无桌面的服务器上。常规接口不返回凭证；
桌面切换命令接口按次返回所选账号的本地脚本。

额度列表只读快照，scheduler 在后台逐账号更新；授权、手动刷新、明细和切换另有
按需回源。请求链路与权限边界见 docs/maintenance.md。
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
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

import uvicorn
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin, pools, snapshot, sessions
from .client import DESKTOP_ENDPOINTS, AuthExpired, RateLimited, fetch_one
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
from .usage import assemble_desktop, assemble_detail, iso_to_dt
from .desktop import DesktopSessionError, build_commands
from .switch_links import SwitchLinks, credential_version, download_command


# ---------- 出站节流 ----------

_request_slots: asyncio.Semaphore | None = None
_pace_lock: asyncio.Lock | None = None
_next_slot = 0.0


async def _pace() -> None:
    """给出站任务分配相隔 REQUEST_MIN_INTERVAL 的时槽，内部重试不重新排队。

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
    """服务端 Cursor 请求入口：先排队拿时槽，再占并发名额。"""
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
        return "expired", "桌面授权已失效，请重新粘贴有效 Cookie 授权"
    if any(isinstance(e, requests.Timeout) for e in errors):
        return "network", "连接 Cursor 超时，稍后会自动重试"
    if any(isinstance(e, requests.ConnectionError) for e in errors):
        return "network", "暂时无法连接 Cursor，稍后会自动重试"
    first = errors[0]
    return "error", f"{type(first).__name__}: 暂时无法更新账号，请稍后重试"


async def refresh_account(acc: dict) -> str | None:
    """回源刷一个账号并写入快照。返回失败类型，成功返回 None（调度器据此调节节奏）。"""
    label = acc.get("label") or "unnamed"
    ident = account_id(acc)
    cookie = acc["cookie"]

    async with snapshot.lock_for(ident):
        try:
            acc = await sessions.ensure_account(acc, fetch_cursor)
            cookie = acc["cookie"]
            label = acc.get("label") or "unnamed"
            raw = await asyncio.gather(
                *(sessions.request_account(acc, fetch_cursor, name) for name in DESKTOP_ENDPOINTS),
                return_exceptions=True,
            )
            errors = [item for item in raw if isinstance(item, BaseException)]
            if not errors:
                sessions.verify_identity(raw[0], email=acc.get("email"), subject=acc["auth_subject"])
        except Exception as exc:
            errors = [exc]
        if errors:
            kind, message = _classify(errors)
            snapshot.record_failure(ident, cookie, kind, message)
            return kind
        snapshot.record_success(ident, cookie, assemble_desktop(label, *raw))
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
# 明细不进后台轮询；同账号在 DETAIL_TTL 内复用结果，Cookie 改变使缓存失效。
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
_switch_links = SwitchLinks()


async def expire_switch_links():
    while True:
        await asyncio.sleep(30)
        _switch_links.prune()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _request_slots, _pace_lock, _next_slot, _scheduler
    initial_password = await asyncio.to_thread(admin.initialize_admin)
    if initial_password:
        print(f"管理员初始密码（仅显示一次）: {initial_password}", flush=True)
    # 同账号的桌面请求共用线程池；后台逐账号处理，不为每个账号嵌套创建线程池。
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

    link_cleanup = asyncio.create_task(expire_switch_links())
    try:
        yield
    finally:
        link_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await link_cleanup
        _switch_links.clear()

    await _scheduler.stop()
    _scheduler = None
    _request_slots = None
    pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cursor 额度面板", lifespan=lifespan)
ADMIN_COOKIE = "cursor_panel_admin"

# 页面全是同源调用，不开 CORS


@app.middleware("http")
async def mark_active(request: Request, call_next):
    """有人在看面板就告诉调度器，别掉进降速档。"""
    is_api = request.url.path.startswith("/api/")
    if is_api and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if ((origin and origin != expected)
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse(status_code=403, content={"detail": "不允许跨站操作"},
                                headers={"Cache-Control": "no-store"})
    raw_session = request.cookies.get(ADMIN_COOKIE, "")
    request.state.admin_session = (
        await asyncio.to_thread(admin.check_session, raw_session) if raw_session and is_api else None
    )
    if (is_api and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path != "/api/admin/login" and request.state.admin_session
            and not secrets.compare_digest(request.headers.get("x-admin-csrf", ""),
                                           request.state.admin_session["csrf_token"])):
        return JSONResponse(status_code=403, content={"detail": "登录校验已变更，请刷新页面后重试"},
                            headers={"Cache-Control": "no-store"})
    if _scheduler is not None and request.url.path.startswith("/api/"):
        _scheduler.touch()
    response = await call_next(request)
    if is_api:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(AccountsError)
async def handle_accounts_error(_req: Request, exc: AccountsError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_req: Request, exc: RequestValidationError):
    # Validation errors must not echo submitted passwords or account credentials.
    return JSONResponse(status_code=422, content={"detail": [
        {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
    ]})


def is_admin(request: Request | None) -> bool:
    return bool(request and getattr(request.state, "admin_session", None))


def require_admin(request: Request) -> dict:
    if not is_admin(request):
        raise HTTPException(401, "请先登录管理员账号")
    return request.state.admin_session


def require_token(x_panel_token: str | None = Header(default=None), request: Request = None) -> None:
    if is_admin(request):
        return
    if PANEL_TOKEN and not secrets.compare_digest(x_panel_token or "", PANEL_TOKEN):
        raise HTTPException(401, "口令不对")


def public_account_view(acc: dict, request: Request | None, snap: dict | None = None,
                        policy: dict | None = None) -> dict:
    result = snapshot.view(acc, account_id(acc), snap)
    result.pop("auth", None)
    result["can_switch"] = admin.switch_allowed(acc, policy if policy is not None else admin.get_policy(),
                                                is_admin=is_admin(request))
    return result


def require_switch(request: Request | None, acc: dict) -> None:
    if not admin.switch_allowed(acc, admin.get_policy(), is_admin=is_admin(request)):
        raise HTTPException(403, "此账号暂未开放本地切换")


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


def account_index(accounts: list[dict], request: Request | None = None) -> list[dict]:
    policy = admin.get_policy()
    return [
        {
            "id": account_id(acc),
            "label": acc.get("label") or "unnamed",
            "email": acc.get("email"),
            "department": acc.get("department") or "",
            "can_switch": admin.switch_allowed(acc, policy, is_admin=is_admin(request)),
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
    return render_page(WEB_INDEX)


def render_page(page):
    html = page.read_text(encoding="utf-8")
    if "__ADMIN_CONTENT__" in html:
        html = html.replace("__ADMIN_CONTENT__", (WEB_DIR / "admin.html").read_text(encoding="utf-8"))
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


@app.get("/admin")
@app.get("/admin/", include_in_schema=False)
def admin_index():
    return render_page(WEB_INDEX)


class AdminLoginReq(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class SwitchPolicyReq(BaseModel):
    all_accounts: bool = Field(default=False, strict=True)
    departments: list[str] = Field(default_factory=list, max_length=1000)
    account_ids: list[int] = Field(default_factory=list, max_length=10000)


@app.get("/api/admin/session")
def api_admin_session(request: Request):
    session = getattr(request.state, "admin_session", None)
    return {"authenticated": True, **session} if session else {"authenticated": False}


@app.post("/api/admin/login")
def api_admin_login(req: AdminLoginReq, request: Request):
    try:
        session = admin.login(req.password, request.client.host if request.client else "unknown")
    except admin.InvalidPassword:
        raise HTTPException(401, "管理员密码不正确") from None
    except admin.LoginThrottled as exc:
        raise HTTPException(429, "尝试次数过多，请稍后重试",
                            headers={"Retry-After": str(exc.retry_after)}) from None
    old_session = request.cookies.get(ADMIN_COOKIE)
    if old_session:
        admin.delete_session(old_session)
    response = JSONResponse({"authenticated": True, "csrf_token": session["csrf_token"],
                             "expires_at": session["expires_at"]})
    response.set_cookie(ADMIN_COOKIE, session["token"], httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/",
                        max_age=max(0, int(session["expires_at"] - time.time())))
    return response


@app.post("/api/admin/logout", dependencies=[Depends(require_admin)])
def api_admin_logout(request: Request):
    admin.delete_session(request.cookies.get(ADMIN_COOKIE, ""))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(ADMIN_COOKIE, path="/", httponly=True, samesite="strict",
                           secure=request.url.scheme == "https")
    return response


def switch_source(acc: dict, policy: dict) -> str:
    if policy["all_accounts"]:
        return "all"
    if (acc.get("department") or "") in policy["departments"]:
        return "department"
    if acc["db_id"] in policy["account_ids"]:
        return "account"
    return "disabled"


@app.get("/api/admin/accounts", dependencies=[Depends(require_admin)])
def api_admin_accounts(q: str = Query(default="", max_length=256),
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=20, ge=1, le=100),
                       department: str | None = Query(default=None, max_length=64)):
    all_accounts = load_accounts()
    selected = accounts_for_department(all_accounts, department)
    query = q.strip().casefold()
    if query:
        selected = [acc for acc in selected if any(
            query in str(acc.get(field) or "").casefold() for field in ("label", "email", "department")
        )]
    total = len(selected)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    policy = admin.get_policy()
    return {
        "accounts": [{"id": account_id(acc), "db_id": acc["db_id"], "label": acc["label"],
                      "email": acc.get("email"), "department": acc.get("department") or "",
                      "auth": admin.credential_view(acc),
                      "switch_enabled": admin.switch_allowed(acc, policy),
                      "switch_source": switch_source(acc, policy)}
                     for acc in selected[(page - 1) * page_size:page * page_size]],
        "total": total, "page": page, "page_size": page_size, "pages": pages,
        "departments": department_counts(all_accounts),
    }


@app.get("/api/admin/switch-policy", dependencies=[Depends(require_admin)])
def api_admin_switch_policy():
    accounts = load_accounts()
    return {"policy": admin.get_policy(), "departments": department_counts(accounts),
            "accounts": [{"id": account_id(acc), "db_id": acc["db_id"], "label": acc["label"],
                          "email": acc.get("email"), "department": acc.get("department") or ""}
                         for acc in accounts]}


@app.put("/api/admin/switch-policy", dependencies=[Depends(require_admin)])
def api_save_switch_policy(req: SwitchPolicyReq):
    accounts = load_accounts()
    departments = {acc.get("department") or "" for acc in accounts}
    account_ids = {acc["db_id"] for acc in accounts}
    if any(dept not in departments for dept in req.departments):
        raise HTTPException(400, "所选部门已变更，请刷新后重试")
    if any(ident not in account_ids or ident <= 0 for ident in req.account_ids):
        raise HTTPException(400, "所选账号已变更，请刷新后重试")
    policy = {"all_accounts": req.all_accounts,
              "departments": list(dict.fromkeys(req.departments)),
              "account_ids": list(dict.fromkeys(req.account_ids))}
    return {"policy": admin.save_policy(policy)}


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
def api_config(request: Request):
    """不鉴权：页面得先知道要不要问口令。"""
    status = _scheduler.status() if _scheduler else {}
    return {
        "needs_token": bool(PANEL_TOKEN) and not is_admin(request),
        "is_admin": is_admin(request),
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
    request: Request,
    department: str | None = Query(default=None, max_length=64),
):
    """只要卡片索引和部门人数，不带额度数据。"""
    all_accounts = load_accounts()
    selected = accounts_for_department(all_accounts, department)
    return {
        "accounts": account_index(selected, request),
        "departments": department_counts(all_accounts),
        "total": len(all_accounts),
    }


@app.get("/api/accounts", dependencies=[Depends(require_token)])
def api_accounts(request: Request, department: str | None = Query(default=None, max_length=64)):
    """一次返回整组卡片，全部读快照，不访问 cursor.com。"""
    all_accounts = load_accounts()
    selected = accounts_for_department(all_accounts, department)
    policy = admin.get_policy()
    return {
        "accounts": [public_account_view(acc, request, policy=policy) for acc in selected],
        "departments": department_counts(all_accounts),
        "total": len(all_accounts),
    }


@app.get("/api/accounts/{account_key}", dependencies=[Depends(require_token)])
def api_account_one(account_key: str, request: Request):
    acc = find_account(account_key)
    return {"account": public_account_view(acc, request)}


@app.post("/api/accounts", dependencies=[Depends(require_token)])
async def api_save(req: SaveReq, request: Request = None):
    """Exchange the submitted cookie and validate desktop queries before committing."""
    cookie = req.cookie.strip()
    if not cookie:
        raise HTTPException(400, "cookie 为空")
    label = req.label or ""
    try:
        session, email = await sessions.exchange_cookie(cookie, label, fetch_cursor)
        raw = await asyncio.gather(
            *(fetch_cursor("", label, name, session.token) for name in DESKTOP_ENDPOINTS),
            return_exceptions=True,
        )
        errors = [item for item in raw if isinstance(item, BaseException)]
        if not errors:
            sessions.verify_identity(raw[0], email=email, subject=session.subject)
    except Exception as exc:
        errors = [exc]
    if errors:
        kind, message = _classify(errors)
        if kind == "rate_limited":
            # 这里最容易冤枉用户：过去 403 一律当失效，会让人反复重粘好 cookie
            raise HTTPException(503, "Cursor 暂时限制了请求，等一两分钟再保存；cookie 可能是好的")
        if kind == "expired":
            raise HTTPException(400, "这个 cookie 是失效的，请在浏览器重新登录 cursor.com 后再回填")
        raise HTTPException(400, "无法完成桌面授权或额度校验，请稍后重试。")

    data = assemble_desktop(label, *raw)
    async with snapshot.lock_for(email):
        if request is not None:
            request.state.admin_session = await asyncio.to_thread(
                admin.check_session, request.cookies.get(ADMIN_COOKIE, "")
            )
            require_token(request.headers.get("x-panel-token"), request)
        acc = await asyncio.to_thread(
            upsert_account, cookie, email, req.label,
            req.department, session=session
        )
        snapshot.record_success(account_id(acc), cookie, data)
    return {
        "ok": True,
        "label": acc["label"],
        "email": acc.get("email"),
        "department": acc.get("department") or "",
    }


@app.post("/api/accounts/{account_key}/switch-command", dependencies=[Depends(require_token)])
async def api_switch_command(account_key: str, request: Request):
    acc = find_account(account_key)
    require_switch(request, acc)
    if not take_manual_token():
        raise HTTPException(429, "操作太频繁，请稍后再生成命令。")
    try:
        acc = await sessions.ensure_account(acc, fetch_cursor)
        me = await sessions.request_account(acc, fetch_cursor, "desktop_me")
        acc = await sessions.ensure_account(acc, fetch_cursor)
        email = sessions.verify_identity(me, email=acc.get("email"), subject=acc["auth_subject"])
    except AuthExpired:
        raise HTTPException(400, "桌面授权已失效，请先重新授权这个账号。") from None
    except DesktopSessionError as exc:
        raise HTTPException(400, str(exc)) from None
    except RateLimited:
        raise HTTPException(503, "Cursor 暂时限制了请求，请稍后再生成命令。") from None
    except Exception:
        raise HTTPException(502, "无法验证桌面会话，请稍后重试。") from None
    # A network round trip may outlive an admin session or a permission change.
    if request is not None:
        request.state.admin_session = await asyncio.to_thread(
            admin.check_session, request.cookies.get(ADMIN_COOKIE, "")
        )
        require_token(request.headers.get("x-panel-token"), request)
    current = find_account(account_key)
    require_switch(request, current)
    if credential_version(current) != credential_version(acc):
        raise HTTPException(409, "账号授权已变更，请重新生成命令。")
    try:
        result = build_commands(sessions.session_from_account(acc), email)
    except DesktopSessionError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        token, expiry = _switch_links.issue(account_key, acc, result,
            request.cookies.get(ADMIN_COOKIE, "") if is_admin(request) else "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from None
    for platform, item in result["commands"].items():
        url = str(request.url_for("api_switch_script", token=token, platform=platform))
        item["command"] = download_command(url, platform)
    result["download_expires_at"] = expiry
    result.update(label=acc.get("label") or email, email=email)
    return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@app.get("/api/s/{token}/{platform}")
def api_switch_script(token: str, platform: str):
    # The random link is the download credential; terminal requests have no browser cookies.
    entry = _switch_links.consume(token, platform)
    if entry is None:
        raise HTTPException(410, "下载链接已失效或已使用，请回到面板重新生成命令。")
    account = find_account(entry.account_key)
    if credential_version(account) != entry.version:
        raise HTTPException(410, "账号授权已变更，请回到面板重新生成命令。")
    administrator = bool(entry.admin_session and admin.check_session(entry.admin_session))
    if ((entry.admin_session and not administrator)
            or not admin.switch_allowed(account, admin.get_policy(), is_admin=administrator)):
        raise HTTPException(403, "切换权限已失效，请回到面板重新生成命令。")
    if entry.expires_at <= time.time():
        raise HTTPException(410, "下载链接已过期，请回到面板重新生成命令。")
    return PlainTextResponse(entry.scripts[platform], headers={
        "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
        "Content-Disposition": 'attachment; filename="switch-account.' + ("sh" if platform == "macos" else "ps1") + '"',
    })


@app.post("/api/accounts/{account_key}/refresh", dependencies=[Depends(require_token)])
async def api_refresh_one(account_key: str, request: Request):
    """卡片上的刷新按钮：立刻回源一次，但要过冷却和令牌桶。"""
    acc = find_account(account_key)
    snap = snapshot.get(account_key, acc["cookie"])

    if (snap["ok_at"] and not snap["error"]
            and time.time() - snap["attempted_at"] < MANUAL_COOLDOWN):
        return {"account": public_account_view(acc, request, snap),
                "notice": "刚更新过，显示的就是最新数据"}
    if not take_manual_token():
        return {"account": public_account_view(acc, request, snap),
                "notice": "刷新太频繁了，等几秒再点；后台本来也在自动更新"}

    await refresh_account(acc)
    return {"account": public_account_view(find_account(account_key), request)}


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
        raw = await sessions.request_account(acc, fetch_cursor, "desktop_aggregated",
                                 int(start.timestamp() * 1000),
                                 int(now.timestamp() * 1000))
    except AuthExpired:
        raise HTTPException(400, "桌面授权已失效，点钥匙图标重新粘贴有效 Cookie 授权")
    except RateLimited:
        raise HTTPException(503, "Cursor 暂时限制了请求，等一会儿再看；cookie 可能是好的")
    except (requests.RequestException, DesktopSessionError, AccountsError):
        raise HTTPException(502, "连接 Cursor 或更新桌面凭证失败，请稍后重试。") from None

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
