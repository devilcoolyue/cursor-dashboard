from __future__ import annotations

import os

from ..application.accounts import AccountService
from ..application.credentials import CredentialService
from ..application.identity import IdentityService, WorkspaceService
from ..application.switching import SwitchService
from ..domain.core import Conflict
from ..infrastructure.persistence.database import Database
from ..infrastructure.persistence.repository import Repository
from ..infrastructure.providers.cursor.gateway import CursorGateway
from ..infrastructure.secrets import Cipher, FileKeyProvider
from .lock import RuntimeLock


class Core:
    """Single-instance composition root shared by maintenance and authenticated API."""
    def __init__(self, config, *, keys=None, gateway=None, initialize=False, upgrade=False):
        self.config, self.db, self.lock = config, None, None
        keys = keys or FileKeyProvider(config.key_file)
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RuntimeLock(config.lock_file).acquire()
        created = False
        try:
            if initialize:
                try:
                    fd = os.open(config.database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise Conflict("V2 database already exists; use open, upgrade or verify") from None
                os.close(fd)
                created = True
            elif not config.database.is_file():
                raise Conflict("V2 database is missing; run init explicitly")
            self.db = Database(config.database)
            self.repository = Repository(self.db, Cipher(keys))
            if initialize:
                self.db.upgrade()
                self.repository.check_key(initialize=True)
            else:
                self.repository.check_key()
                if upgrade:
                    self.db.upgrade()
                self.db.require_current()
            gateway = gateway or CursorGateway(interval=config.request_interval, concurrency=config.request_concurrency)
            self.credentials = CredentialService(self.repository, gateway, config)
            self.accounts = AccountService(self.repository, self.credentials, gateway, config)
            self.identity = IdentityService(self.repository)
            self.workspaces = WorkspaceService(self.repository, self.identity)
            self.switches = SwitchService(self.repository, self.credentials)
        except BaseException:
            if self.db:
                self.db.close()
            if created:
                for path in (config.database, config.database.with_name("core.db-wal"), config.database.with_name("core.db-shm")):
                    path.unlink(missing_ok=True)
            self.lock.close()
            raise

    def close(self):
        if self.db:
            self.db.close()
        if self.lock:
            self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
