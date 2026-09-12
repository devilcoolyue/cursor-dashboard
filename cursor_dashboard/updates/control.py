"""A narrow file queue shared with the host update service; no shell access in the API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from .releases import UpdateError, version_tuple

ACTIVE_STAGES = {"queued", "downloading", "verifying", "backing_up", "upgrading", "restarting", "rolling_back"}
STAGES = ACTIVE_STAGES | {"idle", "complete", "failed"}


def atomic_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o660)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("Invalid update control file")
    return json.loads(path.read_text())


class UpdateControl:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("CURSOR_UPDATE_CONTROL_DIR"))

    def status(self):
        result = {"enabled": False, "stage": "idle", "job_id": None, "version": None, "message": None}
        if self.directory is None:
            return result
        try:
            heartbeat = read_json(self.directory / "agent.json")
            result["enabled"] = 0 <= time.time() - float(heartbeat["heartbeat"]) < 30
            status = read_json(self.directory / "status.json")
            if status.get("stage") in STAGES:
                result.update({k: status.get(k) for k in ("stage", "job_id", "version", "message")})
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            request = read_json(self.directory / "request.json")
            if request["job_id"] != result["job_id"]:
                result.update(stage="queued", job_id=request["job_id"], version=request["version"], message=None)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if not result["enabled"] and result["stage"] in ACTIVE_STAGES:
            result["message"] = "更新服务暂时离线，请稍后查看结果。"
        return result

    def enqueue(self, version):
        version_tuple(version)
        status = self.status()
        if not status["enabled"] or self.directory is None:
            raise UpdateError("该实例尚未启用自动升级服务。")
        if status["stage"] in ACTIVE_STAGES:
            raise UpdateError("已有升级任务正在进行。")
        job = {"job_id": str(uuid.uuid4()), "version": version, "requested_at": time.time()}
        # Reserve the queue slot before publishing a fully written request.
        try:
            reservation = self.directory / "pending"
            descriptor = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
        except FileExistsError:
            raise UpdateError("已有升级任务正在进行。") from None
        os.close(descriptor)
        try:
            atomic_json(self.directory / "request.json", job)
        except BaseException:
            reservation.unlink(missing_ok=True)
            raise
        return {"enabled": True, "stage": "queued", "message": None, **{k: job[k] for k in ("job_id", "version")}}
