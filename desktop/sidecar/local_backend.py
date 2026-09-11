"""Packaged desktop entry point. Bootstrap and parent lifetime use inherited pipes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def main():
    started = time.monotonic()
    bootstrap = json.loads(sys.stdin.buffer.readline(16384))
    token, data_dir = bootstrap.get("token"), bootstrap.get("data_dir")
    if (not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token)
            or not isinstance(data_dir, str) or not Path(data_dir).is_absolute()):
        raise ValueError("Invalid desktop bootstrap")
    parent_gone = threading.Event()

    def watch_parent():
        while sys.stdin.buffer.read(1):
            pass
        parent_gone.set()
    threading.Thread(target=watch_parent, daemon=True).start()
    import asyncio
    import uvicorn
    from cursor_dashboard.local.api import create_local_app
    from cursor_dashboard.local.runtime import DesktopRuntime
    imported = time.monotonic()
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
    try:
        main()
    except Exception:
        # Exceptions can contain paths, authorization input or SQL parameters. No traceback on the pipe.
        sys.exit(1)
