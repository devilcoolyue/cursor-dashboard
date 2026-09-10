from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import time

from ..domain.core import Conflict, NotFound, Secrets
from ..infrastructure.providers.cursor.authorization import exchange_cookie, verify_identity
from ..pools import fill_visible
from ..usage import assemble_detail, iso_to_dt
from .queries import QueryFailure, classify, collect_usage


class AccountService:
    def __init__(self, repository, credentials, gateway, config):
        self.repo, self.credentials, self.gateway, self.config = repository, credentials, gateway, config
        self._locks = {}
        self._details = OrderedDict()

    @asynccontextmanager
    async def _account_lock(self, key):
        entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                self._locks.pop(key, None)

    def list(self, actor, workspace_id):
        rows = self.repo.list_views(actor, workspace_id)
        visible_data = [row["data"] for row in rows if row["data"]]
        for row in rows:
            row["data"] = fill_visible(row["data"], visible_data)
            row["expired"] = bool(row["error_kind"] == "expired" and (row["data"] is None or row["failures"] >= 2))
            row["stale"] = bool(row["data"] is not None and row["error_kind"] and not row["expired"])
            row["pending"] = row["data"] is None and row["error_kind"] is None
        return rows

    def get(self, actor, workspace_id, account_id):
        return next((row for row in self.list(actor, workspace_id) if row["id"] == account_id), None) or self._missing()

    @staticmethod
    def _missing():
        raise NotFound("Workspace or account is unavailable")

    async def authorize(self, actor, workspace_id, cookie, *, label=None, tags=(), account_id=None):
        self.repo.check_access(actor, workspace_id, account_id, "manage")
        expected = self.repo.authorized(workspace_id, account_id) if account_id else None
        session, email = await exchange_cookie(cookie, label or "Account", self.gateway,
                                               expected_email=expected.email if expected else None)
        data = await collect_usage(label or email, lambda name: self.gateway("", label or email, name, session.token),
                                   email=email, subject=session.subject, verify=verify_identity)
        saved = self.repo.put_authorization(actor, workspace_id, email=email, subject=session.subject,
                    label=label, secrets=Secrets(cookie, session.token, session.refresh_token),
                    expires_at=session.expires_at, expected=expected.ref if expected else None, data=data, tags=tags)
        return self.get(actor, workspace_id, saved.ref.account_id)

    async def refresh(self, actor, workspace_id, account_id):
        self.repo.check_access(actor, workspace_id, account_id, "use")
        async with self._account_lock((workspace_id, account_id)):
            account = self.repo.authorized(workspace_id, account_id)
            try:
                account = await self.credentials.ensure(workspace_id, account_id)
                data = await collect_usage(account.label,
                    lambda name: self.credentials.request(workspace_id, account_id, name),
                    email=account.email, subject=account.subject, verify=verify_identity)
            except Exception as error:
                errors = error.errors if isinstance(error, QueryFailure) else [error]
                self.repo.check_access(actor, workspace_id, account_id, "use")
                saved = self.repo.record_snapshot(account.ref, error=classify(errors))
            else:
                self.repo.check_access(actor, workspace_id, account_id, "use")
                saved = self.repo.record_snapshot(account.ref, data=data)
            if not saved:
                raise Conflict("Authorization changed while refreshing; old result discarded")
            return self.get(actor, workspace_id, account_id)

    async def detail(self, actor, workspace_id, account_id):
        row = self.get(actor, workspace_id, account_id)
        data = row["data"] or {}
        start_text = (data.get("cycle") or {}).get("start")
        start = iso_to_dt(start_text)
        if start is None:
            raise Conflict("Account billing period is unavailable; refresh the account first")
        key = (workspace_id, account_id, row["authorization_generation"], start_text)
        cached = self._details.get(key)
        if cached and time.monotonic() - cached[0] < self.config.detail_ttl:
            self._details.move_to_end(key)
            return deepcopy(cached[1])
        now = datetime.now(timezone.utc)
        raw = await self.credentials.request(workspace_id, account_id, "desktop_aggregated",
                                             int(start.timestamp() * 1000), int(now.timestamp() * 1000))
        current = self.get(actor, workspace_id, account_id)
        current_start = ((current["data"] or {}).get("cycle") or {}).get("start")
        if current["authorization_generation"] != key[2] or current_start != start_text:
            raise Conflict("Authorization or billing period changed; old detail discarded")
        result = {**assemble_detail(raw), "id": account_id, "workspace_id": workspace_id,
                  "cycle_start": start_text, "fetched_at": now.isoformat()}
        self._details[key] = (time.monotonic(), deepcopy(result))
        self._details.move_to_end(key)
        while len(self._details) > 128:
            self._details.popitem(last=False)
        return result

    def edit(self, actor, workspace_id, account_id, **changes):
        self.repo.edit(actor, workspace_id, account_id, **changes)
        return self.get(actor, workspace_id, account_id)

    def delete(self, actor, workspace_id, account_id):
        self.repo.delete(actor, workspace_id, account_id)
        for key in list(self._details):
            if key[:2] == (workspace_id, account_id):
                del self._details[key]
