from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import models as dto
from .manual_switch import render_script
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..application.queries import QueryFailure
from ..application.security import digest
from ..client import AuthExpired, RateLimited
from ..desktop import DesktopSessionError
from ..domain.core import (CoreError, Forbidden, NotFound, SecretError,
                            Throttled, Unauthenticated)
from ..infrastructure.persistence.policy import audit

import requests


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    login: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=256)


class PasswordChange(Input):
    current_password: SecretStr = Field(max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=256)


class WorkspaceCreate(Input):
    name: str = Field(min_length=1, max_length=128)


class Invite(Input):
    login: str = Field(min_length=1, max_length=320)
    role: str = Field(pattern="^(admin|member|viewer)$")


class AcceptInvite(Input):
    token: SecretStr = Field(min_length=20, max_length=128)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=256)


class RoleChange(Input):
    role: str = Field(pattern="^(admin|member|viewer)$")


class OwnerChange(Input):
    user_id: uuid.UUID


class GrantChange(Input):
    level: str = Field(pattern="^(view|use)$")


class UserState(Input):
    active: bool = Field(strict=True)


class AccountEdit(Input):
    label: str | None = Field(default=None, min_length=1, max_length=256)
    tags: list[str] | None = Field(default=None, max_length=100)


class Authorization(Input):
    cookie: SecretStr = Field(min_length=1, max_length=16384)
    label: str | None = Field(default=None, min_length=1, max_length=256)
    tags: list[str] = Field(default_factory=list, max_length=100)


class ManualConsume(Input):
    token: SecretStr = Field(min_length=20, max_length=128)
    platform: Literal["macos", "windows"]


def validate_origin(public_origin):
    parsed = urlsplit(public_origin)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise CoreError("Public origin must be an http(s) origin without path or credentials")
    try:
        _ = parsed.port
    except ValueError:
        raise CoreError("Public origin has an invalid port") from None
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise CoreError("External server origins require HTTPS")
    return parsed


