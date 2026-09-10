"""Trusted credential delivery boundary for the P3/P5 transport adapters.

Web scripts and native device delivery use distinct authenticated transports.
Both bind tickets to the exact live session and current account versions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import time

from sqlalchemy import delete, select

from ..domain.core import Conflict, NotFound, Secrets, Unauthenticated
from ..infrastructure.persistence.models import Credential, SwitchTicket, UserSession
from ..infrastructure.persistence.policy import audit, authorize
from .security import Limiter, digest


@dataclass(frozen=True)
class SwitchDelivery:
    ticket_id: str
    account_id: str
    workspace_id: str
    expires_at: float
    secrets: Secrets = field(repr=False)
    email: str = ""
    subject: str = ""


class SwitchService:
    def __init__(self, repository, credentials):
        self.repo, self.db, self.credentials = repository, repository.db, credentials
        self.limiter = Limiter()

    async def issue(self, actor, workspace_id, account_id):
        if actor.session_id is None:
            raise Unauthenticated("Switch tickets require an authenticated session")
        self.repo.check_access(actor, workspace_id, account_id, "use")
        self.limiter.manual(actor, workspace_id, account_id)
        await self.credentials.ensure(workspace_id, account_id)
        token = secrets.token_urlsafe(32)
        with self.db.transaction(write=True) as session:
            authorize(session, actor, "use", workspace_id, account_id)
            credential = session.get(Credential, (workspace_id, account_id))
            now = time.time()
            login = session.get(UserSession, actor.session_id)
            expires = min(now + 300, credential.expires_at, login.expires_at)
            if credential.invalid or expires <= now:
                raise Conflict("Account authorization is unavailable")
            session.execute(delete(SwitchTicket).where(SwitchTicket.expires_at <= now))
            ticket = SwitchTicket(token_hash=digest(token), workspace_id=workspace_id, account_id=account_id,
                user_id=actor.user_id, session_id=actor.session_id, generation=credential.generation,
                version=credential.version, expires_at=expires)
            session.add(ticket)
            session.flush()
            audit(session, actor, "switch.issue", workspace_id, account_id)
            return {"token": token, "expires_at": expires}

    def consume(self, actor, token, *, render=None):
        """Check, decrypt, consume and audit in one write transaction, with no await."""
        with self.db.transaction(write=True) as session:
            ticket = session.scalar(select(SwitchTicket).where(SwitchTicket.token_hash == digest(token)))
            if (ticket is None or ticket.user_id != actor.user_id or ticket.session_id != actor.session_id
                    or ticket.consumed_at is not None or ticket.expires_at <= time.time()):
                raise NotFound("Switch ticket is unavailable")
            account = authorize(session, actor, "use", ticket.workspace_id, ticket.account_id)
            credential = session.get(Credential, (ticket.workspace_id, ticket.account_id))
            if (credential.invalid or credential.expires_at <= time.time() or
                    (credential.generation, credential.version) != (ticket.generation, ticket.version)):
                raise NotFound("Switch ticket is unavailable")
            authorized = self.repo._authorized(session, account)
            if not authorized.secrets.access_token or not authorized.secrets.refresh_token:
                raise Conflict("Desktop authorization is unavailable")
            ticket.consumed_at = time.time()
            audit(session, actor, "switch.consume", ticket.workspace_id, ticket.account_id)
            delivery = SwitchDelivery(ticket.id, ticket.account_id, ticket.workspace_id,
                                      ticket.expires_at, authorized.secrets, authorized.email or "",
                                      authorized.subject or "")
            # Fixed transport rendering shares the write lock with consumption.
            # Render failures roll back; revocation cannot interleave delivery.
            return render(delivery) if render else delivery

    def record_result(self, actor, ticket_id, result):
        """External execution is a separate event and never changes consumption."""
        if result not in {"success", "failure", "cancelled"}:
            raise Conflict("Invalid switch result")
        with self.db.transaction(write=True) as session:
            ticket = session.get(SwitchTicket, ticket_id)
            if (ticket is None or ticket.user_id != actor.user_id or ticket.session_id != actor.session_id
                    or ticket.consumed_at is None):
                raise NotFound("Switch operation is unavailable")
            authorize(session, actor, "view", ticket.workspace_id, ticket.account_id)
            audit(session, actor, "switch.result", ticket.workspace_id, ticket.account_id, result)
