"""Bounded, incremental retention for V2 audit and expired authentication records."""
from __future__ import annotations

import asyncio
import logging
import re
import stat
import time

from sqlalchemy import delete, func, or_, select

from ..domain.core import CoreError
from ..infrastructure.persistence.models import (
    AuditEvent,
    DeviceAuthorization,
    Invitation,
    SwitchTicket,
    UserSession,
)


class Retention:
    batch_size = 5000

    def __init__(self, db, config):
        self.db, self.config = db, config
        self.next_run = 0

    def prune(self):
        now = time.time()
        with self.db.transaction(write=True) as session:
            total = session.scalar(select(func.count()).select_from(AuditEvent))
            overflow = max(0, total - self.config.audit_max_events)
            oldest = select(AuditEvent.id).order_by(AuditEvent.created_at, AuditEvent.id)
            expired = AuditEvent.created_at < now - self.config.audit_retention_days * 86400
            condition = or_(expired, AuditEvent.id.in_(oldest.limit(min(overflow, self.batch_size)))) if overflow else expired
            victims = oldest.where(condition).limit(self.batch_size)
            removed = {"audit_events": session.execute(delete(AuditEvent).where(
                AuditEvent.id.in_(victims)).execution_options(synchronize_session=False)).rowcount}
            for model, key in ((SwitchTicket, SwitchTicket.id), (DeviceAuthorization, DeviceAuthorization.token_hash),
                               (Invitation, Invitation.id), (UserSession, UserSession.id)):
                expired = select(key).where(model.expires_at <= now).limit(self.batch_size)
                removed[model.__tablename__] = session.execute(delete(model).where(
                    key.in_(expired)).execution_options(synchronize_session=False)).rowcount
        # Revisit an existing backlog quickly without holding a long write transaction.
        return removed

    async def tick(self):
        if time.monotonic() < self.next_run:
            return
        self.next_run = time.monotonic() + 60
        job = asyncio.create_task(asyncio.to_thread(self.prune))
        try:
            removed = await asyncio.shield(job)
            if any(count >= self.batch_size for count in removed.values()):
                self.next_run = time.monotonic() + 1
        except asyncio.CancelledError:
            # Database and runtime lock must outlive the in-flight write.
            try:
                await job
            except (CoreError, OSError):
                pass
            raise
        except (CoreError, OSError):
            logging.getLogger(__name__).warning("V2 retention failed; retrying in 60 seconds")

    async def serve(self):
        while True:
            await self.tick()
            await asyncio.sleep(1)


def prune_backup_files(directory, pattern, *, keep, max_bytes, protected=()):
    """Only remove recognized regular backups; always preserve explicit recovery points."""
    if directory.is_symlink():
        return
    entries = []
    for path in directory.iterdir():
        if not re.fullmatch(pattern, path.name):
            continue
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            entries.append((info.st_mtime_ns, path.name, path, info.st_size))
    entries.sort(reverse=True)
    protected = set(protected)
    if entries:
        protected.add(entries[0][2])  # Never remove the most recent recovery point.
    count, size = len(entries), sum(entry[3] for entry in entries)
    for _, _, path, length in reversed(entries):
        if count <= keep and size <= max_bytes:
            break
        if path in protected:
            continue
        try:
            path.unlink(missing_ok=True)
            count -= 1
            size -= length
        except OSError:
            logging.getLogger(__name__).warning("Could not remove an expired V2 backup")
