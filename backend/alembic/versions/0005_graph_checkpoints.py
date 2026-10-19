"""Persist LangGraph intermediate writes and identify interrupted processes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_graph_checkpoints"
down_revision = "0004_conversation_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("active_process_epoch", sa.String(36)), schema="app")
    op.create_table(
        "graph_checkpoint_writes",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app.conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.String(128), primary_key=True),
        sa.Column("checkpoint_namespace", sa.String(128), primary_key=True),
        sa.Column("checkpoint_id", sa.String(128), primary_key=True),
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("write_index", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(128), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        schema="app",
    )


def downgrade():
    op.drop_table("graph_checkpoint_writes", schema="app")
    op.drop_column("conversations", "active_process_epoch", schema="app")
