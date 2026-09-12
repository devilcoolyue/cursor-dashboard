"""Prepare signed updater assets. This command never uploads or publishes files."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from cursor_dashboard.updates.releases import release_asset_url, verify_signature
from release import check_versions

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"darwin-aarch64", "darwin-x86_64", "windows-x86_64"}


def sign(path):
    if not (os.environ.get("TAURI_SIGNING_PRIVATE_KEY") or os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PATH")):
        raise SystemExit("Set TAURI_SIGNING_PRIVATE_KEY or TAURI_SIGNING_PRIVATE_KEY_PATH to the release signing key.")
    subprocess.run(["node", str(ROOT / "desktop/node_modules/@tauri-apps/cli/tauri.js"), "signer", "sign",
                    "--password", os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", ""), str(path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    signature = Path(str(path) + ".sig").read_text().strip()
    verify_signature(path.read_bytes(), signature)
    return signature


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["desktop", "server", "merge"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", choices=sorted(TARGETS))
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--image-metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = check_versions()
    if args.tag != f"v{version}":
        raise SystemExit("The release tag must match every package version.")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "desktop":
        if not args.target or not args.artifact:
            parser.error("desktop requires --target and --artifact")
        if args.target.startswith("darwin-"):
            if args.artifact.suffix != ".app" or not args.artifact.is_dir():
                parser.error("macOS requires a complete .app bundle")
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.artifact)], check=True)
            artifact = args.output / f"Cursor.Panel_{version}_{args.target}.app.tar.gz"
            with tarfile.open(artifact, "w:gz") as archive:
                archive.add(args.artifact, arcname="Cursor Panel.app", recursive=True)
        else:
            if args.artifact.suffix != ".exe":
                parser.error("Windows requires an NSIS .exe installer")
            artifact = args.output / f"Cursor.Panel_{version}_{args.target}-setup.exe"
            shutil.copy2(args.artifact, artifact)
        signature = sign(artifact)
        write(args.output / f"platform-{args.target}.json", {"version": version, "target": args.target,
              "url": release_asset_url(version, artifact.name), "signature": signature})
    elif args.mode == "server":
        if not args.artifact or not args.image_metadata:
            parser.error("server requires --artifact and --image-metadata")
        info = json.loads(args.image_metadata.read_text())[0]
        if (info["Config"]["Labels"]["org.opencontainers.image.version"] != version
                or info["Architecture"] != "amd64" or info["Os"] != "linux"):
            raise SystemExit("The image version or platform is incorrect.")
        artifact = args.output / "cursor-panel-linux-amd64.tar"
        if artifact.resolve() != args.artifact.resolve():
            shutil.copy2(args.artifact, artifact)
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest = args.output / "server-update.json"
        write(manifest, {"format": 1, "version": version, "api_version": 1, "target": "linux-amd64",
              "image_id": info["Id"], "artifact": {"name": artifact.name, "url": release_asset_url(version, artifact.name),
              "size": artifact.stat().st_size, "sha256": digest.hexdigest()}})
        sign(manifest)
    else:
        platforms = {}
        for path in args.output.glob("platform-*.json"):
            item = json.loads(path.read_text())
            if item["version"] != version or item["target"] not in TARGETS or item["target"] in platforms:
                raise SystemExit("Unexpected or duplicate platform metadata.")
            name = item["url"].rsplit("/", 1)[-1]
            if item["url"] != release_asset_url(version, name):
                raise SystemExit("Unexpected release origin.")
            verify_signature((args.output / name).read_bytes(), item["signature"])
            platforms[item["target"]] = {"url": item["url"], "signature": item["signature"]}
        if set(platforms) != TARGETS:
            raise SystemExit("All three desktop platforms are required before publishing the manifest.")
        server = args.output / "server-update.json"
        verify_signature(server.read_bytes(), Path(str(server) + ".sig").read_text())
        write(args.output / "latest.json", {"version": version, "pub_date": datetime.now(timezone.utc).isoformat(),
              "notes": "完整更新内容见本版本发行记录。", "platforms": platforms})
        for path in args.output.glob("platform-*.json"):
            path.unlink()
    print(f"Prepared {args.mode} updater assets for {version}: {args.output}")


if __name__ == "__main__":
    main()
