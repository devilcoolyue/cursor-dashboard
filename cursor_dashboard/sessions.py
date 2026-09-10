"""Desktop credential exchange/refresh shared by the web server and CLI."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time

import requests

from . import store
from .client import AuthExpired
from .config import TOKEN_REFRESH_MARGIN
from .desktop import DesktopSession, DesktopSessionError, desktop_session, login_challenge, parse_session, refreshed_session


def session_from_account(account):
    return DesktopSession(account["access_token"], account["auth_subject"], account["token_expires_at"],
                          account["refresh_token"], "session")


from .infrastructure.providers.cursor.authorization import verify_identity, exchange_cookie


def _usable(account, rejected_token):
    lifetime = account.get("token_expires_at", 0) - account.get("auth_refreshed_at", 0)
    margin = min(TOKEN_REFRESH_MARGIN, max(30, lifetime * .2))
    return (account.get("access_token") and account.get("refresh_token") and not account.get("auth_invalid")
            and account.get("token_expires_at", 0) > time.time() + margin
            and (rejected_token is None or account["access_token"] != rejected_token))


async def ensure_account(account, fetch, *, rejected_token=None):
    """Reuse credentials, or exclusively renew/migrate them without holding a SQLite transaction."""
    key = store.account_id(account)
    owner = secrets.token_hex(16)
    deadline = time.monotonic() + 180
    while True:
        current = await asyncio.to_thread(store.get_account, key)
        if current is None:
            raise store.AccountsError("账号已被删除。")
        if current.get("auth_invalid"):
            raise AuthExpired("桌面会话已被撤销，请重新授权。")
        if _usable(current, rejected_token):
            return current
        if await asyncio.to_thread(store.claim_auth_lease, key, owner):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("账号凭证正在更新，请稍后重试。")
        await asyncio.sleep(.15)
    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(store.renew_auth_lease, key, owner)

    keepalive = asyncio.create_task(heartbeat())
    try:
        current = await asyncio.to_thread(store.get_account, key)
        if current is None:
            raise store.AccountsError("账号已被删除。")
        if _usable(current, rejected_token):
            return current
        if current.get("auth_invalid"):
            raise AuthExpired("桌面会话已被撤销，请重新授权。")
        label = current.get("label") or key
        try:
            if current.get("refresh_token"):
                data = await fetch("", label, "desktop_refresh", current["refresh_token"])
                if isinstance(data, dict) and data.get("shouldLogout") is True:
                    raise AuthExpired("桌面会话已被撤销，请重新授权。")
                session = refreshed_session(data, current["auth_subject"])
            else:
                session, _ = await exchange_cookie(current["cookie"], label, fetch, expected_email=current.get("email"))
        except AuthExpired:
            if current.get("refresh_token"):
                changed = await asyncio.to_thread(store.update_session, current, invalid=True)
                if not changed:
                    return await _latest(key)
            raise
        except DesktopSessionError:
            if not current.get("refresh_token"):
                raise AuthExpired("旧账号无法迁移桌面授权，请重新粘贴有效 Cookie。") from None
            raise
        await asyncio.to_thread(store.update_session, current, session)
        return await _latest(key)
    finally:
        keepalive.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await keepalive
        await asyncio.to_thread(store.release_auth_lease, key, owner)


async def _latest(key):
    latest = await asyncio.to_thread(store.get_account, key)
    if latest is None:
        raise store.AccountsError("账号已被删除。")
    if latest.get("auth_invalid") or not latest.get("refresh_token"):
        raise AuthExpired("桌面授权已变更，请重新授权。")
    return latest


async def request_account(account, fetch, name, *args):
    current = await ensure_account(account, fetch)
    label = current.get("label") or store.account_id(current)
    try:
        return await fetch("", label, name, current["access_token"], *args)
    except AuthExpired:
        current = await ensure_account(current, fetch, rejected_token=current["access_token"])
        return await fetch("", label, name, current["access_token"], *args)
