from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import secrets
import time

from sqlalchemy import delete, func, select, update

from ..domain.core import Actor, Conflict, Forbidden, NotFound, Unauthenticated
from ..infrastructure.persistence.models import (AuditEvent, Grant, Invitation, LegacyImport, LegacyMapping, Membership,
    SwitchTicket, User, UserSession, Workspace)
from ..infrastructure.persistence.policy import (active_user, audit, authorize, membership,
                                                  workspace_capabilities)
from ..infrastructure.persistence.repository import normalize_email
from .security import DUMMY_PASSWORD, Limiter, digest, password_hash, verify_password


def login_name(value):
    value = normalize_email(value)
    if not value or len(value) > 320 or "@" not in value or any(c.isspace() for c in value):
        raise Conflict("A valid login email is required")
    return value


def workspace_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise Conflict("Workspace name must contain 1 to 128 characters")
    return value.strip()


def personal_workspace(session, user):
    existing = session.scalar(select(Workspace).join(Membership).where(
        Membership.user_id == user.id, Workspace.kind == "personal"))
    if existing:
        return existing
    workspace = Workspace(kind="personal", name="Personal")
    session.add(workspace)
    session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    return workspace


def revoke_tickets(session, *, user_id=None, workspace_id=None, account_id=None):
    query = delete(SwitchTicket).where(SwitchTicket.consumed_at.is_(None))
    for column, value in ((SwitchTicket.user_id, user_id), (SwitchTicket.workspace_id, workspace_id),
                           (SwitchTicket.account_id, account_id)):
        if value is not None:
            query = query.where(column == value)
    session.execute(query)


@dataclass(frozen=True)
class LoginResult:
    actor: Actor
    token: str = field(repr=False)
    csrf: str = field(repr=False)
    expires_at: float


