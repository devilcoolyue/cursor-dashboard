from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
from pathlib import Path
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr

from ..api.app import Input
from ..domain.core import CoreError, SecretError
from .archive import export_archive, import_archive


class SwitchInput(Input):
    workspace_id: uuid.UUID
    account_id: uuid.UUID
    confirmed: bool = Field(strict=True)


class RestoreInput(Input):
    backup_id: uuid.UUID
    confirmed: bool = Field(strict=True)


class BackgroundInput(Input):
    enabled: bool = Field(strict=True)


class ArchiveInput(Input):
    path: str = Field(min_length=1, max_length=4096)
    password: SecretStr = Field(min_length=12, max_length=256)
    workspace_id: uuid.UUID | None = None


def create_local_app(runtime, token, port):
    @asynccontextmanager
    async def lifespan(app):
        scheduler = asyncio.create_task(runtime.scheduler())
        try:
            yield
        finally:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler
            await runtime.shutdown()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def private_channel(request: Request, call_next):
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"detail": "Invalid host"}, status_code=403)
        if "origin" in request.headers or any(key.startswith("sec-fetch-") for key in request.headers):
            return JSONResponse({"detail": "Native channel required"}, status_code=403)
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, f"Bearer {token}".encode("ascii")):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(CoreError)
    async def core_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=423 if isinstance(error, SecretError) else 409)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({"detail": "Invalid request fields"}, status_code=422)

    @app.get("/native/status")
    def status():
        return runtime.status()

    @app.post("/native/unlock")
    def unlock():
        return runtime.open()

    @app.post("/native/recover")
    def recover(body: ArchiveInput):
        return runtime.recover(Path(body.path), body.password.get_secret_value())

    @app.get("/native/cursor")
    def cursor():
        return runtime.executor.installation.detect()

    @app.get("/native/switch")
    def switch_status():
        return runtime.executor.status()

    @app.post("/native/switch")
    async def switch(body: SwitchInput):
        if not body.confirmed:
            return JSONResponse({"detail": "Save and confirm before switching"}, status_code=409)
        return runtime.start_switch(str(body.workspace_id), str(body.account_id))

    @app.get("/native/backups")
    def backups():
        return runtime.executor.backups()

    @app.post("/native/restore")
    async def restore(body: RestoreInput):
        if not body.confirmed:
            return JSONResponse({"detail": "Save and confirm before restoring"}, status_code=409)
        return runtime.start_restore(str(body.backup_id))

    @app.put("/native/background")
    def background(body: BackgroundInput):
        return runtime.set_background(body.enabled)

    @app.post("/native/resume", status_code=204)
    def resume():
        runtime.resume()

    @app.post("/native/archive/{action}")
    def archive(action: str, body: ArchiveInput):
        core = runtime.require()
        if action not in {"export", "import"} or body.workspace_id is None:
            return JSONResponse({"detail": "Invalid archive operation"}, status_code=422)
        actor = runtime.identity.actor()
        args = (core, actor, str(body.workspace_id), Path(body.path), body.password.get_secret_value())
        if action == "export":
            return export_archive(*args, runtime.keys)
        return import_archive(*args)

    async def business(scope, receive, send):
        if runtime.business is None:
            await JSONResponse({"detail": "Local account store is locked"}, status_code=423)(scope, receive, send)
        else:
            await runtime.business(scope, receive, send)
    app.mount("/", business)
    return app
