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
from .commands import LocalCommands
from .files import private_directory, write_new
from .identity import LocalIdentity
from .keys import DesktopKeys, SystemKeyStore
from .remote import Connections
from .switching import SwitchExecutor


class DesktopRuntime:
    def __init__(self, data_dir, *, store=None, gateway=None, installation=None, connections=None, script_preview=False):
        self.directory = private_directory(Path(data_dir))
        self.lock = RuntimeLock(self.directory / ".desktop.lock").acquire()
        self.store = store or SystemKeyStore(self.directory)
        self.config = CoreConfig(self.directory, self.directory / ".key-is-in-system-store")
        self.gateway = gateway
        self.core = self.keys = self.identity = self.business = None
        self.error = None
        self.phase = "starting"
        self.open_lock = threading.Lock()
        self.cursor_paths = self.directory / "cursor-paths.json"
        paths = {}
        try:
            saved = json.loads(self.cursor_paths.read_text(encoding="utf-8"))
            paths = {key: saved[key] for key in ("executable_path", "user_data_path") if isinstance(saved.get(key), str)}
        except (OSError, ValueError, AttributeError):
            pass
        self.executor = SwitchExecutor(self.directory, installation or CursorInstallation(**paths))
        self.commands = LocalCommands(self.directory, preview=script_preview)
        self.connections = connections or Connections(self.directory)
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
                                 initialize=not self.config.database.exists(), upgrade=True)
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

    def detect_cursor(self, paths=None):
        # Detection runs in an HTTP worker. Freeze the target throughout authorization,
        # database writes and restart, including the gap before execute() takes its lock.
        if not self.executor.guard.acquire(blocking=False):
            raise Conflict("Wait for the current Cursor operation to finish")
        try:
            if self.job is not None or self.executor.status()["busy"]:
                raise Conflict("Wait for the current Cursor operation to finish")
            if paths is None:
                installation = self.executor.installation
                if hasattr(installation, "refresh"):
                    installation.refresh()
                return installation.detect()
            candidate = CursorInstallation(**paths)
            result = candidate.detect()
            # Invalid manual input cannot replace a working/saved target. Clearing both
            # fields always permits returning to automatic discovery after an uninstall.
            if not result["available"] and any(paths.values()):
                return {**result, "saved": False}
            normalized = {"executable_path": str(candidate.executable) if paths["executable_path"] else "",
                          "user_data_path": str(candidate.user_data) if paths["user_data_path"] else ""}
            pending = self.cursor_paths.with_suffix(".tmp")
            pending.unlink(missing_ok=True)
            write_new(pending, json.dumps(normalized, ensure_ascii=False).encode("utf-8"))
            pending.replace(self.cursor_paths)
            candidate.executable_path = normalized["executable_path"]
            candidate.user_data_path = normalized["user_data_path"]
            self.executor.installation = candidate
            return {**result, "configured_executable_path": candidate.executable_path,
                    "configured_user_data_path": candidate.user_data_path, "saved": True}
        finally:
            self.executor.guard.release()

    def begin_cursor_operation(self, stage):
        if not self.executor.guard.acquire(blocking=False):
            raise Conflict("A Cursor operation is already running")
        try:
            if self.job is not None or self.executor.status()["busy"]:
                raise Conflict("A Cursor operation is already running")
            self.executor.update(stage=stage, busy=True, error=None, written=False)
        finally:
            self.executor.guard.release()

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
            self.commands.prune()
            if self.core is not None:
                await self.core.retention.tick()
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

    async def switch_command(self, workspace_id, account_id, platform):
        core = self.require()
        actor = self.identity.actor()
        issued = await core.switches.issue(actor, workspace_id, account_id)
        return core.switches.consume(actor, issued["token"],
            render=lambda delivery: self.commands.render(delivery, platform))

    def start_switch(self, workspace_id, account_id):
        core = self.require()
        actor = self.identity.actor()
        core.repository.check_access(actor, workspace_id, account_id, "use")
        self.begin_cursor_operation("authorizing")

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
        self.executor.backup_path(backup_id)
        self.begin_cursor_operation("checking")

        async def run():
            try:
                await asyncio.to_thread(self.executor.execute, restore_id=backup_id)
            except CoreError:
                pass
            finally:
                self.job = None
        self.job = asyncio.create_task(run())
        return self.executor.status()

    def start_remote_switch(self, connection_id, workspace_id, account_id):
        self.begin_cursor_operation("authorizing")

        async def run():
            report = None
            result = "failure"
            try:
                delivery, report = await asyncio.to_thread(self.connections.delivery, connection_id, workspace_id, account_id)
                await asyncio.to_thread(self.executor.execute, delivery)
                result = "success"
            except Exception:
                if self.executor.status()["busy"]:
                    self.executor.update(stage="failed", busy=False,
                        error="Remote authorization failed; reconnect and request a new switch")
            finally:
                if report:
                    try:
                        await asyncio.to_thread(report, result)
                    except Exception:
                        self.executor.update(report_pending=True)
                self.job = None
        self.job = asyncio.create_task(run())
        return self.executor.status()

    async def shutdown(self):
        # A started SQLite operation is allowed to finish before disposing its database.
        if self.job is not None:
            await self.job
        self.connections.close()
        self.commands.prune(all_files=True)
        if self.core:
            try:
                self.identity.close()
            finally:
                self.core.close()
        self.lock.close()
