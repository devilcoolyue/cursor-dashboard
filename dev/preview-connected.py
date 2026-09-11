"""P5 isolated HTTP fixtures: browser approval, native callback and synthetic Cursor.

Only this development entry point enables remote-switch fixtures. It takes a
private native-channel token on stdin and never reads real user data or accounts.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile

import uvicorn

from cursor_dashboard.api.app import create_app
from cursor_dashboard.local.api import create_local_app
from cursor_dashboard.local.remote import Connections, RemoteError, remote_http
from cursor_dashboard.local.runtime import DesktopRuntime
from cursor_dashboard.infrastructure.secrets import FileKeyProvider
from cursor_dashboard.runtime.core import Core
from cursor_dashboard.runtime.settings import CoreConfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop/sidecar"))
from desktop_fixture import FixtureInstallation, PreviewGateway, seed as seed_local  # noqa: E402

spec = importlib.util.spec_from_file_location("preview_v2", ROOT / "dev/preview-v2.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class MemoryKeys:
    keys = None
    def read(self):
        return self.keys
    def save(self, keys):
        self.keys = keys


class MemoryDevices:
    def __init__(self):
        self.values = {}
    def read_session(self, key):
        return self.values.get(key)
    def save_session(self, key, value):
        self.values[key] = value
    def delete_session(self, key):
        self.values.pop(key, None)


def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


async def main():
    settings = json.loads(sys.stdin.readline())
    token = settings["token"]
    assert len(token) == 64
    with tempfile.TemporaryDirectory(prefix="p5-http-fixture-") as directory:
        root = Path(directory)
        local_sock, server_sock = listener(), listener()
        local_port, server_port = local_sock.getsockname()[1], server_sock.getsockname()[1]
        server_origin = f"http://127.0.0.1:{server_port}"
        key = root / "server-key.json"
        FileKeyProvider.create(key)
        with Core(CoreConfig(root / "server", key, mode="server", request_interval=0, refresh_margin=0),
                  initialize=True, gateway=PreviewGateway()) as core:
            await preview.seed(core)
            public = create_app(core, public_origin=server_origin, web_dir=ROOT / "frontend/dist", _device_switch_test=True)
            local_dir = root / "local"
            local_dir.mkdir()
            opened, fixture = [], {"offline": False, "incompatible": False, "switch": False}
            def http(origin, method, path, **kwargs):
                if fixture["offline"]:
                    raise RemoteError(502, "Synthetic server offline")
                result = remote_http(origin, method, path, **kwargs)
                if path == "/api/v1/bootstrap":
                    result["api_version"] = 2 if fixture["incompatible"] else 1
                    result["capabilities"]["remote_switch"] = fixture["switch"]
                return result
            connections = Connections(local_dir, store=MemoryDevices(), transport=http,
                                      browser=lambda url: opened.append(url) or True)
            runtime = DesktopRuntime(local_dir, store=MemoryKeys(), gateway=PreviewGateway(),
                installation=FixtureInstallation(local_dir), connections=connections, script_preview=True)
            await seed_local(runtime)
            local = create_local_app(runtime, token, local_port)

            @local.get("/fixture/browser")
            def browser_url():
                return {"url": opened[-1] if opened else None}

            @local.post("/fixture/state")
            def change_fixture(body: dict):
                for key in fixture:
                    if key in body:
                        fixture[key] = body[key] is True
                return fixture

            # The private middleware still applies to fixture control routes.
            mount = next(route for route in local.routes if getattr(route, "path", None) == "")
            local.routes.remove(mount)
            local.routes.append(mount)
            def server(app, port):
                return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                    log_level="critical", access_log=False, proxy_headers=False, timeout_graceful_shutdown=10))
            servers = [server(public, server_port), server(local, local_port)]
            jobs = [asyncio.create_task(s.serve(sockets=[sock])) for s, sock in zip(servers, (server_sock, local_sock))]
            try:
                while not all(s.started for s in servers):
                    if any(job.done() for job in jobs):
                        await asyncio.gather(*jobs)
                    await asyncio.sleep(.02)
                print(json.dumps({"local_port": local_port, "server_origin": server_origin}), flush=True)
                await asyncio.to_thread(sys.stdin.read)
            finally:
                for s in servers:
                    s.should_exit = True
                await asyncio.gather(*jobs)
                local_sock.close()
                server_sock.close()


if __name__ == "__main__":
    asyncio.run(main())
