"""Track the server-owned active run and the scope of cached draft results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_conversation_runs"
down_revision = "0003_usage_calls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("active_run_id", postgresql.UUID(as_uuid=True)), schema="app")
    op.add_column("conversations", sa.Column("active_run_started_at", sa.DateTime(timezone=True)), schema="app")
    op.add_column("conversations", sa.Column("draft_scope", sa.String(8)), schema="app")


def downgrade():
    for name in ("draft_scope", "active_run_started_at", "active_run_id"):
        op.drop_column("conversations", name, schema="app")
