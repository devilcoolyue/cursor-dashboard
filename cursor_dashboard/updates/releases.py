"""Read releases from the project's fixed origin and verify signed server manifests."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cursor_dashboard import __version__

REPOSITORY = "devilcoolyue/cursor-dashboard"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
PUBLIC_KEY = Path(__file__).with_name("public-key.txt").read_text().strip()


class UpdateError(Exception):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise UpdateError("更新版本号无效。")
    return tuple(map(int, value.split(".")))


def download_metadata(url, *, optional=False):
    try:
        with requests.get(url, headers={"Accept": "application/json", "User-Agent": "Cursor-Panel-Updates"},
                          timeout=(5, 15), stream=True) as response:
            if optional and response.status_code == 404:
                return None
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > 262144:
                    raise UpdateError("更新信息超过大小限制。")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException:
        raise UpdateError("无法连接发布服务，请稍后重试。") from None


def latest_release():
    raw = download_metadata(LATEST_URL, optional=True)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        tag = value["tag_name"]
        version = tag.removeprefix("v")
        version_tuple(version)
        if tag != f"v{version}" or value.get("draft") or value.get("prerelease"):
            raise ValueError()
        return {"version": version, "notes": str(value.get("body") or "")[:12000],
                "published_at": value.get("published_at"), "release_url": f"{RELEASES_URL}/tag/{tag}"}
    except (ValueError, KeyError, TypeError):
        raise UpdateError("发布服务返回了无效的版本信息。") from None


def verify_signature(data: bytes, signature: str, public_key=PUBLIC_KEY):
    """Verify the Minisign format emitted by `tauri signer sign`, including its comment."""
    try:
        public_lines = base64.b64decode(public_key, validate=True).decode("utf-8").splitlines()
        lines = base64.b64decode(signature.strip(), validate=True).decode("utf-8").splitlines()
        public = base64.b64decode(public_lines[1], validate=True)
        packet = base64.b64decode(lines[1], validate=True)
        if (len(public) != 42 or public[:2] != b"Ed" or len(packet) != 74 or len(lines) != 4
                or packet[:2] not in {b"Ed", b"ED"} or packet[2:10] != public[2:10]
                or not lines[2].startswith("trusted comment: ")):
            raise ValueError()
        key = Ed25519PublicKey.from_public_bytes(public[10:])
        payload = hashlib.blake2b(data).digest() if packet[:2] == b"ED" else data
        key.verify(packet[10:], payload)
        key.verify(base64.b64decode(lines[3], validate=True), packet[10:] + lines[2][17:].encode())
    except Exception:
        raise UpdateError("更新签名校验失败，已停止升级。") from None


def release_asset_url(version, filename):
    version_tuple(version)
    if not re.fullmatch(r"[A-Za-z0-9_+.-]+", filename) or filename.startswith("."):
        raise UpdateError("更新文件名无效。")
    return f"{RELEASES_URL}/download/v{version}/{filename}"


def server_manifest(version):
    raw = download_metadata(release_asset_url(version, "server-update.json"), optional=True)
    if raw is None:
        return None
    signature = download_metadata(release_asset_url(version, "server-update.json.sig"))
    verify_signature(raw, signature.decode("ascii"))
    try:
        value = json.loads(raw)
        artifact = value["artifact"]
        if (value["version"] != version or value["target"] != "linux-amd64" or value["format"] != 1 or value["api_version"] != 1
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"])
                or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])
                or type(artifact["size"]) is not int or not 0 < artifact["size"] <= 4 * 1024**3
                or artifact["url"] != release_asset_url(version, artifact["name"])):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError):
        raise UpdateError("服务器更新清单无效。") from None


def check_release(current=__version__, *, server=False):
    latest = latest_release()
    result = {"current_version": current, "latest_version": latest["version"] if latest else None,
              "available": bool(latest and version_tuple(latest["version"]) > version_tuple(current)),
              "installable": False, "notes": latest["notes"] if latest else "",
              "release_url": latest["release_url"] if latest else RELEASES_URL,
              "published_at": latest["published_at"] if latest else None}
    if server and result["available"]:
        result["installable"] = server_manifest(latest["version"]) is not None
    return result
