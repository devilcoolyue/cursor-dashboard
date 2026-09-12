from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Mapping

from ..domain.core import CoreError


@dataclass(frozen=True)
class CoreConfig:
    data_dir: Path
    key_file: Path
    mode: str = "local"
    refresh_margin: float = 86400
    request_interval: float = .5
    request_concurrency: int = 3
    detail_ttl: float = 60
    lease_ttl: float = 300
    lease_wait: float = 180
    audit_retention_days: int = 90
    audit_max_events: int = 100000

    def __post_init__(self):
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser().resolve())
        object.__setattr__(self, "key_file", Path(self.key_file).expanduser().resolve())
        if self.mode not in {"local", "server"}:
            raise CoreError("Runtime mode must be local or server")
        reserved = {self.database, self.lock_file, *(Path(f"{self.database}{suffix}")
                     for suffix in ("-wal", "-shm", "-journal"))}
        if self.key_file in reserved:
            raise CoreError("Key file must be separate from database and lock")
        for value in (self.refresh_margin, self.request_interval, self.detail_ttl, self.lease_wait):
            if not math.isfinite(value) or value < 0:
                raise CoreError("Runtime intervals must be finite and non-negative")
        if self.lease_ttl <= 0 or not math.isfinite(self.lease_ttl) or self.request_concurrency < 1:
            raise CoreError("Invalid lease or concurrency configuration")
        if any(type(value) is not int or value < 1 for value in (self.audit_retention_days, self.audit_max_events)):
            raise CoreError("Audit retention days and maximum events must be positive integers")

    @property
    def database(self) -> Path:
        return self.data_dir / "core.db"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / ".core.lock"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CoreConfig:
        values = os.environ if env is None else env
        if not values.get("CURSOR_CORE_DATA_DIR") or not values.get("CURSOR_CORE_KEY_FILE"):
            raise CoreError("Set CURSOR_CORE_DATA_DIR and CURSOR_CORE_KEY_FILE explicitly")
        try:
            return cls(Path(values["CURSOR_CORE_DATA_DIR"]), Path(values["CURSOR_CORE_KEY_FILE"]),
                       mode=values.get("CURSOR_CORE_MODE", "local"),
                       audit_retention_days=int(values.get("CURSOR_AUDIT_RETENTION_DAYS", "90")),
                       audit_max_events=int(values.get("CURSOR_AUDIT_MAX_EVENTS", "100000")))
        except ValueError:
            raise CoreError("Audit retention settings must be positive integers") from None