class IdentityService:
    session_ttl = 12 * 3600

    def __init__(self, repository, limiter=None):
        self.repo, self.db = repository, repository.db
        self.limiter = limiter or Limiter()

    def initialized(self):
        with self.db.transaction() as session:
            return bool(session.scalar(select(User.id).where(User.instance_admin.is_(True),
                User.active.is_(True), User.password_hash.is_not(None)).limit(1)))

    def initialize_server(self, login, password):
        """Offline operator only: intentionally has no public HTTP equivalent."""
        login, encoded = login_name(login), password_hash(password)
        with self.db.transaction(write=True) as session:
            if session.scalar(select(User.id).where(User.instance_admin.is_(True)).limit(1)):
                raise Conflict("Server is already initialized")
            user = session.scalar(select(User).where(User.login == login))
            if user is None:
                user = User(login=login)
                session.add(user)
                session.flush()
            if user.password_hash:
                raise Conflict("Existing identity already has a password")
            user.password_hash, user.instance_admin, user.active = encoded, True, True
            workspace = personal_workspace(session, user)
            audit(session, Actor(user.id), "instance.initialize", resource_id=user.id)
            return {"user_id": user.id, "workspace_id": workspace.id}

    def login(self, login, password, *, source="local", request_id=None):
        normalized = normalize_email(login) or ""
        self.limiter.check((("login-source", source), 20, 300),
                           (("login-name", digest(normalized)), 8, 300), (("login-global",), 100, 300))
        with self.db.transaction() as session:
            user = session.scalar(select(User).where(User.login == normalized))
            encoded = user.password_hash if user and user.active else None
            user_id = user.id if user else None
        valid = verify_password(password, encoded or DUMMY_PASSWORD)
        if not valid or encoded is None:
            with self.db.transaction(write=True) as session:
                audit(session, Actor(user_id, request_id=request_id) if user_id else None,
                      "session.login", result="denied")
            raise Unauthenticated("Invalid login or password")
        with self.db.transaction(write=True) as session:
            user = session.get(User, user_id)
            if not user.active or user.password_hash != encoded:
                raise Unauthenticated("Invalid login or password")
            return self._login(session, user, request_id)

    def _login(self, session, user, request_id=None):
        token = secrets.token_urlsafe(32)
        csrf = digest("csrf:" + token)
        now = time.time()
        session.execute(delete(UserSession).where(UserSession.expires_at <= now))
        # Bound active sessions without returning any previous raw ticket.
        current = list(session.scalars(select(UserSession).where(UserSession.user_id == user.id,
            UserSession.kind == "web",
            UserSession.revoked.is_(False)).order_by(UserSession.created_at.desc())))
        for old in current[19:]:
            old.revoked = True
        record = UserSession(user_id=user.id, token_hash=digest(token), csrf_hash=digest(csrf),
                             created_at=now, expires_at=now + self.session_ttl)
        session.add(record)
        session.flush()
        actor = Actor(user.id, record.id, request_id)
        audit(session, actor, "session.login", resource_id=record.id)
        return LoginResult(actor, token, csrf, record.expires_at)

    def authenticate(self, token, *, csrf=None, request_id=None, kind="web"):
        with self.db.transaction() as session:
            record = session.scalar(select(UserSession).where(UserSession.token_hash == digest(token)))
            if record is None or record.kind != kind or record.revoked or record.expires_at <= time.time():
                raise Unauthenticated("Session expired or revoked")
            actor = Actor(record.user_id, record.id, request_id)
            try:
                active_user(session, actor)
            except NotFound:
                raise Unauthenticated("Session expired or revoked") from None
            if csrf is not None and not hmac.compare_digest(record.csrf_hash, digest(csrf)):
                raise Forbidden("CSRF token is invalid")
            return actor

    def me(self, actor):
        with self.db.transaction() as session:
            user = active_user(session, actor)
            spaces = []
            for member, workspace in session.execute(select(Membership, Workspace).join(Workspace).where(
                    Membership.user_id == user.id).order_by(Workspace.id)):
                spaces.append({"id": workspace.id, "name": workspace.name, "kind": workspace.kind,
                    "role": member.role, "capabilities": workspace_capabilities(member, workspace.kind)})
            return {"id": user.id, "login": user.login, "instance_admin": user.instance_admin,
                    "workspaces": spaces}

    def sessions(self, actor):
        with self.db.transaction() as session:
            active_user(session, actor)
            return [{"id": r.id, "created_at": r.created_at, "expires_at": r.expires_at,
                     "kind": r.kind, "device_id": r.device_id, "device_name": r.device_name,
                     "current": r.id == actor.session_id} for r in session.scalars(select(UserSession).where(
                         UserSession.user_id == actor.user_id, UserSession.revoked.is_(False),
                         UserSession.expires_at > time.time()).order_by(UserSession.created_at.desc()))]

    def revoke_session(self, actor, session_id):
        with self.db.transaction(write=True) as session:
            active_user(session, actor)
            record = session.get(UserSession, session_id)
            if record is None or record.user_id != actor.user_id:
                raise NotFound("Session is unavailable")
            record.revoked = True
            audit(session, actor, "session.revoke", resource_id=record.id)

    def change_password(self, actor, old_password, new_password):
        self.limiter.check((("password", actor.user_id), 5, 300))
        with self.db.transaction() as session:
            encoded = active_user(session, actor).password_hash
        if not verify_password(old_password, encoded):
            raise Unauthenticated("Current password is incorrect")
        replacement = password_hash(new_password)
        with self.db.transaction(write=True) as session:
            user = active_user(session, actor)
            if user.password_hash != encoded:
                raise Conflict("Password changed; sign in again")
            user.password_hash = replacement
            session.execute(update(UserSession).where(UserSession.user_id == user.id).values(revoked=True))
            revoke_tickets(session, user_id=user.id)
            audit(session, actor, "user.password", resource_id=user.id)

    def users(self, actor):
        with self.db.transaction() as session:
            authorize(session, actor, "instance")
            return [{"id": u.id, "login": u.login, "active": u.active, "instance_admin": u.instance_admin}
                    for u in session.scalars(select(User).order_by(User.id))]

    def set_user_active(self, actor, user_id, active):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "instance")
            user = session.get(User, user_id)
            if user is None:
                raise NotFound("User is unavailable")
            if not active and user.instance_admin and session.scalar(select(func.count()).select_from(User).where(
                    User.active.is_(True), User.instance_admin.is_(True))) <= 1:
                raise Conflict("Cannot disable the last active instance administrator")
            user.active = active
            if not active:
                session.execute(update(UserSession).where(UserSession.user_id == user.id).values(revoked=True))
                session.execute(update(Invitation).where(Invitation.issuer_id == user.id).values(revoked=True))
                revoke_tickets(session, user_id=user.id)
            audit(session, actor, "user.enable" if active else "user.disable", resource_id=user.id)

    def recover_password(self, login, password):
        """Offline operator recovery; never exposed through the HTTP router."""
        login, encoded = login_name(login), password_hash(password)
        with self.db.transaction(write=True) as session:
            user = session.scalar(select(User).where(User.login == login))
            if user is None:
                raise NotFound("User is unavailable")
            user.password_hash = encoded
            session.execute(update(UserSession).where(UserSession.user_id == user.id).values(revoked=True))
            revoke_tickets(session, user_id=user.id)
            audit(session, None, "operator.password_recovery", resource_id=user.id)


