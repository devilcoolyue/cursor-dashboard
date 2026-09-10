"""Password hashing and bounded per-instance throttling; no credentials in logs."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import hmac
import secrets
import threading
import time

from ..domain.core import Conflict, Throttled


_password_work = threading.Lock()


def _derive(password, salt):
    # Each scrypt job uses 128 MiB. Serialize jobs so a login burst cannot
    # multiply that memory use by the HTTP worker thread count.
    with _password_work:
        return hashlib.scrypt(password.encode(), salt=salt, n=131072, r=8, p=1, maxmem=256*1024*1024)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise Conflict("Password must contain 12 to 256 characters")
    salt = secrets.token_hex(16)
    value = _derive(password, bytes.fromhex(salt))
    return f"scrypt$131072$8$1${salt}${value.hex()}"


def verify_password(password, encoded):
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if kind != "scrypt" or (n, r, p) != ("131072", "8", "1") or len(password) > 256:
            return False
        value = _derive(password, bytes.fromhex(salt))
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


# Known invalid record still performs the same expensive work as a real login.
DUMMY_PASSWORD = "scrypt$131072$8$1$" + "00" * 16 + "$" + "00" * 64


class Limiter:
    def __init__(self, capacity=4096):
        self.entries = OrderedDict()
        self.capacity = capacity
        self.lock = threading.Lock()

    def check(self, *rules):
        """Atomically charge (key, limit, window) rules. Full active state fails closed."""
        now = time.monotonic()
        with self.lock:
            for key in list(self.entries):
                if self.entries[key][1] <= now:
                    del self.entries[key]
            new = {key for key, _, _ in rules if key not in self.entries}
            if len(self.entries) + len(new) > self.capacity:
                raise Throttled("Too many requests; retry later")
            if any(self.entries.get(key, (0, 0))[0] >= limit for key, limit, _ in rules):
                raise Throttled("Too many requests; retry later")
            for key, _, window in rules:
                count, deadline = self.entries.get(key, (0, now + window))
                self.entries[key] = count + 1, deadline

    def manual(self, actor, workspace_id, account_id):
        self.check((("manual-user", actor.user_id), 60, 60),
                   (("manual-account", workspace_id, account_id), 20, 60))
