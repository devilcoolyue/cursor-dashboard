"""Exercise the installed V2 maintenance CLI from an unrelated temporary directory."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="core-wheel-") as folder:
        root = Path(folder)
        environment = root / "environment"
        subprocess.run(["uv", "venv", str(environment), "--python", "3.12"], check=True)
        binary_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary_dir / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        executable = binary_dir / ("cursor-core.exe" if os.name == "nt" else "cursor-core")
        data_dir, key = root / "state", root / "key.json"
        base = [str(executable), "--data-dir", str(data_dir), "--key-file", str(key)]
        env = {name: value for name, value in os.environ.items() if name not in {"PYTHONPATH", "VIRTUAL_ENV"}}
        env["PYTHONUTF8"] = "1"
        def run(*arguments):
            result = subprocess.run([*base, *arguments], cwd=root, env=env, capture_output=True,
                                    text=True, encoding="utf-8", check=True)
            return json.loads(result.stdout)
        run("keygen")
        owner = run("init", "--owner", "owner@example.test", "--name", "Import smoke")
        source = root / "legacy.db"
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, label TEXT, cookie TEXT, email TEXT, department TEXT)")
            conn.execute("INSERT INTO accounts VALUES (1, 'Fixture', 'fixture-cookie-only', 'fixture@example.test', 'Engineering')")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        assert run("preflight", str(source))["accounts"] == 1
        target = ["--actor", owner["user_id"], "--workspace", owner["workspace_id"]]
        assert run("import-legacy", str(source), *target)["already_imported"] is False
        assert run("import-legacy", str(source), *target)["already_imported"] is True
        assert run("list", *target)[0]["tags"] == ["Engineering"]
        assert run("verify")["credentials_decryptable"] == 1
        assert run("upgrade")["accounts"] == 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert b"fixture-cookie-only" not in (data_dir / "core.db").read_bytes()
        print("Installed wheel: keygen/init/preflight/import/repeat/list/verify/upgrade passed; source unchanged")


if __name__ == "__main__":
    main()