class WorkspaceService:
    def __init__(self, repository, identity):
        self.repo, self.db, self.identity = repository, repository.db, identity

    def create(self, actor, name):
        name = workspace_name(name)
        with self.db.transaction(write=True) as session:
            active_user(session, actor)
            workspace = Workspace(kind="team", name=name)
            session.add(workspace)
            session.flush()
            session.add(Membership(workspace_id=workspace.id, user_id=actor.user_id, role="owner"))
            audit(session, actor, "workspace.create", workspace.id, workspace.id)
            return {"id": workspace.id, "name": workspace.name, "kind": "team", "role": "owner"}

    def members(self, actor, workspace_id):
        with self.db.transaction() as session:
            authorize(session, actor, "members", workspace_id)
            return [{"id": u.id, "login": u.login, "role": m.role, "active": u.active}
                    for m, u in session.execute(select(Membership, User).join(User).where(
                        Membership.workspace_id == workspace_id).order_by(User.id))]

    @staticmethod
    def _can_assign(session, actor, workspace_id, role, target=None):
        authorize(session, actor, "members", workspace_id)
        member = membership(session, actor, workspace_id)
        if role not in {"admin", "member", "viewer"}:
            raise Conflict("Role must be admin, member or viewer")
        if target and target.role == "owner":
            raise Conflict("Use ownership transfer to change the owner")
        if member.role != "owner" and (role == "admin" or (target and target.role == "admin")):
            raise Forbidden("Only the owner may appoint or remove administrators")

    def invite(self, actor, workspace_id, login, role="member"):
        login = login_name(login)
        token = secrets.token_urlsafe(32)
        with self.db.transaction(write=True) as session:
            self._can_assign(session, actor, workspace_id, role)
            user = session.scalar(select(User).where(User.login == login))
            if user and session.get(Membership, (workspace_id, user.id)):
                raise Conflict("User is already a workspace member")
            session.execute(update(Invitation).where(Invitation.workspace_id == workspace_id,
                Invitation.login == login, Invitation.used_at.is_(None)).values(revoked=True))
            invitation = Invitation(workspace_id=workspace_id, login=login, role=role,
                issuer_id=actor.user_id, token_hash=digest(token), expires_at=time.time() + 7 * 86400)
            session.add(invitation)
            session.flush()
            audit(session, actor, "invitation.create", workspace_id, invitation.id, changes={"role": role})
            return {"id": invitation.id, "token": token, "expires_at": invitation.expires_at}

    def invitations(self, actor, workspace_id):
        with self.db.transaction() as session:
            authorize(session, actor, "members", workspace_id)
            return [{"id": i.id, "login": i.login, "role": i.role, "expires_at": i.expires_at}
                    for i in session.scalars(select(Invitation).where(Invitation.workspace_id == workspace_id,
                        Invitation.used_at.is_(None), Invitation.revoked.is_(False),
                        Invitation.expires_at > time.time()).order_by(Invitation.id))]

    def revoke_invitation(self, actor, workspace_id, invitation_id):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "members", workspace_id)
            invitation = session.get(Invitation, invitation_id)
            if invitation is None or invitation.workspace_id != workspace_id:
                raise NotFound("Invitation is unavailable")
            self._can_assign(session, actor, workspace_id, invitation.role)
            invitation.revoked = True
            audit(session, actor, "invitation.revoke", workspace_id, invitation.id)

    def accept(self, token, *, actor=None, password=None, request_id=None, source="local"):
        self.identity.limiter.check((("invitation-source", source), 20, 300))
        # Only a new identity may be established with this password. Existing
        # identities must authenticate; an invite cannot reset their password.
        encoded = password_hash(password) if password is not None and actor is None else None
        with self.db.transaction(write=True) as session:
            invitation = session.scalar(select(Invitation).where(Invitation.token_hash == digest(token)))
            if not invitation or invitation.revoked or invitation.used_at or invitation.expires_at <= time.time():
                raise NotFound("Invitation is unavailable")
            try:
                self._can_assign(session, Actor(invitation.issuer_id), invitation.workspace_id, invitation.role)
            except (NotFound, Forbidden):
                raise NotFound("Invitation is unavailable") from None
            user = session.scalar(select(User).where(User.login == invitation.login))
            if user:
                if actor is None or actor.user_id != user.id:
                    raise Unauthenticated("Sign in as the invited user to join")
                active_user(session, actor)
            else:
                if actor is not None:
                    raise Forbidden("Invitation belongs to another login")
                if encoded is None:
                    raise Conflict("A password is required for a new user")
                user = User(login=invitation.login, password_hash=encoded)
                session.add(user)
                session.flush()
            if session.get(Membership, (invitation.workspace_id, user.id)):
                raise Conflict("User is already a workspace member")
            personal_workspace(session, user)
            session.add(Membership(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role))
            invitation.used_at = time.time()
            joining_actor = actor or Actor(user.id, request_id=request_id)
            audit(session, joining_actor, "invitation.accept", invitation.workspace_id, invitation.id)
            return {"user_id": user.id, "workspace_id": invitation.workspace_id}

    def _invalidate_member(self, session, workspace_id, user_id):
        revoke_tickets(session, workspace_id=workspace_id, user_id=user_id)
        session.execute(update(Invitation).where(Invitation.workspace_id == workspace_id,
            Invitation.issuer_id == user_id, Invitation.used_at.is_(None)).values(revoked=True))

    def set_role(self, actor, workspace_id, user_id, role):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "members", workspace_id)
            target = session.get(Membership, (workspace_id, user_id))
            if target is None:
                raise NotFound("Member is unavailable")
            self._can_assign(session, actor, workspace_id, role, target)
            previous_role = target.role
            target.role = role
            if role == "viewer":
                session.execute(update(Grant).where(Grant.workspace_id == workspace_id,
                    Grant.user_id == user_id, Grant.level == "use").values(level="view"))
            self._invalidate_member(session, workspace_id, user_id)
            audit(session, actor, "membership.role", workspace_id, user_id,
                  changes={"previous_role": previous_role, "role": role})

    def remove_member(self, actor, workspace_id, user_id):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "view", workspace_id)
            workspace = session.get(Workspace, workspace_id)
            target = session.get(Membership, (workspace_id, user_id))
            if target is None:
                raise NotFound("Member is unavailable")
            if workspace.kind != "team" or target.role == "owner":
                raise Conflict("Owner must transfer ownership before leaving")
            if actor.user_id != user_id:
                self._can_assign(session, actor, workspace_id, target.role, target)
            self._invalidate_member(session, workspace_id, user_id)
            session.delete(target)
            audit(session, actor, "membership.remove", workspace_id, user_id)

    def transfer(self, actor, workspace_id, user_id):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "owner", workspace_id)
            target = session.get(Membership, (workspace_id, user_id))
            user = session.get(User, user_id)
            if target is None or user is None or not user.active or user_id == actor.user_id:
                raise Conflict("Choose another active workspace member")
            owner = membership(session, actor, workspace_id)
            owner.role = "admin"
            session.flush()  # release the unique owner slot within this transaction
            target.role = "owner"
            self._invalidate_member(session, workspace_id, actor.user_id)
            self._invalidate_member(session, workspace_id, user_id)
            audit(session, actor, "workspace.transfer", workspace_id, user_id)

    def delete(self, actor, workspace_id):
        with self.db.transaction(write=True) as session:
            workspace = authorize(session, actor, "owner", workspace_id)
            audit(session, actor, "workspace.delete", workspace_id, workspace_id)
            # Whole-space deletion also removes its import receipts; account
            # deletion alone still preserves P1 idempotent import semantics.
            sources = select(LegacyImport.source_hash).where(LegacyImport.workspace_id == workspace_id)
            session.execute(delete(LegacyMapping).where(LegacyMapping.source_hash.in_(sources)))
            session.execute(delete(LegacyImport).where(LegacyImport.workspace_id == workspace_id))
            session.delete(workspace)

    def grants(self, actor, workspace_id, account_id):
        with self.db.transaction() as session:
            authorize(session, actor, "grant", workspace_id, account_id)
            return [{"user_id": g.user_id, "level": g.level} for g in session.scalars(select(Grant).where(
                Grant.workspace_id == workspace_id, Grant.account_id == account_id).order_by(Grant.user_id))]

    def set_grant(self, actor, workspace_id, account_id, user_id, level):
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "grant", workspace_id, account_id)
            target = session.get(Membership, (workspace_id, user_id))
            if target is None:
                raise NotFound("Member is unavailable")
            if target.role not in {"member", "viewer"} or level not in {None, "view", "use"}:
                raise Conflict("Grants apply to members and viewers with view or use access")
            if target.role == "viewer" and level == "use":
                raise Conflict("Viewers cannot receive use access")
            grant = session.get(Grant, (workspace_id, account_id, user_id))
            previous_level = grant.level if grant else None
            if grant:
                if level is None:
                    session.delete(grant)
                else:
                    grant.level = level
            elif level:
                session.add(Grant(workspace_id=workspace_id, account_id=account_id, user_id=user_id, level=level))
            revoke_tickets(session, user_id=user_id, workspace_id=workspace_id, account_id=account_id)
            audit(session, actor, "grant.revoke" if level is None else "grant.set", workspace_id, account_id,
                  changes={"user_id": user_id, "previous_level": previous_level, "level": level})

    def audits(self, actor, workspace_id, *, limit=100, offset=0):
        if not 1 <= limit <= 200 or offset < 0:
            raise Conflict("Invalid audit pagination")
        with self.db.transaction() as session:
            if workspace_id is None:
                authorize(session, actor, "instance")
            else:
                authorize(session, actor, "audit", workspace_id)
            # Instance admins see instance events only; workspace events require membership.
            rows = session.scalars(select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit).offset(offset))
            return [{"id": e.id, "actor_id": e.actor_id, "workspace_id": e.workspace_id,
                     "resource_id": e.resource_id, "action": e.action, "result": e.result,
                     "created_at": e.created_at, "request_id": e.request_id, "changes": e.changes} for e in rows]
