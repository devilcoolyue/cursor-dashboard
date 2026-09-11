"""Browser approval and single-use S256 exchange for native device sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid
from urllib.parse import urlencode, urlsplit

from sqlalchemy import delete, select

from ..domain.core import Actor, Conflict, Forbidden, NotFound, Unauthenticated
from ..infrastructure.persistence.models import DeviceAuthorization, UserSession
from ..infrastructure.persistence.policy import active_user, audit
from .security import digest


def redirect_uri(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                and parsed.netloc == f"127.0.0.1:{parsed.port}" and parsed.path == "/callback"
                and not parsed.query and not parsed.fragment):
            return value
    except ValueError:
        pass
    raise Conflict("Device callback must be an exact IPv4 loopback callback")


def challenge(verifier):
    if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise Conflict("Invalid PKCE verifier")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


class DeviceService:
    ttl = 30 * 86400

    def __init__(self, repository, identity):
        self.db, self.identity = repository.db, identity

    def require_kind(self, actor, kind):
        with self.db.transaction() as session:
            active_user(session, actor)
            record = session.get(UserSession, actor.session_id) if actor.session_id else None
            if record is None or record.kind != kind:
                raise Forbidden("This operation requires a " + kind + " session")

    def authorize(self, actor, *, code_challenge, state, callback, device_id, device_name):
        callback = redirect_uri(callback)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", state):
            raise Conflict("Invalid device authorization parameters")
        try:
            device_id = str(uuid.UUID(device_id))
        except (ValueError, TypeError, AttributeError):
            raise Conflict("Invalid device identifier") from None
        if not isinstance(device_name, str) or not 1 <= len(device_name.strip()) <= 128:
            raise Conflict("Device name must contain 1 to 128 characters")
        self.identity.limiter.check((("device-authorize", actor.user_id), 20, 300))
        with self.db.transaction(write=True) as session:
            active_user(session, actor)
            browser = session.get(UserSession, actor.session_id) if actor.session_id else None
            if browser is None or browser.kind != "web":
                raise Forbidden("Browser approval is required")
            now = time.time()
            session.execute(delete(DeviceAuthorization).where(DeviceAuthorization.expires_at <= now))
            code = secrets.token_urlsafe(32)
            session.add(DeviceAuthorization(token_hash=digest(code), session_id=browser.id,
                challenge=code_challenge, redirect_uri=callback, device_id=device_id,
                device_name=device_name.strip(), expires_at=min(now + 120, browser.expires_at)))
            audit(session, actor, "device.approve", resource_id=device_id)
            return {"callback_url": callback + "?" + urlencode({"code": code, "state": state})}

    def exchange(self, *, code, verifier, callback, device_id, source="native", request_id=None):
        self.identity.limiter.check((("device-exchange", source), 30, 300))
        callback, supplied = redirect_uri(callback), challenge(verifier)
        with self.db.transaction(write=True) as session:
            record = session.get(DeviceAuthorization, digest(code))
            now = time.time()
            if (record is None or record.expires_at <= now or record.redirect_uri != callback
                    or record.device_id != device_id or not hmac.compare_digest(record.challenge, supplied)):
                raise Unauthenticated("Device authorization is unavailable; start login again")
            browser = session.get(UserSession, record.session_id)
            actor = Actor(browser.user_id, browser.id, request_id)
            user = active_user(session, actor)
            # Reconnecting the same installation replaces only its own device session.
            for old in session.scalars(select(UserSession).where(UserSession.user_id == user.id,
                    UserSession.kind == "device", UserSession.revoked.is_(False))):
                if old.device_id == device_id:
                    old.revoked = True
            active = list(session.scalars(select(UserSession).where(UserSession.user_id == user.id,
                UserSession.kind == "device", UserSession.revoked.is_(False), UserSession.expires_at > now)
                .order_by(UserSession.created_at.desc())))
            for old in active[19:]:
                old.revoked = True
            token = secrets.token_urlsafe(32)
            login = UserSession(user_id=user.id, token_hash=digest(token), csrf_hash=digest(secrets.token_urlsafe(32)),
                kind="device", device_id=device_id, device_name=record.device_name,
                created_at=now, expires_at=now + self.ttl)
            session.add(login)
            session.delete(record)
            session.flush()
            audit(session, Actor(user.id, login.id, request_id), "device.login", resource_id=login.id)
            return {"token": token, "session_id": login.id, "expires_at": login.expires_at}

    def sessions(self, actor):
        return [record for record in self.identity.sessions(actor) if record["kind"] == "device"]

    def revoke(self, actor, session_id):
        with self.db.transaction(write=True) as session:
            active_user(session, actor)
            record = session.get(UserSession, session_id)
            if record is None or record.user_id != actor.user_id or record.kind != "device":
                raise NotFound("Device is unavailable")
            record.revoked = True
            audit(session, actor, "device.revoke", resource_id=record.id)
