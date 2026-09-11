"""Password encrypted portable accounts and key recovery, native file dialogs only."""
from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from ..domain.core import AccountRef, Conflict, SecretError, Secrets
from ..infrastructure.persistence.models import Account, AccountTag, Credential, Snapshot, Tag, new_id
from ..infrastructure.persistence.policy import audit, authorize
from .files import write_new

MAGIC = b"CURSOR-PANEL-ARCHIVE\x00\x01"
MAX_SIZE = 64 * 1024 * 1024


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=256)
    email: str | None = Field(max_length=320)
    subject: str | None = Field(max_length=256)
    cookie: str = Field(max_length=16384, repr=False)
    access_token: str = Field(max_length=32768, repr=False)
    refresh_token: str = Field(max_length=32768, repr=False)
    expires_at: int = Field(ge=0)
    refreshed_at: int = Field(ge=0)
    invalid: bool
    tags: list[str] = Field(max_length=100)
    snapshot: dict | None
    ok_at: int = Field(ge=0)


class Archive(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    accounts: list[Record] = Field(max_length=10000, repr=False)
    recovery_keys: dict = Field(repr=False)


def derive(password: str, salt: bytes):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise Conflict("Archive password must contain 12 to 256 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def seal(document: Archive, password: str):
    salt, nonce = os.urandom(16), os.urandom(12)
    payload = document.model_dump_json().encode("utf-8")
    if len(payload) > MAX_SIZE - 1024:
        raise Conflict("Archive is too large")
    header = MAGIC + salt + nonce
    return header + AESGCM(derive(password, salt)).encrypt(nonce, payload, header)


def read_archive(path: Path, password: str):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_SIZE + 1)
        start = len(MAGIC)
        if not data.startswith(MAGIC) or not start + 44 <= len(data) <= MAX_SIZE:
            raise ValueError()
        salt, nonce = data[start:start + 16], data[start + 16:start + 28]
        header = data[:start + 28]
        plain = AESGCM(derive(password, salt)).decrypt(nonce, data[start + 28:], header)
        return Archive.model_validate_json(plain)
    except (OSError, ValueError, InvalidTag, ValidationError):
        raise SecretError("Archive password is incorrect or the file is invalid") from None


def export_archive(core, actor, workspace_id, path: Path, password: str, keys):
    with core.db.transaction(write=True) as session:
        authorize(session, actor, "export", workspace_id)
        records = []
        for account in session.scalars(select(Account).where(Account.workspace_id == workspace_id)):
            item = core.repository._authorized(session, account)
            snapshot = session.get(Snapshot, (workspace_id, account.id))
            valid = snapshot is not None and snapshot.generation == item.ref.generation
            tags = list(session.scalars(select(Tag.name).join(AccountTag,
                (Tag.id == AccountTag.tag_id) & (Tag.workspace_id == AccountTag.workspace_id)).where(
                AccountTag.workspace_id == workspace_id, AccountTag.account_id == account.id)))
            records.append(Record(label=item.label, email=item.email, subject=item.subject,
                cookie=item.secrets.cookie, access_token=item.secrets.access_token,
                refresh_token=item.secrets.refresh_token, expires_at=item.expires_at,
                refreshed_at=item.refreshed_at, invalid=item.invalid, tags=tags,
                snapshot=snapshot.data if valid else None, ok_at=snapshot.ok_at if valid else 0))
        data = seal(Archive(version=1, accounts=records, recovery_keys=keys.document), password)
        # Never truncate an existing archive, even when the file dialog confirms overwrite.
        try:
            write_new(path, data)
        except OSError:
            raise Conflict("Cannot create archive; choose a new file in a writable directory") from None
        audit(session, actor, "archive.export", workspace_id)
    return {"count": len(records)}


def import_archive(core, actor, workspace_id, path: Path, password: str):
    core.repository.check_access(actor, workspace_id, action="export")
    document = read_archive(path, password)
    with core.db.transaction(write=True) as session:
        authorize(session, actor, "export", workspace_id)
        # Explicit empty-space import avoids partial merges, duplicate credentials and stale overwrites.
        if session.scalar(select(Account.id).where(Account.workspace_id == workspace_id).limit(1)):
            raise Conflict("Import requires an empty personal space")
        for item in document.accounts:
            if any(not tag.strip() or len(tag) > 128 for tag in item.tags):
                raise Conflict("Archive contains invalid tags")
            account = Account(id=new_id(), workspace_id=workspace_id, provider="cursor",
                provider_subject=item.subject, email=item.email, label=item.label)
            session.add(account)
            session.flush()
            ref = AccountRef(workspace_id, account.id, new_id(), 1)
            key_id, ciphertext = core.repository.cipher.seal(
                Secrets(item.cookie, item.access_token, item.refresh_token), ref)
            session.add(Credential(workspace_id=workspace_id, account_id=account.id,
                generation=ref.generation, version=1, key_id=key_id, ciphertext=ciphertext,
                expires_at=item.expires_at, refreshed_at=item.refreshed_at, invalid=item.invalid))
            core.repository._set_tags(session, account, item.tags)
            if item.snapshot is not None:
                session.add(Snapshot(workspace_id=workspace_id, account_id=account.id,
                    generation=ref.generation, data=item.snapshot, ok_at=min(item.ok_at, int(time.time())),
                    attempted_at=0))
        audit(session, actor, "archive.import", workspace_id)
    return {"count": len(document.accounts)}
