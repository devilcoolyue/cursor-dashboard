"""Build each release target once; assemble and verify artifacts before publication."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

from release import ROOT, LOCKS, audit_source, audit_tree, audit_wheel, check_licenses, check_versions, git, sha256
from cursor_dashboard.updates.releases import release_asset_url, verify_signature, version_tuple

spec = importlib.util.spec_from_file_location("update_manifest", ROOT / "dev/update-manifest.py")
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)

TARGETS = ("darwin-aarch64", "darwin-x86_64", "windows-x86_64", "server")
OUTPUT = ROOT / "output/release"


def run(*args, **kwargs):
    command = [str(arg) for arg in args]
    command[0] = shutil.which(command[0]) or command[0]
    subprocess.run(command, cwd=kwargs.pop("cwd", ROOT), check=True, **kwargs)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(version, revision):
    version_tuple(version)
    if check_versions() != version or git("rev-parse", "HEAD") != revision:
        raise ValueError("Release version or source revision changed")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Release requires a clean checkout")
    notes = ROOT / f"docs/archive/v{version}.md"
    if not notes.is_file() or not notes.read_text(encoding="utf-8").strip():
        raise ValueError(f"Write release notes in {notes.relative_to(ROOT)} first")
    check_licenses()
    audit_source()


def record_part(directory, target, version, revision, smoke=None):
    record = {
        "version": version, "source_revision": revision, "target": target,
        "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "lock_sha256": {name: sha256(ROOT / name) for name in LOCKS},
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha256(p)}
                  for p in sorted(directory.iterdir()) if p.is_file() and p.name != "part.json"},
        "installed_smoke": smoke,
    }
    write_json(directory / "part.json", record)


def desktop(target, version, revision):
    suffix = ".exe" if sys.platform == "win32" else ""
    host = "windows-x86_64" if suffix else "darwin-aarch64" if os.uname().machine == "arm64" else "darwin-x86_64"
    if target != host:
        raise ValueError("Desktop target does not match this builder")
    # One freeze and one Tauri build supply both the installer and updater archive.
    run("npm", "--prefix", "desktop", "run", "build", "--", "--bundles", "nsis" if suffix else "app,dmg")
    audit_tree(ROOT / "desktop/src-tauri/runtime")
    run("cargo", "test", "--locked", "--manifest-path", "desktop/src-tauri/Cargo.toml", "--bin", "cursor-panel-desktop")
    binary = ROOT / f"desktop/sidecar/dist/cursor-local/cursor-local{suffix}"
    environment = dict(os.environ, P4_BACKEND_BINARY=str(binary))
    run("uv", "run", "--project", "desktop/sidecar", "--frozen", "python", "-m", "unittest", "discover",
        "-s", "desktop/tests", "-p", "test_local_backend.py", "-v", env=environment)
    bundle = ROOT / "desktop/src-tauri/target/release/bundle"
    if suffix:
        installer = next((bundle / "nsis").glob("*.exe"))
        install = Path(os.environ["RUNNER_TEMP"]) / "Release installed with spaces"
        subprocess.run(f'"{installer}" /S /D={install}', check=True, timeout=120)
        executable = install / "cursor-panel-desktop.exe"
    else:
        installer = next((bundle / "dmg").glob("*.dmg"))
        app = bundle / "macos/Cursor Panel.app"
        run("codesign", "--verify", "--deep", "--strict", app)
        executable = app / "Contents/MacOS/cursor-panel-desktop"
    report = OUTPUT / f"smoke-{target}.json"
    run("uv", "run", "--project", "desktop/sidecar", "--frozen", "python", "desktop/scripts/desktop-smoke.py",
        executable, "--output", report)
    destination = OUTPUT / "parts" / target
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(installer, destination / installer.name.replace(" ", "."))
    # Sign the installed/tested Windows installer, or the tested complete macOS app.
    run("uv", "run", "--frozen", "python", "dev/update-manifest.py", "desktop", "--tag", f"v{version}",
        "--target", target, "--artifact", installer if suffix else app, "--output", destination)
    record_part(destination, target, version, revision, json.loads(report.read_text(encoding="utf-8")))


def server(version, revision):
    destination = OUTPUT / "parts/server"
    destination.mkdir(parents=True, exist_ok=True)
    run("uv", "run", "--frozen", "python", "dev/build-web.py")
    wheels = OUTPUT / "wheel"
    run("uv", "build", "--wheel", "--out-dir", wheels)
    wheel = next(wheels.glob("*.whl"))
    run(sys.executable, "dev/verify-core-wheel.py", wheel, "--web")
    audit_wheel(wheel)
    shutil.copy2(wheel, destination / wheel.name)
    run("docker", "build", "--build-arg", f"APP_VERSION={version}", "--build-arg", f"SOURCE_REVISION={revision}",
        "-t", "cursor-panel:release", ".")
    run("uv", "run", "--frozen", "python", "dev/verify-v2-container.py", "cursor-panel:release")
    image = OUTPUT / "image.tar"
    run("docker", "save", "--output", image, "cursor-panel:release")
    info = subprocess.check_output(["docker", "image", "inspect", "cursor-panel:release"], cwd=ROOT)
    metadata = OUTPUT / "image.json"
    metadata.write_bytes(info)
    run("uv", "run", "--frozen", "python", "dev/update-manifest.py", "server", "--tag", f"v{version}",
        "--artifact", image, "--image-metadata", metadata, "--output", destination)
    record_part(destination, "server", version, revision)


def verify_parts(parts, version, revision):
    records = []
    if {p.name for p in parts.iterdir()} != set(TARGETS):
        raise ValueError("All four release targets are required")
    for target in TARGETS:
        directory = parts / target
        record = json.loads((directory / "part.json").read_text(encoding="utf-8"))
        if (record["version"], record["source_revision"], record["target"]) != (version, revision, target):
            raise ValueError("Release parts mix versions, revisions or targets")
        if record["lock_sha256"] != {name: sha256(ROOT / name) for name in LOCKS}:
            raise ValueError("Release part dependency locks differ from source")
        if record["run_id"] != os.environ.get("GITHUB_RUN_ID"):
            raise ValueError("Release part came from a different workflow run")
        if set(record["files"]) | {"part.json"} != {p.name for p in directory.iterdir()}:
            raise ValueError("Release part contains missing or unexpected files")
        for name, item in record["files"].items():
            if Path(name).name != name or "\\" in name:
                raise ValueError("Invalid release filename")
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
                raise ValueError(f"Release part integrity failed: {name}")
        if target != "server":
            samples = record["installed_smoke"]["samples"]
            if len(samples) != 3 or {s["mode"] for s in samples} != {"cold", "warm", "crash"}:
                raise ValueError("Installed startup evidence is incomplete")
            if not all(s["process_tree_cleaned"] and s["startup_log_verified"] and s["backend"]["frozen"]
                       and s["frontend"]["rendered_accounts"] == 2 for s in samples):
                raise ValueError("Installed startup checks failed")
        records.append(record)
    return records


def expected_assets(version):
    names = {"LICENSE", "SHA256SUMS", "latest.json", "server-update.json", "server-update.json.sig",
             "cursor-panel-linux-amd64.tar", f"cursor_dashboard-{version}-py3-none-any.whl",
             f"cursor-panel-v{version}-source.zip", f"cursor-panel-v{version}-source.tar.gz",
             f"cursor-panel-v{version}-verification.zip", f"Cursor.Panel_{version}_aarch64.dmg",
             f"Cursor.Panel_{version}_x64.dmg", f"Cursor.Panel_{version}_x64-setup.exe"}
    for target in TARGETS[:-1]:
        name = f"Cursor.Panel_{version}_{target}" + ("-setup.exe" if target.startswith("windows") else ".app.tar.gz")
        names.update({name, name + ".sig"})
    return names


def verify_assets(directory, version):
    if {p.name for p in directory.iterdir()} != expected_assets(version):
        raise ValueError("Release must contain exactly the expected 19 assets")
    sums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name in sums or name not in expected_assets(version) - {"SHA256SUMS"}:
            raise ValueError("Invalid or duplicate checksum entry")
        sums[name] = digest
    if set(sums) != expected_assets(version) - {"SHA256SUMS"}:
        raise ValueError("Missing checksum entries")
    for name, digest in sums.items():
        if (directory / name).is_symlink() or sha256(directory / name) != digest:
            raise ValueError(f"Checksum mismatch: {name}")
    feed = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
    if feed["version"] != version or set(feed["platforms"]) != set(TARGETS[:-1]):
        raise ValueError("Update feed does not match release")
    for item in feed["platforms"].values():
        name = item["url"].rsplit("/", 1)[-1]
        if name not in sums or item["url"] != release_asset_url(version, name):
            raise ValueError("Update feed points outside this release")
        if (directory / (name + ".sig")).read_text().strip() != item["signature"].strip():
            raise ValueError("Detached signature differs from update feed")
        verify_signature((directory / name).read_bytes(), item["signature"])
    manifest = directory / "server-update.json"
    verify_signature(manifest.read_bytes(), (directory / "server-update.json.sig").read_text())
    info = json.loads(manifest.read_text())
    item = info["artifact"]
    if (info["version"] != version or info["target"] != "linux-amd64" or item["name"] != "cursor-panel-linux-amd64.tar"
            or item["url"] != release_asset_url(version, item["name"]) or item["sha256"] != sums[item["name"]]
            or item["size"] != (directory / item["name"]).stat().st_size):
        raise ValueError("Server image differs from its signed manifest")
    return {p.name: {"size": p.stat().st_size, "sha256": sha256(p)} for p in sorted(directory.iterdir())}


def assemble(version, revision):
    records = verify_parts(OUTPUT / "parts", version, revision)
    destination = OUTPUT / "assets"
    if destination.exists():
        shutil.rmtree(destination)  # This invocation owns only its generated staging directory.
    destination.mkdir(parents=True)
    for target in TARGETS:
        for path in (OUTPUT / "parts" / target).iterdir():
            if path.name == "part.json":
                continue
            if (destination / path.name).exists():
                raise ValueError("Duplicate release filename across targets")
            shutil.copy2(path, destination / path.name)
    run("uv", "run", "--frozen", "python", "dev/update-manifest.py", "merge", "--tag", f"v{version}", "--output", destination)
    with tarfile.open(destination / "cursor-panel-linux-amd64.tar") as archive:
        entries = json.load(archive.extractfile("manifest.json"))
        raw = archive.extractfile(entries[0]["Config"]).read()
        image = json.loads(raw)
        server_info = json.loads((destination / "server-update.json").read_text())
        if (len(entries) != 1 or server_info["image_id"] != "sha256:" + hashlib.sha256(raw).hexdigest()
                or image["architecture"] != "amd64" or image["os"] != "linux"
                or image["config"]["Labels"]["org.opencontainers.image.revision"] != revision
                or image["config"]["Labels"]["org.opencontainers.image.version"] != version):
            raise ValueError("Image source, version or architecture differs from release")
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")
    for suffix in ("zip", "tar.gz"):
        run("git", "archive", f"--format={suffix}", f"--prefix=cursor-panel-v{version}/",
            f"--output={destination / f'cursor-panel-v{version}-source.{suffix}'}", revision)
    proof = {"version": version, "source_revision": revision, "parts": records,
             "run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    with zipfile.ZipFile(destination / f"cursor-panel-v{version}-verification.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("verification.json", json.dumps(proof, ensure_ascii=False, indent=2))
    (destination / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in sorted(destination.iterdir())), encoding="utf-8")
    manifest = verify_assets(destination, version)
    write_json(OUTPUT / "manifest.json", {"version": version, "source_revision": revision, "assets": manifest})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["check", "desktop", "server", "assemble"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--target", choices=TARGETS[:-1])
    args = parser.parse_args()
    check(args.version, args.revision)
    if args.operation == "desktop":
        if not args.target:
            parser.error("desktop requires --target")
        desktop(args.target, args.version, args.revision)
    elif args.operation == "server":
        server(args.version, args.revision)
    elif args.operation == "assemble":
        assemble(args.version, args.revision)
    print(json.dumps({"step": args.operation, "version": args.version, "revision": args.revision, "status": "passed"}))


if __name__ == "__main__":
    main()
