"""One authorization entry point, shared by HTTP and trusted local use cases."""
from __future__ import annotations

import time

from ...domain.core import Forbidden, NotFound, Unauthenticated
from .models import Account, AuditEvent, Grant, Membership, User, UserSession, Workspace


def active_user(session, actor):
    user = session.get(User, actor.user_id)
    if user is None or not user.active:
        raise NotFound("User or resource is unavailable")
    if actor.session_id is not None:
        login = session.get(UserSession, actor.session_id)
        if login is None or login.user_id != user.id or login.revoked or login.expires_at <= time.time():
            raise Unauthenticated("Session expired or revoked")
    return user


def membership(session, actor, workspace_id):
    active_user(session, actor)
    member = session.get(Membership, (workspace_id, actor.user_id))
    if member is None:
        raise NotFound("Workspace or account is unavailable")
    return member


def capabilities(session, actor, member, account_id):
    manager = member.role in {"owner", "admin"}
    grant = session.get(Grant, (member.workspace_id, account_id, actor.user_id)) if not manager else None
    view = manager or grant is not None
    use = manager or bool(member.role == "member" and grant and grant.level == "use")
    return {"view": view, "detail": view, "refresh": use, "switch": use,
            "edit": manager, "delete": manager, "authorize": manager, "grant": manager}


def workspace_capabilities(member, kind):
    manager = member.role in {"owner", "admin"}
    owner = member.role == "owner"
    return {"manage_accounts": manager, "manage_members": manager and kind == "team",
            "manage_admins": owner and kind == "team", "transfer_owner": owner and kind == "team",
            "delete": owner and kind == "team", "audit": manager, "export_credentials": owner}


def authorize(session, actor, action, workspace_id=None, account_id=None):
    if action == "instance":
        user = active_user(session, actor)
        if not user.instance_admin:
            raise Forbidden("Instance administration is required")
        return user
    member = membership(session, actor, workspace_id)
    if account_id is not None:
        account = session.get(Account, account_id)
        if account is None or account.workspace_id != workspace_id:
            raise NotFound("Workspace or account is unavailable")
        allowed = capabilities(session, actor, member, account_id)
        if not allowed["view"]:
            raise NotFound("Workspace or account is unavailable")
        mapped = {"view": "view", "use": "switch", "manage": "edit", "grant": "grant"}.get(action)
        if mapped is None or not allowed[mapped]:
            raise Forbidden("This action is not allowed")
        return account
    workspace = session.get(Workspace, workspace_id)
    allowed = workspace_capabilities(member, workspace.kind)
    key = {"manage": "manage_accounts", "members": "manage_members", "owner": "transfer_owner",
           "audit": "audit", "export": "export_credentials"}.get(action)
    if action != "view" and (key is None or not allowed[key]):
        raise Forbidden("This workspace action is not allowed")
    return workspace


def audit(session, actor, action, workspace_id=None, resource_id=None, result="success", *, changes=None):
    # Only call sites construct this allowlisted metadata; no request dictionaries.
    if changes and set(changes) - {"user_id", "previous_role", "role", "previous_level", "level", "fields"}:
        raise ValueError("Unsupported audit metadata")
    session.add(AuditEvent(actor_id=actor.user_id if actor else None, workspace_id=workspace_id,
        resource_id=resource_id, action=action, result=result, request_id=actor.request_id if actor else None,
        changes=changes))