def create_app(core, *, public_origin, web_dir=None, manual_switch_preview=False, _local=None):
    parsed = validate_origin(public_origin)
    if _local is not None and (core.config.mode != "local" or _local.core is not core):
        raise CoreError("Private desktop identity requires its own local core")
    if _local is None and core.config.mode != "server":
        raise CoreError("HTTP requires explicit server mode")
    if _local is None and not core.identity.initialized():
        raise CoreError("Initialize server authentication with cursor-core server-init before listening")
    secure = parsed.scheme == "https"
    cookie_name = "__Host-cursor_session" if secure else "cursor_session"
    app = FastAPI(title="Cursor Dashboard V2", version="1", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core = core

    def error_response(status, message):
        return JSONResponse({"detail": message}, status_code=status)

    @app.middleware("http")
    async def boundary(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        origin = request.headers.get("origin")
        if _local is not None:
            # The outer native middleware authenticates every request, including reads.
            response = await call_next(request)
        elif request.headers.get("host", "").lower() != parsed.netloc.lower():
            response = error_response(400, "Invalid host")
        elif (origin is not None and origin != public_origin) or request.headers.get("sec-fetch-site") == "cross-site":
            response = error_response(403, "Invalid origin")
        elif request.method not in {"GET", "HEAD", "OPTIONS"} and origin != public_origin:
            response = error_response(403, "Same-origin requests are required")
        else:
            length = request.headers.get("content-length", "0")
            if not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > 65536:
                response = error_response(413, "Request body is too large")
            else:
                # Bound chunked input as well as Content-Length before JSON parsing.
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        break
                if len(body) > 65536:
                    response = error_response(413, "Request body is too large")
                else:
                    request._body = bytes(body)
                    response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(CoreError)
    async def core_error(request, error):
        status = (401 if isinstance(error, Unauthenticated) else 403 if isinstance(error, Forbidden)
                  else 404 if isinstance(error, NotFound) else 429 if isinstance(error, Throttled)
                  else 503 if isinstance(error, SecretError) else 409)
        # Failed attempts contain only server-defined route identifiers and UUIDs.
        actor = getattr(request.state, "actor", None)
        route = request.scope.get("route")
        if actor and status in {401, 403, 404}:
            workspace = request.path_params.get("workspace_id")
            try:
                workspace = str(uuid.UUID(str(workspace))) if workspace else None
            except ValueError:
                workspace = None
            with core.db.transaction(write=True) as session:
                audit(session, actor, "http." + (route.name if route else "request"), workspace, result="denied")
        return error_response(status, str(error))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # FastAPI's default includes rejected input (including Cookie/password).
        return error_response(422, "Invalid request fields")

    async def provider_error(request, error):
        return error_response(502, "Provider request failed; retry or reauthorize the account")

    for error_type in (QueryFailure, AuthExpired, RateLimited, DesktopSessionError, requests.RequestException):
        app.add_exception_handler(error_type, provider_error)

    def current_actor(request: Request):
        if _local is not None:
            actor = _local.identity.actor(request.state.request_id)
            request.state.actor = actor
            return actor
        token = request.cookies.get(cookie_name, "")
        csrf = request.headers.get("x-csrf-token", "") if request.method not in {"GET", "HEAD", "OPTIONS"} else None
        actor = core.identity.authenticate(token, csrf=csrf, request_id=request.state.request_id)
        request.state.actor = actor
        return actor

    @app.get("/api/v1/bootstrap", response_model=dto.Bootstrap)
    def bootstrap():
        if _local is not None:
            return {"mode": "local", "initialized": True, "api_version": 1,
                    "capabilities": {"workspaces": False, "invitations": False,
                        "manual_switch": False, "native_switch": True, "archives": True,
                        "device_sessions": False, "remote_switch": False}}
        return {"mode": "server", "initialized": True, "api_version": 1,
                "capabilities": {"workspaces": True, "invitations": True,
                                 "manual_switch": True, "device_sessions": False, "remote_switch": False}}

    @app.post("/api/v1/auth/login", response_model=dto.LoginResult)
    def login(body: Login, request: Request):
        result = core.identity.login(body.login, body.password.get_secret_value(),
            source=request.client.host if request.client else "unknown", request_id=request.state.request_id)
        response = JSONResponse({"csrf_token": result.csrf, "expires_at": result.expires_at})
        response.set_cookie(cookie_name, result.token, max_age=core.identity.session_ttl,
                            httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.get("/api/v1/auth/csrf", response_model=dto.Csrf)
    def csrf(request: Request, actor=Depends(current_actor)):
        return {"csrf_token": digest("csrf:" + request.cookies[cookie_name])}

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(actor=Depends(current_actor)):
        core.identity.revoke_session(actor, actor.session_id)
        response = Response(status_code=204)
        response.delete_cookie(cookie_name, secure=secure, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/v1/me", response_model=dto.Me)
    def me(actor=Depends(current_actor)):
        return core.identity.me(actor)

    @app.put("/api/v1/auth/password", status_code=204)
    def password(body: PasswordChange, actor=Depends(current_actor)):
        core.identity.change_password(actor, body.current_password.get_secret_value(), body.new_password.get_secret_value())
        return Response(status_code=204)

    @app.get("/api/v1/auth/sessions", response_model=list[dto.SessionView])
    def sessions(actor=Depends(current_actor)):
        return core.identity.sessions(actor)

    @app.delete("/api/v1/auth/sessions/{session_id}", status_code=204)
    def revoke_session(session_id: uuid.UUID, actor=Depends(current_actor)):
        core.identity.revoke_session(actor, str(session_id))
        return Response(status_code=204)

    @app.post("/api/v1/workspaces", status_code=201, response_model=dto.WorkspaceCreated)
    def create_workspace(body: WorkspaceCreate, actor=Depends(current_actor)):
        return core.workspaces.create(actor, body.name)

    @app.delete("/api/v1/workspaces/{workspace_id}", status_code=204)
    def delete_workspace(workspace_id: uuid.UUID, actor=Depends(current_actor)):
        core.workspaces.delete(actor, str(workspace_id))
        return Response(status_code=204)

    @app.put("/api/v1/workspaces/{workspace_id}/owner", status_code=204)
    def transfer_owner(workspace_id: uuid.UUID, body: OwnerChange, actor=Depends(current_actor)):
        core.workspaces.transfer(actor, str(workspace_id), str(body.user_id))
        return Response(status_code=204)

    @app.get("/api/v1/workspaces/{workspace_id}/members", response_model=list[dto.MemberView])
    def members(workspace_id: uuid.UUID, actor=Depends(current_actor)):
        return core.workspaces.members(actor, str(workspace_id))

    @app.put("/api/v1/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    def role(workspace_id: uuid.UUID, user_id: uuid.UUID, body: RoleChange, actor=Depends(current_actor)):
        core.workspaces.set_role(actor, str(workspace_id), str(user_id), body.role)
        return Response(status_code=204)

    @app.delete("/api/v1/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    def remove_member(workspace_id: uuid.UUID, user_id: uuid.UUID, actor=Depends(current_actor)):
        core.workspaces.remove_member(actor, str(workspace_id), str(user_id))
        return Response(status_code=204)

    @app.post("/api/v1/workspaces/{workspace_id}/invitations", status_code=201, response_model=dto.InvitationIssued)
    def invite(workspace_id: uuid.UUID, body: Invite, actor=Depends(current_actor)):
        return core.workspaces.invite(actor, str(workspace_id), body.login, body.role)

    @app.get("/api/v1/workspaces/{workspace_id}/invitations", response_model=list[dto.InvitationView])
    def invitations(workspace_id: uuid.UUID, actor=Depends(current_actor)):
        return core.workspaces.invitations(actor, str(workspace_id))

    @app.delete("/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
    def revoke_invitation(workspace_id: uuid.UUID, invitation_id: uuid.UUID, actor=Depends(current_actor)):
        core.workspaces.revoke_invitation(actor, str(workspace_id), str(invitation_id))
        return Response(status_code=204)

    @app.post("/api/v1/invitations/accept", response_model=dto.Joined)
    def accept(body: AcceptInvite, request: Request):
        actor = current_actor(request) if request.cookies.get(cookie_name) else None
        return core.workspaces.accept(body.token.get_secret_value(), actor=actor,
            password=body.password.get_secret_value() if body.password else None,
            request_id=request.state.request_id, source=request.client.host if request.client else "unknown")

    @app.get("/api/v1/workspaces/{workspace_id}/accounts", response_model=dto.AccountPage)
    def accounts(workspace_id: uuid.UUID, q: str = Query("", max_length=256), tag: str | None = Query(None, max_length=128),
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), actor=Depends(current_actor)):
        return core.accounts.search(actor, str(workspace_id), query=q, tag=tag, limit=limit, offset=offset)

    @app.get("/api/v1/workspaces/{workspace_id}/accounts/{account_id}", response_model=dto.AccountView)
    def account(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        return core.accounts.get(actor, str(workspace_id), str(account_id))

    @app.post("/api/v1/workspaces/{workspace_id}/accounts", status_code=201, response_model=dto.AccountView)
    async def authorize_account(workspace_id: uuid.UUID, body: Authorization, actor=Depends(current_actor)):
        return await core.accounts.authorize(actor, str(workspace_id), body.cookie.get_secret_value(),
                                             label=body.label, tags=body.tags)

    @app.post("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/authorization", response_model=dto.AccountView)
    async def reauthorize_account(workspace_id: uuid.UUID, account_id: uuid.UUID, body: Authorization,
                                  actor=Depends(current_actor)):
        return await core.accounts.authorize(actor, str(workspace_id), body.cookie.get_secret_value(),
                                             label=body.label, tags=body.tags, account_id=str(account_id))

    @app.patch("/api/v1/workspaces/{workspace_id}/accounts/{account_id}", response_model=dto.AccountView)
    def edit_account(workspace_id: uuid.UUID, account_id: uuid.UUID, body: AccountEdit, actor=Depends(current_actor)):
        return core.accounts.edit(actor, str(workspace_id), str(account_id), **body.model_dump(exclude_none=True))

    @app.delete("/api/v1/workspaces/{workspace_id}/accounts/{account_id}", status_code=204)
    def delete_account(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        core.accounts.delete(actor, str(workspace_id), str(account_id))
        return Response(status_code=204)

    @app.post("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/refresh", response_model=dto.AccountView)
    async def refresh(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        return await core.accounts.refresh(actor, str(workspace_id), str(account_id))

    @app.get("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/detail", response_model=dto.DetailView)
    async def detail(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        return await core.accounts.detail(actor, str(workspace_id), str(account_id))

    @app.get("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/grants", response_model=list[dto.GrantView])
    def grants(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        return core.workspaces.grants(actor, str(workspace_id), str(account_id))

    @app.put("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/grants/{user_id}", status_code=204)
    def grant(workspace_id: uuid.UUID, account_id: uuid.UUID, user_id: uuid.UUID, body: GrantChange,
              actor=Depends(current_actor)):
        core.workspaces.set_grant(actor, str(workspace_id), str(account_id), str(user_id), body.level)
        return Response(status_code=204)

    @app.delete("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/grants/{user_id}", status_code=204)
    def revoke_grant(workspace_id: uuid.UUID, account_id: uuid.UUID, user_id: uuid.UUID, actor=Depends(current_actor)):
        core.workspaces.set_grant(actor, str(workspace_id), str(account_id), str(user_id), None)
        return Response(status_code=204)

    @app.get("/api/v1/workspaces/{workspace_id}/audit", response_model=list[dto.AuditView])
    def workspace_audit(workspace_id: uuid.UUID, limit: int = Query(100, ge=1, le=200),
                        offset: int = Query(0, ge=0), actor=Depends(current_actor)):
        return core.workspaces.audits(actor, str(workspace_id), limit=limit, offset=offset)

    @app.get("/api/v1/instance/users", response_model=list[dto.UserView])
    def users(actor=Depends(current_actor)):
        return core.identity.users(actor)

    @app.put("/api/v1/instance/users/{user_id}", status_code=204)
    def user_state(user_id: uuid.UUID, body: UserState, actor=Depends(current_actor)):
        core.identity.set_user_active(actor, str(user_id), body.active)
        return Response(status_code=204)

    @app.get("/api/v1/instance/audit", response_model=list[dto.AuditView])
    def instance_audit(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), actor=Depends(current_actor)):
        return core.workspaces.audits(actor, None, limit=limit, offset=offset)

    @app.get("/api/v1/health", response_model=dto.Health)
    def health():
        with core.db.transaction() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "api_version": 1}

    @app.post("/api/v1/workspaces/{workspace_id}/accounts/{account_id}/manual-switch", response_model=dto.SwitchIssued)
    async def manual_ticket(workspace_id: uuid.UUID, account_id: uuid.UUID, actor=Depends(current_actor)):
        return await core.switches.issue(actor, str(workspace_id), str(account_id))

    @app.post("/api/v1/manual-switch/consume", response_model=dto.ManualScript)
    def manual_consume(body: ManualConsume, actor=Depends(current_actor)):
        return core.switches.consume(actor, body.token.get_secret_value(),
            render=lambda delivery: render_script(delivery, body.platform, preview=manual_switch_preview))

    if _local is not None:
        allowed = {"bootstrap", "me", "accounts", "account", "authorize_account", "reauthorize_account",
                   "edit_account", "delete_account", "refresh", "detail", "workspace_audit", "health"}
        app.router.routes[:] = [route for route in app.router.routes if route.name in allowed]
        return app

    # Only explicit UI routes are mounted. Unknown API routes never become HTML.
    root = Path(web_dir) if web_dir else Path(__file__).parents[1] / "web_v2"
    if (root / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def frontend():
            return FileResponse(root / "index.html", media_type="text/html")

    return app
