"""Persistent administrator sessions, switch permissions, and safe credential metadata."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from . import store
from .config import TOKEN_REFRESH_MARGIN


SESSION_LIFETIME = 12 * 60 * 60
LOGIN_WINDOW = 15 * 60
CLIENT_LOGIN_LIMIT = 5
GLOBAL_LOGIN_LIMIT = 100
MAX_PASSWORD_LENGTH = 1024
_PASSWORD_KEY = "admin_password_scrypt"
_POLICY_KEY = "admin_switch_policy"
_JWT_PART = re.compile(r"[A-Za-z0-9_-]+\Z")


class InvalidPassword(ValueError):
    pass


class LoginThrottled(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Too many login attempts.")
        self.retry_after = max(1, retry_after)


@contextmanager
def _connection():
    try:
        with closing(store._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                csrf_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS admin_login_attempts (
                client TEXT NOT NULL,
                attempted_at REAL NOT NULL
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS admin_login_attempts_time
                ON admin_login_attempts (attempted_at)""")
            yield conn
    except sqlite3.Error as exc:
        raise store.AccountsError("Administrator database operation failed.") from exc


def _metadata(conn, key):
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_metadata(conn, key, value):
    conn.execute("""INSERT INTO metadata (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, value))


def _password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def _password_matches(password, stored):
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        if len(salt) != 32 or len(expected) != 128:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=16384, r=8, p=1)
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (AttributeError, ValueError, UnicodeError):
        return False


def initialize_admin(password: str | None = None) -> str | None:
    """Return a generated first-use password once; explicit overrides revoke sessions."""
    if password is None:
        password = os.environ.get("ADMIN_PASSWORD") or None
    if password is not None and (not isinstance(password, str) or not password
                                 or len(password) > MAX_PASSWORD_LENGTH):
        raise ValueError("Invalid administrator password length.")
    generated = None
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _metadata(conn, _PASSWORD_KEY)
        if current is None or password is not None:
            if password is None:
                password = generated = secrets.token_urlsafe(24)
            if current is None or not _password_matches(password, current):
                _set_metadata(conn, _PASSWORD_KEY, _password_hash(password))
                conn.execute("DELETE FROM admin_sessions")
                conn.execute("DELETE FROM admin_login_attempts")
        if _metadata(conn, _POLICY_KEY) is None:
            _set_metadata(conn, _POLICY_KEY, json.dumps(_default_policy()))
    return generated


def _token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _valid_session_token(token):
    return (isinstance(token, str) and 32 <= len(token) <= 128
            and _JWT_PART.fullmatch(token) is not None)


def _new_session(conn, now):
    token = secrets.token_urlsafe(32)
    session = {"token": token, "csrf_token": secrets.token_urlsafe(32),
               "expires_at": int(now) + SESSION_LIFETIME}
    conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
    conn.execute("INSERT INTO admin_sessions (token_hash, csrf_token, expires_at) VALUES (?, ?, ?)",
                 (_token_hash(token), session["csrf_token"], session["expires_at"]))
    return session


def login(password: str, client: str) -> dict:
    """Check the password and issue an administrator session with persistent throttling."""
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH:
        raise InvalidPassword("Invalid administrator password.")
    client_key = hashlib.sha256(str(client).encode("utf-8")).hexdigest()
    now = time.time()
    session = None
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM admin_login_attempts WHERE attempted_at <= ?", (now - LOGIN_WINDOW,))
        attempts = conn.execute("SELECT client, attempted_at FROM admin_login_attempts ORDER BY attempted_at").fetchall()
        own_attempts = [row for row in attempts if row["client"] == client_key]
        limited = []
        if len(own_attempts) >= CLIENT_LOGIN_LIMIT:
            limited.append(own_attempts[-CLIENT_LOGIN_LIMIT]["attempted_at"] + LOGIN_WINDOW)
        if len(attempts) >= GLOBAL_LOGIN_LIMIT:
            limited.append(attempts[-GLOBAL_LOGIN_LIMIT]["attempted_at"] + LOGIN_WINDOW)
        if limited:
            raise LoginThrottled(math.ceil(max(limited) - now))
        if _password_matches(password, _metadata(conn, _PASSWORD_KEY)):
            conn.execute("DELETE FROM admin_login_attempts WHERE client = ?", (client_key,))
            session = _new_session(conn, now)
        else:
            conn.execute("INSERT INTO admin_login_attempts (client, attempted_at) VALUES (?, ?)",
                         (client_key, now))
    if session is None:
        raise InvalidPassword("Invalid administrator password.")
    return session


def check_session(raw: str | None) -> dict | None:
    if not _valid_session_token(raw):
        return None
    with _connection() as conn:
        row = conn.execute("SELECT csrf_token, expires_at FROM admin_sessions WHERE token_hash = ?",
                           (_token_hash(raw),)).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= time.time():
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_token_hash(raw),))
            return None
        return dict(row)


def delete_session(raw: str | None) -> None:
    if _valid_session_token(raw):
        with _connection() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_token_hash(raw),))


def _default_policy():
    return {"all_accounts": False, "departments": [], "account_ids": []}


def _validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {"all_accounts", "departments", "account_ids"}:
        raise ValueError("Invalid switch policy.")
    departments, account_ids = policy["departments"], policy["account_ids"]
    if (type(policy["all_accounts"]) is not bool
            or not isinstance(departments, list) or len(departments) > 1000
            or any(not isinstance(item, str) or len(item) > 200 for item in departments)
            or not isinstance(account_ids, list) or len(account_ids) > 10000
            or any(type(item) is not int or item <= 0 for item in account_ids)):
        raise ValueError("Invalid switch policy.")
    return {"all_accounts": policy["all_accounts"],
            "departments": sorted(set(departments)), "account_ids": sorted(set(account_ids))}


def get_policy() -> dict:
    with _connection() as conn:
        value = _metadata(conn, _POLICY_KEY)
    if value is None:
        return _default_policy()
    try:
        return _validate_policy(json.loads(value))
    except (ValueError, TypeError):
        raise store.AccountsError("Saved administrator switch policy is invalid.") from None


def save_policy(policy: dict) -> dict:
    normalized = _validate_policy(policy)
    with _connection() as conn:
        _set_metadata(conn, _POLICY_KEY, json.dumps(normalized, ensure_ascii=True))
    return normalized


def switch_allowed(account: dict, policy: dict, is_admin: bool = False) -> bool:
    return bool(is_admin or policy.get("all_accounts") is True
                or account.get("department", "") in policy.get("departments", [])
                or account.get("db_id") in policy.get("account_ids", []))


def _timestamp(value):
    if type(value) not in (int, float) or not 0 < value <= 253402300799:
        return None
    return value


def _iso(value):
    timestamp = _timestamp(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _embedded_expiry(token):
    """Read display-only JWT claims, including expired JWTs; never authenticate here."""
    if not isinstance(token, str) or len(token) > 12000:
        return None
    parts = token.split(".")
    if len(parts) != 3 or not all(_JWT_PART.fullmatch(part) for part in parts):
        return None
    try:
        claims = json.loads(base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4),
                                            altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        return None
    return _timestamp(claims.get("exp")) if isinstance(claims, dict) else None


def credential_view(account: dict) -> dict:
    has_access = bool(account.get("access_token"))
    has_refresh = bool(account.get("refresh_token"))
    access_expiry = _embedded_expiry(account.get("access_token"))
    refresh_expiry = _embedded_expiry(account.get("refresh_token"))
    stored_expiry = _timestamp(account.get("token_expires_at"))
    refreshed = _timestamp(account.get("auth_refreshed_at"))
    due = None
    if stored_expiry is not None and has_access and has_refresh:
        lifetime = stored_expiry - (refreshed or 0)
        due = stored_expiry - min(TOKEN_REFRESH_MARGIN, max(30, lifetime * .2))
    if account.get("auth_invalid"):
        status = "needs_reauthorization"
    elif not has_access or not has_refresh:
        status = "not_authorized"
    elif due is None or due <= time.time():
        status = "refresh_due"
    else:
        status = "active"
    return {"access_expires_at": _iso(access_expiry), "refresh_expires_at": _iso(refresh_expiry),
            "refreshed_at": _iso(refreshed), "refresh_due_at": _iso(due), "status": status,
            "has_access_token": has_access, "has_refresh_token": has_refresh}
