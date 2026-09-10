"""Read a stopped, consistent legacy SQLite backup; import atomically into a V2 database."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import func, select

from ...domain.core import AccountRef, Conflict, Secrets
from .models import (Account, Credential, LegacyImport, LegacyMapping, Snapshot, Workspace, new_id)
from .repository import normalize_email, normalize_subject


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(repr=False)
class LegacyBackup:
    path: Path
    digest: str
    accounts: list[dict]
    snapshots: dict[int, dict]
    report: dict


def _snapshot_data(value):
    if value is None:
        return None
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError()
    allowed = {"label", "email", "plan", "cycle", "quota", "spend_usd", "on_demand", "grok_weekly", "notice"}
    forbidden = {"cookie", "access_token", "refresh_token", "accesstoken", "refreshtoken", "token"}
    def scrub(item):
        if isinstance(item, dict):
            return {k: scrub(v) for k, v in item.items() if k.casefold() not in forbidden}
        if isinstance(item, list):
            return [scrub(v) for v in item]
        return item
    return scrub({key: value for key, value in data.items() if key in allowed})


def read_backup(source: Path) -> LegacyBackup:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise Conflict("Legacy backup does not exist")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(f"{source}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size:
            raise Conflict("Use a standalone SQLite-consistent backup; source has a WAL or journal")
    before = digest_file(source)
    issues = Counter()
    try:
        # immutable avoids creating SHM/journal files; only a stopped standalone backup is supported.
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Conflict("Legacy database integrity check failed")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "accounts" not in tables or "alembic_version" in tables:
                raise Conflict("Source is not a supported legacy database")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
            if not {"id", "label", "cookie"}.issubset(columns):
                raise Conflict("Legacy accounts schema is unsupported")
            version = "1"
            if "metadata" in tables:
                row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                version = row[0] if row else "1"
            if version not in {"1", "2", "3", "4"}:
                raise Conflict("Legacy schema version is unsupported")
            accounts = [dict(row) for row in conn.execute("SELECT * FROM accounts ORDER BY id")]
            identities, emails, subjects = defaultdict(list), set(), set()
            for account in accounts:
                if not isinstance(account["id"], int) or account["id"] <= 0:
                    raise Conflict("Legacy account IDs are invalid")
                if not isinstance(account["cookie"], str):
                    raise Conflict("Legacy credential format is invalid")
                for field in ("label", "email", "auth_subject", "department"):
                    if account.get(field) is not None and not isinstance(account[field], str):
                        raise Conflict("Legacy account metadata format is invalid")
                old_key = account.get("email") or account.get("label") or "unnamed"
                identities[old_key].append(account)
                email, subject = normalize_email(account.get("email")), normalize_subject(account.get("auth_subject"))
                if (email and email in emails) or (subject and subject in subjects):
                    raise Conflict("Legacy accounts have conflicting normalized identities; resolve duplicates in a copy first")
                if email:
                    emails.add(email)
                if subject:
                    subjects.add(subject)
                for field in ("access_token", "refresh_token"):
                    if not isinstance(account.get(field, ""), str):
                        raise Conflict("Legacy credential format is invalid")
                for field in ("updated_at", "token_expires_at", "auth_refreshed_at"):
                    try:
                        if int(account.get(field) or 0) < 0:
                            raise ValueError()
                    except (ValueError, TypeError, OverflowError):
                        raise Conflict("Legacy account timestamp is invalid") from None
            snapshots = {}
            if "snapshots" in tables:
                for source_row in conn.execute("SELECT * FROM snapshots"):
                    row = dict(source_row)
                    matches = identities.get(row.get("account_id"), [])
                    if len(matches) != 1:
                        issues["snapshot_ambiguous" if matches else "snapshot_orphaned"] += 1
                        continue
                    account = matches[0]
                    if row.get("fingerprint") != hashlib.sha256(account["cookie"].encode()).hexdigest()[:16]:
                        issues["snapshot_stale"] += 1
                        continue
                    try:
                        row["data"] = _snapshot_data(row.get("payload"))
                        for field in ("ok_at", "attempted_at", "failures"):
                            row[field] = max(0, int(row.get(field) or 0))
                    except (ValueError, TypeError, OverflowError):
                        issues["snapshot_invalid_json"] += 1
                        continue
                    snapshots[account["id"]] = row
    except sqlite3.Error:
        raise Conflict("Cannot read the legacy backup; check its schema and integrity") from None
    if digest_file(source) != before:
        raise Conflict("Legacy backup changed during inspection; stop its writer and create a fresh backup")
    report = {"source_hash": before, "source_schema": version, "accounts": len(accounts),
              "snapshots": len(snapshots), "tags": len({(a.get("department") or "").strip() for a in accounts}
                                                        - {""}), "skipped": dict(issues)}
    return LegacyBackup(source, before, accounts, snapshots, report)


def import_backup(repository, actor, workspace_id, source: Path):
    if source.expanduser().resolve() == repository.db.path.resolve():
        raise Conflict("Source and destination databases must be different")
    backup = read_backup(source)
    with repository.db.transaction(write=True) as session:
        member = repository._membership(session, actor, workspace_id)
        workspace = session.get(Workspace, workspace_id)
        if member.role != "owner" or workspace.kind != "team":
            raise Conflict("Import requires the Owner of a dedicated team workspace")
        receipt = session.get(LegacyImport, backup.digest)
        if receipt:
            if receipt.workspace_id != workspace_id:
                raise Conflict("This backup was already imported into another workspace")
            return {**receipt.report, "already_imported": True}
        if session.scalar(select(func.count()).select_from(Account)) or session.scalar(select(func.count()).select_from(LegacyImport)):
            raise Conflict("Destination already contains account data; implicit merge is not supported")
        session.add(LegacyImport(source_hash=backup.digest, workspace_id=workspace_id, report=backup.report))
        session.flush()
        for old in backup.accounts:
            account = Account(id=new_id(), workspace_id=workspace_id, provider="cursor",
                provider_subject=normalize_subject(old.get("auth_subject")), email=normalize_email(old.get("email")),
                label=old.get("label") or "Imported account", updated_at=int(old.get("updated_at") or 0))
            session.add(account)
            session.flush()
            ref = AccountRef(workspace_id, account.id, new_id(), 1)
            payload = Secrets(old["cookie"], old.get("access_token", ""), old.get("refresh_token", ""))
            key_id, ciphertext = repository.cipher.seal(payload, ref)
            session.add(Credential(workspace_id=workspace_id, account_id=account.id, generation=ref.generation,
                version=1, key_id=key_id, ciphertext=ciphertext, expires_at=int(old.get("token_expires_at") or 0),
                refreshed_at=int(old.get("auth_refreshed_at") or 0), invalid=bool(old.get("auth_invalid"))))
            repository._set_tags(session, account, [old.get("department") or ""])
            session.add(LegacyMapping(source_hash=backup.digest, old_id=old["id"],
                                     workspace_id=workspace_id, account_id=account.id))
            previous = backup.snapshots.get(old["id"])
            if previous:
                kind = previous.get("error_kind")
                if kind and kind not in {"expired", "rate_limited", "network", "error"}:
                    kind = "error"
                session.add(Snapshot(workspace_id=workspace_id, account_id=account.id, generation=ref.generation,
                    data=previous["data"], ok_at=int(previous.get("ok_at") or 0),
                    attempted_at=int(previous.get("attempted_at") or 0), error_kind=kind,
                    error_message="Imported last failure; retry refresh" if kind else None,
                    failures=int(previous.get("failures") or 0)))
        if digest_file(backup.path) != backup.digest:
            raise Conflict("Legacy backup changed during import; no changes were committed")
    return {**backup.report, "already_imported": False}
