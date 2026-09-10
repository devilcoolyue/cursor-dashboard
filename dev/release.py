"""Build and verify a local, traceable V2 preview delivery (never publishes)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("uv.lock", "frontend/package-lock.json", "desktop/package-lock.json",
         "desktop/sidecar/uv.lock", "desktop/src-tauri/Cargo.lock")
TARGETS = ("web-python", "linux-amd64", "linux-arm64", "macos-arm64", "macos-x64", "windows-x64")
FORBIDDEN = re.compile(r"(?:\.(?:db|sqlite|sqlite3)(?:$|[.-])|\.cursorarchive$|\.(?:p12|pfx|key)$)", re.I)
TOKEN = re.compile(rb"(?:eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,})")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(["git", *args], cwd=root).decode("utf-8").strip()


def check_name(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name
            or any(ord(char) < 32 for char in name) or FORBIDDEN.search(name)
            or any(part in {"data", "cursor-backups", ".git", ".venv"} for part in path.parts)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.name in {"accounts.json", "master.json", "key.json", "connections.json"}):
        raise ValueError(f"Disallowed delivery path: {name}")


def check_content(name, payload):
    if payload.startswith(b"SQLite format 3\x00") or TOKEN.search(payload):
        # Never print matched material, only the relative source/member name.
        raise ValueError(f"Potential runtime data or credential in: {name}")


def check_versions(root=ROOT):
    version = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Package version must be X.Y.Z")
    values = {
        "python": re.search(r'^__version__ = "([^"]+)"', (root / "cursor_dashboard/__init__.py").read_text(encoding="utf-8"), re.M)[1],
        "tauri": json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"],
    }
    for name in ("desktop", "frontend"):
        values[name] = json.loads((root / name / "package.json").read_text(encoding="utf-8"))["version"]
        lock = json.loads((root / name / "package-lock.json").read_text(encoding="utf-8"))
        values[name + " lock"] = lock["version"]
        values[name + " lock root"] = lock["packages"][""]["version"]
    for name in ("desktop/src-tauri/Cargo.toml", "desktop/sidecar/pyproject.toml"):
        values[name] = re.search(r'^version = "([^"]+)"', (root / name).read_text(encoding="utf-8"), re.M)[1]
    for name, package in (("uv.lock", "cursor-dashboard"), ("desktop/sidecar/uv.lock", "cursor-dashboard"),
                          ("desktop/sidecar/uv.lock", "cursor-panel-desktop-sidecar"),
                          ("desktop/src-tauri/Cargo.lock", "cursor-panel-desktop")):
        values[name + ":" + package] = re.search(
            r'\[\[package\]\]\nname = "' + re.escape(package) + r'"\nversion = "([^"]+)"',
            (root / name).read_text(encoding="utf-8"))[1]
    for name, value in values.items():
        if value != version:
            raise ValueError(f"Version mismatch: {name} ({value} != {version})")
    return version


def audit_source(root=ROOT):
    names = git("ls-files", "-z", root=root).split("\0")
    count = 0
    for name in filter(None, names):
        check_name(name)
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Source must be a regular file: {name}")
        check_content(name, path.read_bytes())
        count += 1
    return count


def audit_tree(directory):
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Runtime audit requires an existing regular directory")
    count = 0
    for path in sorted(directory.rglob("*")):
        name = path.relative_to(directory).as_posix()
        check_name(name)
        if path.is_symlink():
            # PyInstaller's macOS framework uses internal symlinks. Preserve
            # those, but never let an audit leave the explicit runtime tree.
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(directory.resolve()):
                raise ValueError(f"Runtime symlink escapes tree: {name}")
            check_name(resolved.relative_to(directory.resolve()).as_posix())
        if path.is_file():
            check_content(name, path.read_bytes())
            count += 1
    if not count:
        raise ValueError("Runtime audit cannot validate an empty tree")
    return count


def audit_wheel(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise ValueError("Duplicate wheel members")
        if "cursor_dashboard/web_v2/index.html" not in members:
            raise ValueError("Delivery wheel is missing built Web assets")
        for name in members:
            check_name(name)
            if not name.startswith(("cursor_dashboard/", "cursor_dashboard-")):
                raise ValueError(f"Unexpected wheel member: {name}")
            check_content(name, archive.read(name))


def manifest(args):
    version = check_versions()
    count = audit_source()
    dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    if dirty and not args.allow_dirty:
        raise ValueError("Use a clean checkout; --allow-dirty is only for local verification")
    if any(Path(name).is_symlink() for name in args.artifact):
        raise ValueError("Artifact symlinks are not allowed")
    artifacts = [Path(name).resolve() for name in args.artifact]
    if len({path.name for path in artifacts}) != len(artifacts):
        raise ValueError("Artifact basenames must be unique")
    reserved = {"release-manifest.json", "SHA256SUMS"}
    for path in artifacts:
        check_name(path.name)
        if path.name in reserved or not path.is_file():
            raise ValueError("Artifacts must be regular files with non-reserved names")
        if path.suffix == ".whl":
            audit_wheel(path)
        allowed = {"web-python": ".whl", "linux-amd64": ".tar", "linux-arm64": ".tar",
                   "macos-arm64": ".dmg", "macos-x64": ".dmg", "windows-x64": ".exe"}
        if path.suffix != allowed[args.target]:
            raise ValueError("Artifact extension does not match target")
    report = {
        "format": 1, "product": "Cursor Panel", "version": version, "channel": "v2-preview",
        "target": args.target, "source_commit": git("rev-parse", "HEAD"),
        "source_dirty": dirty, "local_verification_only": dirty,
        "source_url": "https://github.com/devilcoolyue/cursor-dashboard",
        "build_run": os.environ.get("GITHUB_RUN_ID"), "build_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "api_major": 1, "schema": "0003_devices", "remote_switch": False,
        "signing": "unsigned", "notarized": False,
        "source_files_checked": count,
        "lock_sha256": {name: sha256(ROOT / name) for name in LOCKS},
        "tools": {"python": sys.version.split()[0]},
        "artifacts": [{"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)} for path in artifacts],
    }
    for name, command in (("uv", ["uv", "--version"]), ("node", ["node", "--version"]),
                          ("rust", ["rustc", "--version"]), ("docker", ["docker", "--version"])):
        if shutil.which(command[0]):
            report["tools"][name] = subprocess.check_output(command, text=True).strip()
    if args.image_metadata:
        info = json.loads(Path(args.image_metadata).read_text(encoding="utf-8"))
        info = info[0] if isinstance(info, list) else info
        labels = info["Config"]["Labels"]
        if (labels.get("org.opencontainers.image.version") != version
                or labels.get("org.opencontainers.image.revision") != report["source_commit"]):
            raise ValueError("Container labels do not match source/version")
        if f'{info["Os"]}-{info["Architecture"]}' != args.target:
            raise ValueError("Container architecture does not match target")
        report["container"] = {"id": info["Id"], "repo_digests": info.get("RepoDigests", []), "labels": labels}
    elif args.target.startswith("linux-"):
        raise ValueError("Container delivery requires docker image inspect metadata")
    args.output.mkdir(parents=True, exist_ok=False)
    for path in artifacts:
        shutil.copyfile(path, args.output / path.name)
    (args.output / "release-manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    files = sorted([*artifacts, args.output / "release-manifest.json"], key=lambda path: path.name)
    (args.output / "SHA256SUMS").write_text("".join(
        f"{sha256(args.output / path.name)}  {path.name}\n" for path in files), encoding="utf-8")
    verify(args.output)
    return report


def verify(directory):
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([^/\\]+)", line)
        if not match:
            raise ValueError("Invalid checksum entry")
        digest, name = match.groups()
        check_name(name)
        if name in checksums or name == "SHA256SUMS":
            raise ValueError("Duplicate or recursive checksum entry")
        checksums[name] = digest
    if "release-manifest.json" not in checksums:
        raise ValueError("Missing manifest checksum")
    if {p.name for p in directory.iterdir()} != {*checksums, "SHA256SUMS"}:
        raise ValueError("Delivery has missing or unlisted files")
    for name, digest in checksums.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Checksum mismatch or non-regular file: {name}")
    report = json.loads((directory / "release-manifest.json").read_text(encoding="utf-8"))
    if report["format"] != 1 or report["channel"] != "v2-preview":
        raise ValueError("Unsupported delivery format/channel")
    artifacts = report["artifacts"]
    if (not artifacts or len({a["name"] for a in artifacts}) != len(artifacts)
            or {a["name"] for a in artifacts} != set(checksums) - {"release-manifest.json"}):
        raise ValueError("Manifest/checksum artifact set differs")
    for item in artifacts:
        if item["sha256"] != checksums[item["name"]] or item["size"] != (directory / item["name"]).stat().st_size:
            raise ValueError("Manifest artifact digest/size mismatch")
    return {"verified": True, "artifacts": len(artifacts), "source_commit": report["source_commit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    tree = commands.add_parser("audit-tree")
    tree.add_argument("directory", type=Path)
    pack = commands.add_parser("manifest")
    pack.add_argument("--artifact", action="append", required=True)
    pack.add_argument("--target", choices=TARGETS, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--image-metadata", type=Path)
    pack.add_argument("--allow-dirty", action="store_true")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = {"version": check_versions(), "source_files_checked": audit_source()}
        elif args.command == "audit-tree":
            result = {"runtime_files_checked": audit_tree(args.directory)}
        elif args.command == "manifest":
            result = manifest(args)
        else:
            result = verify(args.directory)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Delivery check failed: {error}\n")


if __name__ == "__main__":
    main()
