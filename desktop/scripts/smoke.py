"""Launch a relocated release bundle with developer tools removed from PATH.

The installed Vue page acknowledges the actual DOM after fetching demo data.
Only this script's process tree is inspected. No user accounts or Cursor data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import psutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--kill-shell", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix="Cursor Panel P0 空格 ") as directory:
        root = Path(directory)
        if sys.platform == "darwin":
            app = next(parent for parent in source.parents if parent.suffix == ".app")
            copy = root / app.name
            shutil.copytree(app, copy, symlinks=True)
            executable = copy / source.relative_to(app)
        else:
            executable = root / source.name
            shutil.copy2(source, executable)
            shutil.copy2(source.with_name("p0-backend.exe"), root / "p0-backend.exe")
        report = root / "report.json"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PYTHON", "VIRTUAL_ENV", "CONDA", "DYLD_", "LD_LIBRARY_"))}
        env["PATH"] = str(root / "no-developer-tools")
        if sys.platform == "win32":
            env["PATH"] += os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
        started = time.monotonic()
        child = subprocess.Popen([str(executable), "--p0-smoke", str(report)], cwd=root,
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        tracked = {}
        observations = []
        data = None
        killed = False
        try:
            while time.monotonic() - started < 75:
                try:
                    process = psutil.Process(child.pid)
                    for item in [process, *process.children(recursive=True)]:
                        tracked[item.pid] = item
                except psutil.NoSuchProcess:
                    pass
                if report.exists() and data is None:
                    data = json.loads(report.read_text(encoding="utf-8"))
                    data["launch_to_report_ms"] = round((time.monotonic() - started) * 1000)
                    data["relocated_path_with_spaces"] = True
                    data["developer_tools_removed_from_path"] = True
                    data["executable_bytes"] = executable.stat().st_size
                    data["relocated_bundle_bytes"] = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
                if data:
                    rss = 0
                    for process in tracked.values():
                        try:
                            rss += process.memory_info().rss
                        except psutil.NoSuchProcess:
                            pass
                    observations.append(rss)
                    if args.kill_shell and not killed:
                        child.kill()
                        killed = True
                if child.poll() is not None:
                    break
                time.sleep(.2)
            if child.poll() is None:
                raise RuntimeError("No clean exit within 75 seconds")
            if data is None:
                raise RuntimeError("Packaged Vue page did not report readiness")
            deadline = time.monotonic() + 8
            survivors = []
            while time.monotonic() < deadline:
                survivors = [p for p in tracked.values() if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
                if not survivors:
                    break
                time.sleep(.1)
            data["process_tree_cleaned"] = not survivors
            data["shell_killed"] = args.kill_shell
            data["exit_code"] = child.returncode
            data["idle_process_tree_rss_bytes"] = observations[len(observations) // 2] if observations else None
            data["memory_scope"] = "shell + observed descendants; shared pages may be double-counted; OS-managed WebView processes may be excluded"
            args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            assert data["backend"]["frozen"], "Python was not bundled"
            assert data["frontend"]["rendered_accounts"] == 2, "Vue did not render demo accounts"
            assert data["keyring"]["ok"], f'System credential store failed: {data["keyring"]["detail"]}'
            assert data["process_tree_cleaned"], "Backend survived shell exit"
            assert args.kill_shell or child.returncode == 0, "Shell failed"
            print(json.dumps(data, ensure_ascii=False, indent=2))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for process in tracked.values():
                try:
                    if process.is_running():
                        process.kill()
                except psutil.NoSuchProcess:
                    pass
            if child.stderr:
                child.stderr.close()


if __name__ == "__main__":
    main()
