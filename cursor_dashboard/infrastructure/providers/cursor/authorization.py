"""Cursor authorization protocol; no persistence or runtime configuration."""
from __future__ import annotations
import asyncio
import requests
from ....client import AuthExpired
from ....desktop import DesktopSessionError, desktop_session, login_challenge, parse_session


def verify_identity(data, *, email=None, subject=None):
    actual_email = data.get("email") if isinstance(data, dict) else None
    actual_subject = (data.get("authId") or data.get("sub")) if isinstance(data, dict) else None
    if not isinstance(actual_email, str) or not actual_email.strip():
        raise AuthExpired("会话已失效，请重新登录网页版并更新 Cookie。")
    if email and actual_email.casefold() != email.casefold():
        raise DesktopSessionError("凭证与所选账号的邮箱不一致。")
    if subject and (not isinstance(actual_subject, str)
                    or actual_subject.removeprefix("auth0|") != subject.removeprefix("auth0|")):
        raise DesktopSessionError("凭证与所选账号的身份不一致。")
    return actual_email


async def exchange_cookie(cookie, label, fetch, *, expected_email=None):
    source = parse_session(cookie)
    me = await fetch(cookie, label, "me")
    email = verify_identity(me, email=expected_email, subject=source.subject)
    flow, verifier, challenge = login_challenge()
    await fetch(cookie, label, "desktop_callback", flow, challenge)
    for attempt in range(5):
        try:
            data = await fetch("", label, "desktop_poll", flow, verifier)
            session = desktop_session(data, source.subject)
            break
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            if attempt == 4:
                raise DesktopSessionError("Cursor 尚未签发桌面凭证，请稍后重试。") from None
            await asyncio.sleep(.5)
    identity = await fetch("", label, "desktop_me", session.token)
    verify_identity(identity, email=email, subject=session.subject)
    return session, email
