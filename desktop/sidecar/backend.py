"""P0-only backend: bundled fixtures, no production imports, no outbound requests."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn


def create_app(token: str, port: int) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    fixture = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))

    @app.middleware("http")
    async def private_channel(request: Request, call_next):
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"detail": "Invalid host"}, status_code=403)
        # The native HTTP client supplies no Origin. A browser never talks here.
        if "origin" in request.headers or request.headers.get("sec-fetch-site"):
            return JSONResponse({"detail": "Native channel required"}, status_code=403)
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, f"Bearer {token}".encode("ascii")):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    def health():
        # Exercise the bundled SQLite module without opening any user database.
        with sqlite3.connect(":memory:") as conn:
            conn.execute("SELECT 1").fetchone()
        return {"api_version": 1, "frozen": bool(getattr(sys, "frozen", False)),
                "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "pid": os.getpid()}

    @app.get("/api/v1/demo")
    def demo():
        return fixture

    return app


def main() -> None:
    # Input is inherited stdin, never a command-line argument, URL or environment value.
    bootstrap = json.loads(sys.stdin.buffer.readline(4096))
    token = bootstrap.get("token", "")
    if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid bootstrap")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    parent_gone = threading.Event()

    def watch_parent():
        # Closing/killing the shell closes its pipe. This also works through the onefile bootloader.
        while sys.stdin.buffer.read(1):
            pass
        parent_gone.set()

    threading.Thread(target=watch_parent, daemon=True).start()

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(json.dumps({"port": port, "pid": os.getpid(), "protocol": 1}), flush=True)

        async def on_tick(self, counter):
            return parent_gone.is_set() or await super().on_tick(counter)

    server = Server(uvicorn.Config(create_app(token, port), host="127.0.0.1", port=port,
                                  log_config=None, log_level="critical", access_log=False,
                                  loop="asyncio", http="h11", ws="none", timeout_graceful_shutdown=2))
    try:
        asyncio.run(server.serve(sockets=[listener]))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
