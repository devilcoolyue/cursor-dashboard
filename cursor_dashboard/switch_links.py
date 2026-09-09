"""Bounded, process-local, single-use downloads for desktop switch scripts."""

from __future__ import annotations

import hashlib
import json
import secrets
import shlex
import threading
import time
from dataclasses import dataclass


LINK_TTL = 300
MAX_LINKS = 128


def credential_version(account: dict) -> str:
    values = [account.get(key) for key in (
        "db_id", "auth_generation", "access_token", "refresh_token", "auth_invalid",
    )]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def download_command(url: str, platform: str) -> str:
    if platform == "macos":
        # Buffer the complete response: a failed/truncated transfer must not run a partial script.
        # Use a pipe, not a seekable here-string: macOS Bash 3.2 can corrupt its
        # input offset when the progress spinner forks while reading from one.
        return (f"(switch_script=$(curl -fsS --max-time 30 {shlex.quote(url)}) "
                '&& printf "%s\\n" "$switch_script" | /bin/bash)')
    if platform == "windows":
        quoted = "'" + url.replace("'", "''") + "'"
        return ("& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing "
                f"-TimeoutSec 30 -MaximumRedirection 0 -ErrorAction Stop {quoted}).Content))")
    raise ValueError("Unsupported desktop platform")


@dataclass(frozen=True, repr=False)
class SwitchLink:
    account_key: str
    version: str
    admin_session: str
    expires_at: int
    scripts: dict[str, str]


class SwitchLinks:
    def __init__(self):
        self._entries: dict[str, SwitchLink] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                del self._entries[key]

    def prune(self) -> None:
        with self._lock:
            self._prune(time.time())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def issue(self, account_key: str, account: dict, result: dict,
              admin_session: str = "") -> tuple[str, int]:
        now = time.time()
        expiry = min(int(now) + LINK_TTL, result["expires_at"])
        if expiry <= now:
            raise ValueError("桌面凭证已过期，请重新生成命令。")
        entry = SwitchLink(account_key, credential_version(account), admin_session, expiry,
                           {platform: item["script"] for platform, item in result["commands"].items()})
        with self._lock:
            self._prune(now)
            if len(self._entries) >= MAX_LINKS:
                raise OverflowError("待下载命令过多，请稍后再生成。")
            token = secrets.token_urlsafe(24)
            self._entries[token] = entry
        return token, expiry

    def consume(self, token: str, platform: str) -> SwitchLink | None:
        with self._lock:
            self._prune(time.time())
            entry = self._entries.get(token)
            if entry is None or platform not in entry.scripts:
                return None
            # Both platforms belong to one grant; concurrent downloads cannot both succeed.
            return self._entries.pop(token)
