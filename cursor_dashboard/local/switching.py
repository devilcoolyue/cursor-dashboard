from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from ..desktop import desktop_session, DesktopSessionError
from ..domain.core import Conflict
from ..runtime.lock import RuntimeLock
from ..runtime.retention import prune_backup_files
from .files import private_directory, write_new

AUTH_VALUES = ("accessToken", "refreshToken", "cachedEmail", "cachedSignUpType", "stripeMembershipAuthId")
STALE_VALUES = ("stripeMembershipType", "stripeSubscriptionStatus", "stripeCustomerId", "cachedScopedProfile", "cachedTeam", "teamId")


def validate_delivery(delivery):
    try:
        verified = desktop_session({"accessToken": delivery.secrets.access_token,
                                    "refreshToken": delivery.secrets.refresh_token}, delivery.subject)
        if delivery.expires_at <= time.time() or not delivery.subject:
            raise ValueError()
        return verified
    except (ValueError, TypeError, KeyError, DesktopSessionError):
        raise Conflict("Desktop authorization is invalid or expired; reauthorize the account") from None


def open_database(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=5)


def verify_database(connection):
    if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise Conflict("Cursor database integrity check failed")
    connection.execute("SELECT key, value FROM ItemTable LIMIT 1").fetchone()


class SwitchExecutor:
    """Serial operations with durable, credential-free progress and consistent WAL backups."""
    def __init__(self, data_dir, installation):
        self.directory = private_directory(data_dir / "cursor-backups")
        self.installation = installation
        self.guard = threading.Lock()
        self.status_lock = threading.RLock()
        self.state_file = data_dir / "switch-state.json"
        self.state = {"stage": "idle", "busy": False, "backup_id": None, "error": None}
        if self.state_file.is_file():
            try:
                old = json.loads(self.state_file.read_text(encoding="utf-8"))
                if old.get("backup_id"):
                    self.backup_path(old["backup_id"])
                    self.state = old
                    if old.get("busy"):
                        self.update(stage="interrupted", busy=False,
                                    error="Operation was interrupted; inspect Cursor or restore the backup")
            except (ValueError, OSError, Conflict, TypeError):
                pass

    def status(self):
        with self.status_lock:
            return dict(self.state)

    def update(self, **values):
        with self.status_lock:
            self.state.update(values)
            pending = self.state_file.with_suffix(".tmp")
            pending.unlink(missing_ok=True)
            write_new(pending, json.dumps(self.state).encode("utf-8"))
            pending.replace(self.state_file)

    def backup_path(self, backup_id):
        try:
            if str(uuid.UUID(backup_id)) != backup_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Conflict("Backup is unavailable") from None
        path = self.directory / f"{backup_id}.sqlite"
        if path.is_symlink():
            raise Conflict("Backup is unavailable")
        return path

    def backups(self):
        return [{"id": path.stem, "created_at": path.stat().st_mtime}
                for path in sorted(self.directory.glob("*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not path.is_symlink()][:100]

    def backup(self, source, *, protected=()):
        backup_id = str(uuid.uuid4())
        path = self.backup_path(backup_id)
        write_new(path, b"")
        try:
            with closing(sqlite3.connect(path)) as target:
                source.backup(target)
                verify_database(target)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.update(backup_id=backup_id)
        prune_backup_files(self.directory, r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.sqlite",
                           keep=20, max_bytes=512 * 1024**2, protected=(*protected, path))
        return backup_id

    def execute(self, delivery=None, *, restore_id=None):
        if not self.guard.acquire(blocking=False):
            raise Conflict("A Cursor operation is already running")
        try:
            self.update(stage="checking", busy=True, error=None, written=False)
            if delivery is not None:
                validate_delivery(delivery)
            installation = self.installation
            installation.require()
            # Shared by every instance that targets this Cursor data directory.
            with RuntimeLock(installation.database.parent / ".cursor-panel-switch.lock"):
                restore_path = self.backup_path(restore_id) if restore_id else None
                if restore_path and not restore_path.is_file():
                    raise Conflict("Backup is unavailable")
                self.update(stage="quitting")
                installation.quit()
                installation.ensure_stopped()
                with closing(open_database(installation.database)) as database:
                    verify_database(database)
                    self.update(stage="backing_up")
                    self.backup(database, protected=(restore_path,) if restore_path else ())
                    installation.ensure_stopped()
                    self.update(stage="writing")
                    if restore_path:
                        with closing(open_database(restore_path)) as source:
                            verify_database(source)
                            source.backup(database)
                    else:
                        verified = validate_delivery(delivery)
                        values = (delivery.secrets.access_token, delivery.secrets.refresh_token,
                                  delivery.email, "Auth_0", verified.subject)
                        database.execute("BEGIN IMMEDIATE")
                        try:
                            installation.ensure_stopped()
                            validate_delivery(delivery)
                            database.executemany("INSERT OR REPLACE INTO ItemTable(key,value) VALUES (?,?)",
                                [("cursorAuth/" + key, value) for key, value in zip(AUTH_VALUES, values)])
                            database.executemany("DELETE FROM ItemTable WHERE key=?",
                                [("cursorAuth/" + key,) for key in STALE_VALUES])
                            database.commit()
                        except BaseException:
                            database.rollback()
                            raise
                    verify_database(database)
                self.update(stage="restarting", written=True)
                installation.restart()
                self.update(stage="complete", busy=False)
        except BaseException as error:
            safe = str(error) if isinstance(error, Conflict) else "Cursor operation failed; use the backup to recover"
            self.update(stage="failed", busy=False, error=safe)
            raise Conflict(safe) from None
        finally:
            self.guard.release()
        return self.status()
