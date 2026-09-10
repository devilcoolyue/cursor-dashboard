from __future__ import annotations

import asyncio
import json
from pathlib import Path
import random
import threading
import time

from ..api.app import create_app
from ..domain.core import Conflict, CoreError, Locked, SecretError
from ..runtime.core import Core
from ..runtime.lock import RuntimeLock
from ..runtime.settings import CoreConfig
from .archive import read_archive
from .cursor import CursorInstallation
from .files import private_directory, write_new
from .identity import LocalIdentity
from .keys import DesktopKeys, SystemKeyStore
from .switching import SwitchExecutor


class DesktopRuntime:
    def __init__(self, data_dir, *, store=None, gateway=None, installation=None):
        self.directory = private_directory(Path(data_dir))
        self.lock = RuntimeLock(self.directory / ".desktop.lock").acquire()
        self.store = store or SystemKeyStore(self.directory)
        self.config = CoreConfig(self.directory, self.directory / ".key-is-in-system-store")
        self.gateway = gateway
        self.core = self.keys = self.identity = self.business = None
        self.error = None
        self.phase = "starting"
        self.open_lock = threading.Lock()
        self.executor = SwitchExecutor(self.directory, installation or CursorInstallation())
        self.job = None
        self.background = False
        self.last_refresh = None
        self.refresh_error = None
        self.next_refresh = time.time() + random.uniform(30, 90)
        self.rotation = 0
        self.preferences = self.directory / "preferences.json"
        try:
            self.background = json.loads(self.preferences.read_text(encoding="utf-8")).get("background") is True
        except (OSError, ValueError, AttributeError):
            pass
        self.open()

    def open(self, recovery=None):
        with self.open_lock:
            if self.core is not None:
                return self.status()
            candidate = None
            try:
                keys = recovery or self.store.read()
                if keys is None:
                    if self.config.database.exists():
                        raise SecretError("Original system key is missing; restore it from an encrypted archive")
                    keys = DesktopKeys.generate()
                    self.store.save(keys)
                candidate = Core(self.config, keys=keys, gateway=self.gateway,
                                 initialize=not self.config.database.exists())
                identity = LocalIdentity(candidate)
                if recovery is not None:
                    self.store.save(keys)
                self.core, self.keys, self.identity = candidate, keys, identity
                self.business = create_app(candidate, public_origin="http://127.0.0.1", _local=self)
                self.phase, self.error = "ready", None
            except CoreError as error:
                if candidate:
                    candidate.close()
                self.core = self.identity = self.keys = self.business = None
                self.phase = "locked" if isinstance(error, SecretError) else "in_use" if isinstance(error, Locked) else "unavailable"
                self.error = str(error)
            return self.status()

    def recover(self, path, password):
        if self.core is not None or not self.config.database.is_file():
            raise Conflict("Key recovery requires an existing locked database")
        document = read_archive(path, password)
        return self.open(DesktopKeys(json.dumps(document.recovery_keys)))

    def require(self):
        if self.core is None:
            raise SecretError("Local account store is locked or unavailable")
        return self.core

    def status(self):
        return {"phase": self.phase, "error": self.error, "background": self.background,
                "api_version": 1, "last_refresh": self.last_refresh,
                "refresh_error": self.refresh_error, "switch": self.executor.status()}

    def set_background(self, enabled):
        pending = self.preferences.with_suffix(".tmp")
        pending.unlink(missing_ok=True)
        write_new(pending, json.dumps({"background": enabled}).encode("utf-8"))
        pending.replace(self.preferences)
        self.background = enabled
        self.resume()
        return self.status()

    def resume(self):
        # Re-evaluate credential expiry through ensure() at execution time, one account at a time.
        self.next_refresh = time.time() + random.uniform(30, 90)

    async def scheduler(self):
        previous = time.time()
        while True:
            await asyncio.sleep(10)
            now = time.time()
            if now - previous > 30 or now < previous:
                self.resume()
            previous = now
            if not self.background or self.core is None or now < self.next_refresh or self.job:
                continue
            self.next_refresh = now + 30
            actor = self.identity.actor()
            space = self.core.identity.me(actor)["workspaces"][0]["id"]
            accounts = self.core.accounts.search(actor, space, limit=200, offset=self.rotation)["items"]
            if not accounts:
                self.rotation = 0
                self.next_refresh = now + 900
                continue
            self.rotation += 1
            try:
                row = await self.core.accounts.refresh(actor, space, accounts[0]["id"])
                self.last_refresh = time.time()
                self.refresh_error = "Refresh failed; the last successful snapshot is retained" if row["error_kind"] else None
            except Exception:
                self.refresh_error = "Refresh failed; the last successful snapshot is retained"

    def start_switch(self, workspace_id, account_id):
        core = self.require()
        if self.job is not None:
            raise Conflict("A Cursor operation is already running")
        actor = self.identity.actor()
        core.repository.check_access(actor, workspace_id, account_id, "use")
        self.executor.update(stage="authorizing", busy=True, error=None, written=False)

        async def run():
            ticket_id = None
            result = "failure"
            try:
                issued = await core.switches.issue(actor, workspace_id, account_id)
                delivery = core.switches.consume(actor, issued["token"])
                ticket_id = delivery.ticket_id
                await asyncio.to_thread(self.executor.execute, delivery)
                result = "success"
            except Exception:
                if self.executor.status()["busy"]:
                    self.executor.update(stage="failed", busy=False,
                        error="Authorization failed; refresh or reauthorize the account before retrying")
            finally:
                try:
                    if ticket_id:
                        core.switches.record_result(actor, ticket_id, result)
                finally:
                    self.job = None
        self.job = asyncio.create_task(run())
        return self.executor.status()

    def start_restore(self, backup_id):
        self.require()
        if self.job is not None:
            raise Conflict("A Cursor operation is already running")
        self.executor.backup_path(backup_id)
        self.executor.update(stage="checking", busy=True, error=None, written=False)

        async def run():
            try:
                await asyncio.to_thread(self.executor.execute, restore_id=backup_id)
            except CoreError:
                pass
            finally:
                self.job = None
        self.job = asyncio.create_task(run())
        return self.executor.status()

    async def shutdown(self):
        # A started SQLite operation is allowed to finish before disposing its database.
        if self.job is not None:
            await self.job
        if self.core:
            try:
                self.identity.close()
            finally:
                self.core.close()
        self.lock.close()
