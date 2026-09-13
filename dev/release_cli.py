"""Small release entry point: prepare once, dispatch once, inspect one concise status."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from release import ROOT, check_versions, git
from cursor_dashboard.updates.releases import REPOSITORY, version_tuple

STATE = ROOT / "output/release-state.json"


def command(*args):
    return subprocess.check_output([str(arg) for arg in args], cwd=ROOT, text=True).strip()


def prepare(version, notes, root=ROOT):
    current = check_versions(root)
    if version_tuple(version) <= version_tuple(current):
        raise ValueError("Choose a version greater than the current package version")
    body = notes.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError("Release notes cannot be empty")
    changes = {}
    simple = ["pyproject.toml", "cursor_dashboard/__init__.py", "desktop/sidecar/pyproject.toml",
              "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/tauri.conf.json", "desktop/package.json",
              "frontend/package.json", "Dockerfile", "deploy/v2/.env.example", "deploy/v2/compose.yaml",
              "README.md", "README_CN.md", "docs/automatic-updates.md", "docs/supported-platforms.md",
              "docs/v2-release-operations.md"]
    for name in simple:
        text = (root / name).read_text(encoding="utf-8")
        if current not in text:
            raise ValueError(f"Expected current version in {name}; inspect before preparing release")
        changes[name] = text.replace(current, version)
    # Keep historical notes and old upgrade references intact.
    old = f"archive/v{current}.md"
    release_ops = (root / "docs/v2-release-operations.md").read_text(encoding="utf-8")
    changes["docs/v2-release-operations.md"] = release_ops.replace(f"当前包版本为 `{current}`", f"当前包版本为 `{version}`")
    changes["docs/v2-release-operations.md"] = changes["docs/v2-release-operations.md"].replace(
        f"`v{current}` 的变更与升级说明见[本次版本归档]({old})",
        f"`v{version}` 的变更与升级说明见[本次版本归档](archive/v{version}.md)")
    changes["docs/v2-release-operations.md"] = re.sub(
        r"旧版附件保留在 \[v[\d.]+ 归档\]\(archive/v[\d.]+\.md\)",
        f"旧版附件保留在 [v{current} 归档]({old})", changes["docs/v2-release-operations.md"])
    changes["docs/v2-release-operations.md"] = changes["docs/v2-release-operations.md"].replace(
        f"cursor_dashboard-{current}-py3-none-any.whl", f"cursor_dashboard-{version}-py3-none-any.whl")
    name = "docs/v2-desktop-operations.md"
    changes[name] = (root / name).read_text(encoding="utf-8").replace(f"当前版本为 `{current}`", f"当前版本为 `{version}`")
    for name in ("frontend/package-lock.json", "desktop/package-lock.json"):
        data = json.loads((root / name).read_text(encoding="utf-8"))
        data["version"] = data["packages"][""]["version"] = version
        changes[name] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    for name in ("uv.lock", "desktop/sidecar/uv.lock", "desktop/src-tauri/Cargo.lock"):
        text = (root / name).read_text(encoding="utf-8")
        pattern = r'(\[\[package\]\]\nname = "(?:cursor-dashboard|cursor-panel-desktop|cursor-panel-desktop-sidecar)"\nversion = ")' + re.escape(current) + r'(")'
        text, count = re.subn(pattern, lambda match: match[1] + version + match[2], text)
        if not count:
            raise ValueError(f"First-party version missing from {name}")
        changes[name] = text
    archive = f"docs/archive/v{version}.md"
    if (root / archive).exists():
        raise ValueError("Release notes for this version already exist")
    changes[archive] = body + "\n"
    # Validate every input before touching any file; never write partial version updates on a missing file.
    originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in changes}
    try:
        for name, text in changes.items():
            (root / name).write_text(text, encoding="utf-8")
        if check_versions(root) != version:
            raise ValueError("Version preparation did not converge")
    except BaseException:
        for name, content in originals.items():
            if content is None:
                (root / name).unlink(missing_ok=True)
            else:
                (root / name).write_bytes(content)
        raise
    return {"version": version, "changed_files": list(changes), "next": "Review, commit and push; then run start"}


def start(publish):
    from release_pipeline import check
    version, revision = check_versions(), git("rev-parse", "HEAD")
    check(version, revision)
    branch = git("branch", "--show-current")
    if not branch:
        raise ValueError("Dispatch from a pushed branch")
    repo = json.loads(command("gh", "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
    if repo != REPOSITORY:
        raise ValueError("Release entry point requires the Cursor Panel repository")
    remote = json.loads(command("gh", "api", f"repos/{repo}/git/ref/heads/{branch}"))
    if remote["object"]["sha"] != revision:
        raise ValueError("Push this exact commit before dispatching")
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        if previous["revision"] == revision and previous["publish"] == publish:
            state = json.loads(command("gh", "run", "view", previous["run_id"], "--json", "status,conclusion"))
            if state["status"] != "completed" or state["conclusion"] == "success":
                return {**previous, "reused": True}
            raise ValueError(f"Run {previous['run_id']} failed; inspect status and resume instead of rebuilding everything")
    if publish:
        from release_publish import GitHub, Publisher
        Publisher(GitHub(), version, revision, Path(), {}, "").check_available()
    output = command("gh", "workflow", "run", "release.yml", "--ref", branch,
                     "-f", f"version={version}", "-f", f"expected_sha={revision}", "-f", f"publish={str(publish).lower()}")
    match = re.search(r"https://github\.com/[^/]+/[^/]+/actions/runs/(\d+)", output)
    if not match:
        raise ValueError("Workflow dispatched but CLI did not return a run URL; inspect gh run list before retrying")
    state = {"version": version, "revision": revision, "publish": publish, "run_id": match[1], "url": match[0]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    return state


def status(run_id=None):
    if not run_id:
        if not STATE.exists():
            raise ValueError("No saved release run; pass --run")
        run_id = json.loads(STATE.read_text())["run_id"]
    value = json.loads(command("gh", "run", "view", run_id, "--json", "status,conclusion,jobs,url"))
    return {"run_id": run_id, "url": value["url"], "status": value["status"], "conclusion": value["conclusion"],
            "jobs": [{"name": job["name"], "status": job["status"], "conclusion": job["conclusion"],
                      "current": [s["name"] for s in job["steps"] if s["status"] == "in_progress"],
                      "failed": [s["name"] for s in job["steps"] if s["conclusion"] == "failure"]} for job in value["jobs"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    prep = commands.add_parser("prepare", help="Update local versions and notes only; never commit or publish")
    prep.add_argument("version")
    prep.add_argument("--notes", type=Path, required=True)
    launch = commands.add_parser("start", help="Dispatch one release workflow; CI continues without local polling")
    launch.add_argument("--publish", action="store_true")
    state = commands.add_parser("status")
    state.add_argument("--run")
    resume = commands.add_parser("resume", help="Rerun failed jobs only, preserving successful target artifacts")
    resume.add_argument("--run")
    args = parser.parse_args()
    if args.operation == "prepare":
        result = prepare(args.version, args.notes)
    elif args.operation == "start":
        result = start(args.publish)
    else:
        result = status(args.run)
        if args.operation == "resume":
            if result["status"] != "completed" or result["conclusion"] == "success":
                raise ValueError("Only completed failed runs can be resumed")
            command("gh", "run", "rerun", result["run_id"], "--failed")
            result = {"resumed": result["run_id"], "url": result["url"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
