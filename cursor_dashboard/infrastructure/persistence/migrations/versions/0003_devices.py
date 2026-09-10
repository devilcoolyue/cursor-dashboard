"""PKCE authorization codes and revocable native device identities."""
from alembic import op
import sqlalchemy as sa

revision = "0003_devices"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_sessions", sa.Column("kind", sa.String(16),
        sa.CheckConstraint("kind IN ('web','device')", name="session_kind"), nullable=False, server_default="web"))
    op.add_column("user_sessions", sa.Column("device_id", sa.String(36)))
    op.add_column("user_sessions", sa.Column("device_name", sa.String(128)))
    # SQLite permits an inline CHECK on the new column without rebuilding a
    # referenced session table (which would cascade-delete existing tickets).
    op.create_table("device_authorizations",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("user_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("challenge", sa.String(43), nullable=False),
        sa.Column("redirect_uri", sa.String(256), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False))


def downgrade():
    raise RuntimeError("Restore a matched database backup; destructive schema downgrade is unsupported")
