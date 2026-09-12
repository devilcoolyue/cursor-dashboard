"""Indexes for bounded audit retention and workspace history."""
from alembic import op

revision = "0004_retention"
down_revision = "0003_devices"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("audit_chronological", "audit_events", ["created_at", "id"])
    op.create_index("audit_workspace_chronological", "audit_events", ["workspace_id", "created_at", "id"])


def downgrade():
    raise RuntimeError("Restore a matched database backup; destructive schema downgrade is unsupported")
