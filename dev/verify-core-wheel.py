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
import socket
import time
import urllib.request
import urllib.error
import http.cookiejar
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--web", action="store_true", help="Require built P3 Web assets and exercise remote CLI")
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
        # Initialize a real server identity through the installed CLI, with
        # synthetic password supplied by a test-only getpass replacement.
        script = root / "initialize.py"
        script.write_text("from unittest.mock import patch\n"
            "from cursor_dashboard.runtime.cli import main\n"
            "import sys\n"
            "with patch('cursor_dashboard.runtime.cli.getpass.getpass', return_value='Synthetic wheel password 42!'):\n"
            "    raise SystemExit(main(sys.argv[1:]))\n")
        subprocess.run([str(python), str(script), *base[1:], "server-init", "--login", "owner@example.test"],
                       cwd=root, env=env, capture_output=True, check=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        api = binary_dir / ("cursor-api.exe" if os.name == "nt" else "cursor-api")
        server_env = {**env, "CURSOR_CORE_DATA_DIR": str(data_dir), "CURSOR_CORE_KEY_FILE": str(key),
                      "CURSOR_CORE_MODE": "server"}
        process = subprocess.Popen([str(api), "--port", str(port), "--public-origin", origin],
            cwd=root, env=server_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            cookies = http.cookiejar.CookieJar()
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
            deadline = time.monotonic() + 20
            while True:
                try:
                    with client.open(origin + "/api/v1/bootstrap", timeout=1) as response:
                        assert json.load(response)["initialized"] is True
                    break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Installed API failed to start") from None
                    time.sleep(.1)
            if args.web:
                with client.open(origin + "/", timeout=5) as response:
                    assert "Cursor Panel" in response.read().decode()
                    assert "frame-ancestors" in response.headers["Content-Security-Policy"]
            body = json.dumps({"login": "owner@example.test", "password": "Synthetic wheel password 42!"}).encode()
            request = urllib.request.Request(origin + "/api/v1/auth/login", data=body,
                headers={"Content-Type": "application/json", "Origin": origin})
            with client.open(request, timeout=10) as response:
                csrf = json.load(response)["csrf_token"]
            with client.open(origin + "/api/v1/me", timeout=5) as response:
                me = json.load(response)
                assert me["id"] == owner["user_id"]
                assert len(me["workspaces"]) == 2
            with client.open(origin + f"/api/v1/workspaces/{owner['workspace_id']}/accounts", timeout=5) as response:
                result = json.load(response)
                assert result["total"] == 1
                assert "fixture-cookie-only" not in json.dumps(result)
            if args.web:
                with client.open(origin + "/api/v1/auth/sessions", timeout=5) as response:
                    before_sessions = len(json.load(response))
                remote = root / "remote.py"
                remote.write_text("from unittest.mock import patch\n"
                    "from cursor_dashboard.runtime.remote import main\nimport sys\n"
                    "with patch('cursor_dashboard.runtime.remote.getpass.getpass', return_value='Synthetic wheel password 42!'):\n"
                    "    raise SystemExit(main(sys.argv[1:]))\n")
                result = subprocess.run([str(python), str(remote), "--server", origin, "--login", "owner@example.test",
                    "list", "--workspace", owner["workspace_id"]], cwd=root, env=env, text=True, encoding="utf-8", capture_output=True, check=True)
                assert json.loads(result.stdout)["total"] == 1
                assert "fixture-cookie-only" not in result.stdout
                assert "Synthetic wheel password" not in result.stdout + result.stderr
                with client.open(origin + "/api/v1/auth/sessions", timeout=5) as response:
                    assert len(json.load(response)) == before_sessions, "Remote CLI left an active session"
            request = urllib.request.Request(origin + "/api/v1/auth/logout", data=b"", headers={"Origin": origin, "X-CSRF-Token": csrf})
            with client.open(request, timeout=5) as response:
                assert response.status == 204
            try:
                client.open(origin + "/api/v1/me", timeout=5)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("Logout did not revoke the session")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        assert run("verify")["credentials_decryptable"] == 1
        print("Installed wheel: Web/remote CLI checks enabled=" + str(args.web) + "; " + " migration/recovery CLI and real API login/list/logout passed; source unchanged")


if __name__ == "__main__":
    main()
