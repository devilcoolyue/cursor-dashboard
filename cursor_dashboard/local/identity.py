from __future__ import annotations

import threading
import time

from sqlalchemy import select, update

from ..application.identity import personal_workspace
from ..domain.core import Conflict
from ..infrastructure.persistence.models import Metadata, User, UserSession


class LocalIdentity:
    """Private desktop session, never a public passwordless login endpoint."""
    def __init__(self, core):
        if core.config.mode != "local":
            raise Conflict("Local identity requires local mode")
        self.core, self._lock = core, threading.RLock()
        with core.db.transaction(write=True) as session:
            marker = session.get(Metadata, "desktop_user")
            if marker is None:
                if session.scalar(select(User.id).limit(1)):
                    raise Conflict("This data directory is not an independent desktop instance")
                user = User(login="local@desktop.invalid")
                session.add(user)
                session.flush()
                workspace = personal_workspace(session, user)
                workspace.name = "我的账号"
                session.add(Metadata(name="desktop_user", value=user.id))
                self.user_id = user.id
            else:
                self.user_id = marker.value
                user = session.get(User, self.user_id)
                if user is None or not user.active or user.password_hash or user.instance_admin:
                    raise Conflict("Local identity is unavailable")
            session.execute(update(UserSession).where(UserSession.user_id == self.user_id).values(revoked=True))
            self.login = core.identity._login(session, user)

    def actor(self, request_id=None):
        with self._lock:
            if self.login.expires_at <= time.time() + 600:
                with self.core.db.transaction(write=True) as session:
                    session.execute(update(UserSession).where(UserSession.user_id == self.user_id).values(revoked=True))
                    self.login = self.core.identity._login(session, session.get(User, self.user_id))
            return self.core.identity.authenticate(self.login.token, request_id=request_id)

    def close(self):
        with self.core.db.transaction(write=True) as session:
            session.execute(update(UserSession).where(UserSession.user_id == self.user_id).values(revoked=True))
