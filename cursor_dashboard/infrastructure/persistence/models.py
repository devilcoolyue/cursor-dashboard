from __future__ import annotations

import time
import uuid

from sqlalchemy import (Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, LargeBinary, String, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    login: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    instance_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    __table_args__ = (CheckConstraint("kind IN ('personal','team')", name="workspace_kind"),)


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    __table_args__ = (
        CheckConstraint("role IN ('owner','admin','member','viewer')", name="membership_role"),
        Index("one_owner_per_workspace", "workspace_id", unique=True, sqlite_where=text("role = 'owner'")),
    )


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="cursor")
    provider_subject: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320))
    label: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="account_scope"),
                     UniqueConstraint("workspace_id", "provider", "provider_subject", name="account_subject"),
                     UniqueConstraint("workspace_id", "provider", "email", name="account_email"))


class Credential(Base):
    __tablename__ = "credentials"
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    key_id: Mapped[str] = mapped_column(String(36))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    refreshed_at: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "account_id"], ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),
        CheckConstraint("version > 0", name="credential_version"),
    )


class Snapshot(Base):
    __tablename__ = "usage_snapshots"
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation: Mapped[str] = mapped_column(String(36))
    data: Mapped[dict | None] = mapped_column(JSON)
    ok_at: Mapped[int] = mapped_column(Integer, default=0)
    attempted_at: Mapped[int] = mapped_column(Integer, default=0)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(256))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "account_id"],
                       ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),)


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="tag_name"),
                     UniqueConstraint("workspace_id", "id", name="tag_scope"))


class AccountTag(Base):
    __tablename__ = "account_tags"
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "account_id"], ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["workspace_id", "tag_id"], ["tags.workspace_id", "tags.id"], ondelete="CASCADE"),
    )


class Grant(Base):
    __tablename__ = "account_grants"
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    level: Mapped[str] = mapped_column(String(8))
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "account_id"], ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["workspace_id", "user_id"], ["memberships.workspace_id", "memberships.user_id"], ondelete="CASCADE"),
        CheckConstraint("level IN ('view','use')", name="grant_level"),
    )


class Lease(Base):
    __tablename__ = "auth_leases"
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "account_id"],
                       ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),)


class Metadata(Base):
    __tablename__ = "core_metadata"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String)


class LegacyImport(Base):
    __tablename__ = "legacy_imports"
    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    report: Mapped[dict] = mapped_column(JSON)


class LegacyMapping(Base):
    __tablename__ = "legacy_account_mapping"
    source_hash: Mapped[str] = mapped_column(ForeignKey("legacy_imports.source_hash"), primary_key=True)
    old_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36))
    # Mapping is an immutable import receipt and remains after an imported account is deleted.


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), default="web", server_default="web")
    device_id: Mapped[str | None] = mapped_column(String(36))
    device_name: Mapped[str | None] = mapped_column(String(128))
    __table_args__ = (CheckConstraint("kind IN ('web','device')", name="session_kind"),)


class DeviceAuthorization(Base):
    __tablename__ = "device_authorizations"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"))
    challenge: Mapped[str] = mapped_column(String(43))
    redirect_uri: Mapped[str] = mapped_column(String(256))
    device_id: Mapped[str] = mapped_column(String(36))
    device_name: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[float] = mapped_column(Float)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    login: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    issuer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[float] = mapped_column(Float)
    used_at: Mapped[float | None] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint("role IN ('admin','member','viewer')", name="invitation_role"),)


class SwitchTicket(Base):
    __tablename__ = "switch_tickets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"))
    generation: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "account_id"],
                       ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    # Deliberately survives resource deletion. No payload, names or secrets.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), default="success")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    request_id: Mapped[str | None] = mapped_column(String(36))
    changes: Mapped[dict | None] = mapped_column(JSON)
