"""Linux host service for the standard Compose deployment's signed, reversible updates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import requests

from .control import atomic_json, read_json
from .releases import UpdateError, server_manifest, version_tuple


def docker(*args, timeout=300):
    try:
        return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, timeout=timeout).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Docker output and configuration can contain credentials; report only the operation.
        raise UpdateError(f"容器操作 {args[0]} 未完成。") from None


def write_env(path, contents):
    if path.is_symlink():
        raise UpdateError("部署配置不能是符号链接。")
    fd, name = tempfile.mkstemp(prefix=".cursor-update-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def updated_env(contents, values):
    lines = [line for line in contents.splitlines()
             if not any(re.match(rf"\s*(?:export\s+)?{re.escape(key)}\s*=", line) for key in values)]
    return "\n".join([*lines, *(f"{key}={value}" for key, value in values.items())]) + "\n"


def download_artifact(artifact, destination):
    digest, size = hashlib.sha256(), 0
    if shutil.disk_usage(destination.parent).free < artifact["size"] + 128 * 1024**2:
        raise UpdateError("磁盘空间不足，无法下载更新。")
    try:
        with requests.get(artifact["url"], stream=True, timeout=(10, 60)) as response, destination.open("xb") as output:
            response.raise_for_status()
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > artifact["size"]:
                    raise UpdateError("更新包大小不符。")
                digest.update(chunk)
                output.write(chunk)
    except requests.RequestException:
        raise UpdateError("更新包下载失败。") from None
    if size != artifact["size"] or digest.hexdigest() != artifact["sha256"]:
        raise UpdateError("更新包完整性校验失败。")


class Agent:
    def __init__(self, compose_files, env_file, control_dir, state_dir, *, runner=docker, manifest=server_manifest,
                 downloader=download_artifact):
        self.compose_files = [Path(path).resolve() for path in compose_files]
        self.env_file = Path(env_file).resolve()
        self.control = Path(control_dir)
        self.private = Path(state_dir)
        self.runner, self.manifest, self.downloader = runner, manifest, downloader
        self.journal = self.private / "recovery.json"

    def compose(self, *args, timeout=300):
        return self.runner("compose", "--env-file", str(self.env_file),
                           *(part for path in self.compose_files for part in ("-f", str(path))), *args, timeout=timeout)

    def status(self, job, stage, message=None):
        atomic_json(self.control / "status.json", {"stage": stage, "job_id": job["job_id"],
                    "version": job["version"], "message": message})

    def rollback(self, journal):
        # The original volume was never migrated; switching back does not downgrade a database.
        self.compose("stop", "--timeout", "90", "proxy", "panel", timeout=120)
        write_env(self.env_file, journal["environment"])
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "panel", "proxy", timeout=150)

    def recover(self):
        if not self.journal.exists():
            return
        journal = read_json(self.journal)
        if journal.get("committed"):
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "panel", "proxy", timeout=150)
            self.status(journal, "complete")
        else:
            self.status(journal, "rolling_back", "上次升级被中断，正在恢复旧版本。")
            self.rollback(journal)
            self.status(journal, "failed", "上次升级被中断，旧版本和原数据已恢复。")
        self.journal.unlink()
        (self.control / "request.json").unlink(missing_ok=True)
        (self.control / "pending").unlink(missing_ok=True)

    def execute(self, job):
        uuid.UUID(job["job_id"])
        version_tuple(job["version"])
        version = job["version"]
        changed = False
        committed = False
        journal = None
        try:
            manifest = self.manifest(version)
            if manifest is None:
                raise UpdateError("这个版本尚未提供已签名的服务器更新包。")
            container_id = self.compose("ps", "-q", "panel").strip()
            if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
                raise UpdateError("未找到当前运行的服务器。")
            container = json.loads(self.runner("inspect", container_id))[0]
            old_image = container["Image"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", old_image):
                raise UpdateError("当前镜像标识无效。")
            old_version = container["Config"]["Labels"]["org.opencontainers.image.version"]
            if version_tuple(version) <= version_tuple(old_version):
                raise UpdateError("目标版本必须高于当前服务器版本。")
            mounts = {mount["Destination"]: mount for mount in container["Mounts"]}
            volume = mounts["/var/lib/cursor-panel"]
            secrets = mounts["/run/cursor-secrets"]
            if volume["Type"] != "volume" or secrets["Type"] != "volume":
                raise UpdateError("自动升级需要标准部署的数据卷和密钥卷。")
            old_volume, key_volume = volume["Name"], secrets["Name"]
            if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) for name in (old_volume, key_volume)):
                raise UpdateError("部署卷名称无效。")
            environment = self.env_file.read_text()
            if len(environment.encode()) > 16384:
                raise UpdateError("部署配置过大，无法保存自动恢复记录。")
            journal = {**job, "environment": updated_env(environment, {"CURSOR_PANEL_IMAGE": old_image,
                "CURSOR_PANEL_DATA_VOLUME": old_volume}), "old_volume": old_volume, "old_image": old_image}
            image = manifest["image_id"]
            self.status(job, "downloading")
            with tempfile.TemporaryDirectory(prefix="cursor-panel-update-") as directory:
                artifact = Path(directory) / "image.tar"
                self.downloader(manifest["artifact"], artifact)
                self.status(job, "verifying")
                self.runner("load", "--input", str(artifact), timeout=600)
            image_info = json.loads(self.runner("image", "inspect", image))[0]
            if (image_info["Id"] != image or image_info["Config"]["Labels"].get("org.opencontainers.image.version") != version
                    or image_info["Architecture"] != "amd64" or image_info["Os"] != "linux"):
                raise UpdateError("更新镜像与签名清单不符。")
            if self.env_file.read_text() != environment:
                raise UpdateError("部署配置已变化，请重新检查更新。")
            new_volume = f"{old_volume.split('-update-')[0]}-update-{job['job_id']}"
            self.runner("volume", "create", new_volume)
            atomic_json(self.journal, journal)
            write_env(self.env_file.with_name(self.env_file.name + ".before-" + job["job_id"]), environment)
            changed = True
            self.status(job, "backing_up")
            self.compose("stop", "--timeout", "90", "proxy", "panel", timeout=120)
            self.runner("run", "--rm", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "-v", old_volume + ":/backup-source:ro", "-v", new_volume + ":/var/lib/cursor-panel",
                "--entrypoint", "python", old_image, "-c",
                "import shutil; shutil.copytree('/backup-source', '/var/lib/cursor-panel', dirs_exist_ok=True)", timeout=600)
            self.status(job, "upgrading")
            for operation in ("upgrade", "verify"):
                self.runner("run", "--rm", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    "-v", new_volume + ":/var/lib/cursor-panel", "-v", key_volume + ":/run/cursor-secrets:ro",
                    image, "cursor-core", operation, timeout=600)
            write_env(self.env_file, updated_env(environment, {"CURSOR_PANEL_IMAGE": image,
                "CURSOR_PANEL_VERSION": version, "CURSOR_PANEL_DATA_VOLUME": new_volume}))
            self.status(job, "restarting")
            self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "panel", timeout=150)
            atomic_json(self.journal, {**journal, "committed": True})
            committed = True
            # After traffic is admitted, recovery must retain all writes to the new volume.
            self.compose("up", "-d", "--no-build", "proxy", timeout=60)
            self.status(job, "complete", "升级完成，原数据卷和旧镜像已保留。")
            self.journal.unlink()
        except Exception as error:
            if committed:
                self.status(job, "failed", "新版本已启动，代理尚未恢复；更新服务将继续恢复连接，保留新版本数据。")
                return
            if changed and journal is not None:
                self.status(job, "rolling_back")
                try:
                    self.rollback(journal)
                    self.journal.unlink(missing_ok=True)
                except Exception:
                    self.status(job, "failed", "升级未完成，自动恢复仍需处理；原数据已保留，请联系服务器维护者。")
                    return
                self.status(job, "failed", "升级未完成，已恢复旧版本和原数据。")
            else:
                self.status(job, "failed", str(error) if isinstance(error, UpdateError) else "升级准备失败，当前服务未被替换。")
        finally:
            (self.control / "request.json").unlink(missing_ok=True)
            (self.control / "pending").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", action="append", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--control-dir", default="/var/lib/cursor-panel-updates")
    parser.add_argument("--state-dir", default="/var/lib/cursor-panel-update-agent")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("Server automatic updates currently support Linux amd64 Compose deployments.")
    import fcntl
    agent = Agent(args.compose, args.env_file, args.control_dir, args.state_dir)
    agent.private.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(agent.private, 0o700)
    agent.control.mkdir(parents=True, exist_ok=True)
    os.chown(agent.control, 0, 10001)
    os.chmod(agent.control, 0o2770)
    with (agent.private / "agent.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agent.recover()
        def heartbeat():
            while True:
                atomic_json(agent.control / "agent.json", {"heartbeat": time.time()})
                time.sleep(5)
        threading.Thread(target=heartbeat, daemon=True).start()
        if not (agent.control / "status.json").exists():
            atomic_json(agent.control / "status.json", {"stage": "idle", "job_id": None, "version": None, "message": None})
        while True:
            if agent.journal.exists():
                agent.recover()
            request = agent.control / "request.json"
            if request.exists():
                try:
                    job = read_json(request)
                    if time.time() - job["requested_at"] > 300:
                        raise ValueError("Expired request")
                    agent.execute(job)
                except (ValueError, KeyError, TypeError, UpdateError):
                    request.unlink(missing_ok=True)
                    (agent.control / "pending").unlink(missing_ok=True)
            elif (agent.control / "pending").exists() and time.time() - (agent.control / "pending").stat().st_mtime > 60:
                (agent.control / "pending").unlink(missing_ok=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
