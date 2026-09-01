"""Web 面板服务端——只做编排：取数逻辑在 client/usage，账号落盘在 store。

    cursor-panel                                        # 本机 :8787
    PANEL_TOKEN=xxx cursor-panel --host 0.0.0.0 --no-open   # 部署

账号怎么进来：用户在页面粘贴 WorkosCursorSessionToken，服务端验活后落盘。
服务端本身不碰浏览器，可以跑在无桌面的服务器上，且从不把 cookie 回传给前端。
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import cache
from .client import ENDPOINTS, AuthExpired, fetch_one
from .config import CACHE_TTL, DATABASE_PATH, MAX_WORKERS, PANEL_TOKEN, WEB_INDEX
from .store import AccountsError, account_id, delete_account, load_accounts, upsert_account
from .usage import assemble


# ---------- 取数 ----------

async def probe(acc: dict, force: bool = False) -> dict:
    """拉一个账号的额度。5 个接口并发发出，绝不回传 cookie。"""
    label = acc.get("label") or "unnamed"
    ident = account_id(acc)
    cookie = acc["cookie"]

    if not force:
        cached = cache.get(ident, cookie)
        if cached:
            return cached

    t0 = time.time()
    async with cache.lock_for(ident):
        # 等锁期间别人可能刚回源完，直接复用他的结果（强制刷新同样适用）
        fresh = cache.since(ident, cookie, t0)
        if fresh:
            return fresh
        base = {"id": ident, "label": label, "email": acc.get("email")}
        try:
            raw = await asyncio.gather(
                *(asyncio.to_thread(fetch_one, cookie, label, name) for name in ENDPOINTS)
            )
            data = assemble(label, *raw)
            result = {**base, "email": data.get("email"), "ok": True, "data": data}
        except AuthExpired as e:
            result = {**base, "ok": False, "expired": True, "error": str(e)}
        except Exception as e:
            result = {**base, "ok": False, "expired": False,
                      "error": f"{type(e).__name__}: {e}"}
        cache.put(ident, cookie, result)
        return {**result, "age": 0.0}


# ---------- HTTP ----------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # N 账号 × 5 接口打平成一个任务集，共用这一个池。不要改成嵌套线程池——线程数会乘起来。
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="probe")
    asyncio.get_running_loop().set_default_executor(pool)
    yield
    pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Cursor 额度面板", lifespan=lifespan)

# 页面全是同源调用，不开 CORS


@app.exception_handler(AccountsError)
async def handle_accounts_error(_req: Request, exc: AccountsError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def require_token(x_panel_token: str | None = Header(default=None)) -> None:
    if PANEL_TOKEN and not secrets.compare_digest(x_panel_token or "", PANEL_TOKEN):
        raise HTTPException(401, "口令不对")


class SaveReq(BaseModel):
    cookie: str
    label: str | None = None


@app.get("/")
def index():
    return FileResponse(WEB_INDEX)


@app.get("/api/config")
def api_config():
    """不鉴权：页面得先知道要不要问口令。"""
    return {"needs_token": bool(PANEL_TOKEN), "cache_ttl": CACHE_TTL}


@app.get("/api/accounts", dependencies=[Depends(require_token)])
async def api_accounts(force: bool = False):
    """force=1 跳过缓存强制回源；页面的「刷新」按钮走这个。"""
    accounts = load_accounts()
    if not accounts:
        return {"accounts": []}
    results = await asyncio.gather(*(probe(a, force) for a in accounts))
    return {"accounts": list(results)}


@app.post("/api/accounts", dependencies=[Depends(require_token)])
async def api_save(req: SaveReq):
    """粘贴进来的 cookie 先验活再落盘。"""
    cookie = req.cookie.strip()
    if not cookie:
        raise HTTPException(400, "cookie 为空")
    label = req.label or ""
    try:
        raw = await asyncio.gather(
            *(asyncio.to_thread(fetch_one, cookie, label, name) for name in ENDPOINTS)
        )
        data = assemble(label, *raw)
    except AuthExpired:
        raise HTTPException(400, "这个 cookie 是失效的，请在浏览器重新登录 cursor.com 后再回填")
    except Exception as e:
        raise HTTPException(400, f"校验失败：{type(e).__name__}: {e}")
    acc = await asyncio.to_thread(upsert_account, cookie, data.get("email"), req.label)
    cache.drop(account_id(acc))     # 换了 cookie，旧结果作废
    return {"ok": True, "label": acc["label"], "email": acc.get("email")}


@app.post("/api/accounts/{account_key}/refresh", dependencies=[Depends(require_token)])
async def api_refresh_one(account_key: str):
    """只刷一个账号，卡片上的刷新按钮走这个。"""
    acc = next((a for a in load_accounts() if account_id(a) == account_key), None)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return {"account": await probe(acc, force=True)}


@app.delete("/api/accounts/{account_key}", dependencies=[Depends(require_token)])
def api_delete(account_key: str):
    if not delete_account(account_key):
        raise HTTPException(404, "账号不存在")
    cache.drop(account_key)
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
          f"线程池: {MAX_WORKERS}   缓存: {CACHE_TTL}s", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
