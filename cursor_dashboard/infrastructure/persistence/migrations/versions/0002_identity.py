"""Identity, revocable sessions, invitations, audit and switch authorization."""
from alembic import op
import sqlalchemy as sa

revision = "0002_identity"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("instance_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.create_table("user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False))
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_table("invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("login", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("issuer_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("used_at", sa.Float()),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('admin','member','viewer')", name="invitation_role"))
    op.create_index("ix_invitations_workspace_id", "invitations", ["workspace_id"])
    op.create_table("switch_tickets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("user_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float()),
        sa.ForeignKeyConstraint(["workspace_id", "account_id"], ["accounts.workspace_id", "accounts.id"], ondelete="CASCADE"))
    op.create_table("audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36)),
        sa.Column("workspace_id", sa.String(36)),
        sa.Column("resource_id", sa.String(36)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("request_id", sa.String(36)),
        sa.Column("changes", sa.JSON()))
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])


def downgrade():
    raise RuntimeError("Restore a matched database backup; destructive schema downgrade is unsupported")
