"""Packaged desktop entry point. Bootstrap and parent lifetime use inherited pipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def startup_failure(error, stage):
    # Only fixed categories and numeric OS codes cross the pipe. Exception
    # messages/tracebacks may contain account material, paths or SQL values.
    name = type(error).__name__
    code = {"bootstrap": "bootstrap", "imports": "imports", "runtime": "runtime",
            "listener": "listener"}.get(stage, "runtime")
    if isinstance(error, ImportError):
        code = "imports"
    elif name == "Locked":
        code = "data_in_use"
    elif isinstance(error, PermissionError) or (name == "Conflict" and
            str(error) == "Cannot restrict private desktop file permissions"):
        code = "data_permissions"
    elif isinstance(error, OSError) and stage == "runtime":
        code = "data_io"
    kind = name if name in {"KeyError", "TypeError", "ValueError", "FileNotFoundError", "FileExistsError",
                           "PermissionError", "OSError", "ImportError", "ModuleNotFoundError", "RuntimeError",
                           "Conflict", "Locked", "SecretError", "OperationalError", "DatabaseError", "TimeoutExpired"} else "Exception"
    return {"error": {"code": code, "kind": kind, "os_error": getattr(error, "winerror", None) or
                     getattr(error, "errno", None)}}


def main(progress):
    started = time.monotonic()
    bootstrap = json.loads(sys.stdin.buffer.readline(16384))
    token, data_dir = bootstrap.get("token"), bootstrap.get("data_dir")
    if (not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token)
            or not isinstance(data_dir, str) or not Path(data_dir).is_absolute()):
        raise ValueError("Invalid desktop bootstrap")
    parent_gone = threading.Event()
    parent_fd = sys.stdin.fileno()

    def watch_parent():
        try:
            # A daemon blocked in BufferedReader.read holds stdin's lock and
            # causes _enter_buffered_busy to abort during interpreter shutdown
            # if startup fails while the parent still holds its pipe open.
            while os.read(parent_fd, 1):
                pass
        except OSError:
            pass
        finally:
            parent_gone.set()
    threading.Thread(target=watch_parent, daemon=True).start()
    progress["stage"] = "imports"
    import asyncio
    import uvicorn
    from cursor_dashboard.local.api import create_local_app
    from cursor_dashboard.local.runtime import DesktopRuntime
    imported = time.monotonic()
    progress["stage"] = "runtime"
    kwargs = {}
    fixture = bootstrap.get("fixture") is True
    if fixture:
        from desktop_fixture import FixtureInstallation, PreviewGateway, seed
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        kwargs = {"gateway": PreviewGateway(), "installation": FixtureInstallation(data_dir), "script_preview": True}
    runtime = DesktopRuntime(Path(data_dir), **kwargs)
    if fixture and runtime.core:
        asyncio.run(seed(runtime))
    opened = time.monotonic()
    progress["stage"] = "listener"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(json.dumps({"port": port, "pid": os.getpid(), "protocol": 1,
                    "frozen": bool(getattr(sys, "frozen", False)), "fixture": fixture,
                    "imports_ms": round((imported - started) * 1000),
                    "core_ms": round((opened - imported) * 1000),
                    "listener_ms": round((time.monotonic() - opened) * 1000)}), flush=True)

        async def on_tick(self, counter):
            return parent_gone.is_set() or await super().on_tick(counter)

    server = Server(uvicorn.Config(create_local_app(runtime, token, port), log_config=None,
        log_level="critical", access_log=False, loop="asyncio", http="h11", ws="none",
        proxy_headers=False, timeout_graceful_shutdown=60))
    try:
        asyncio.run(server.serve(sockets=[listener]))
    finally:
        listener.close()


if __name__ == "__main__":
    progress = {"stage": "bootstrap"}
    try:
        main(progress)
    except Exception as error:
        print(json.dumps(startup_failure(error, progress["stage"])), flush=True)
        sys.exit(1)
