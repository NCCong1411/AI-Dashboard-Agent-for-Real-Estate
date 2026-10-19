"""Durable, idempotent per-call LLM budget reservations in app metadata."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_usage_calls"
down_revision = "0002_analytics_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app.usage_records.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_input_tokens", sa.Integer()),
        sa.Column("actual_output_tokens", sa.Integer()),
        sa.Column("input_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("charged_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','settled','unknown')", name="ck_usage_calls_status"),
        sa.CheckConstraint("mode IN ('mock','live')", name="ck_usage_calls_mode"),
        sa.CheckConstraint("reserved_input_tokens >= 0 AND reserved_output_tokens >= 0 AND charged_cost_usd >= 0 AND input_price >= 0 AND output_price >= 0", name="ck_usage_calls_nonnegative"),
        schema="app",
    )
    op.create_index("ix_usage_calls_run_day", "usage_calls", ["run_id", "usage_date"], schema="app")


def downgrade():
    op.drop_table("usage_calls", schema="app")
