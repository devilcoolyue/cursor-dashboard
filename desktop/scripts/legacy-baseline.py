"""Run legacy tests with temporary storage and preserve the original test behavior."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
output = root / "desktop" / "output"
output.mkdir(parents=True, exist_ok=True)
tests = ["discover", "-s", "tests", "-v"]
if sys.platform == "win32":
    # The complete legacy suite has unconditional /bin/bash calls. Exercise the
    # Windows-specific classes here, and run the full suite on macOS unchanged.
    tests = ["-v", "test_desktop.PowerShellDiscoveryTest", "test_desktop.PowerShellProgressTest",
             "test_switch_links.DownloadCommandTest.test_powershell_download_success_http_failure_and_partial_transfer"]
with tempfile.TemporaryDirectory(prefix="p0-legacy-") as folder:
    env = dict(os.environ, DATABASE_PATH=f"{folder}/accounts.db", ACCOUNTS_PATH=f"{folder}/missing.json")
    if sys.platform == "win32":
        env["PYTHONPATH"] = str(root / "tests")
    result = subprocess.run(["uv", "run", "--frozen", "python", "-m", "unittest", *tests],
                            cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace")
    (output / "legacy-baseline.txt").write_text(result.stdout, encoding="utf-8")
    print(result.stdout)
    (output / "legacy-baseline.json").write_text(json.dumps({
        "platform": sys.platform, "scope": "PowerShell-specific" if sys.platform == "win32" else "full",
        "exit_code": result.returncode, "selection": tests,
    }, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(result.returncode)
