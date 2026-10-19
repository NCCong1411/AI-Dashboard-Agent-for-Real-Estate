"""SQLAlchemy metadata for application-owned tables in schema ``app``."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


APP_SCHEMA = "app"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('builder', 'viewer')", name="ck_users_role"),
        CheckConstraint("scope_profile IN ('ALL', 'CA', 'TX')", name="ck_users_scope"),
        UniqueConstraint("username", name="uq_users_username"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_profile: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        Index("ix_sessions_user_expires", "user_id", "expires_at"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Dashboard(TimestampMixin, Base):
    __tablename__ = "dashboards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["app.dashboard_versions.id", "app.dashboard_versions.dashboard_id"],
            name="fk_dashboards_current_version_same_dashboard",
            use_alter=True,
        ),
        Index("ix_dashboards_owner", "owner_user_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class DashboardVersion(Base):
    __tablename__ = "dashboard_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="ck_dashboard_versions_number"),
        CheckConstraint("base_version_number IS NULL OR base_version_number >= 0", name="ck_dashboard_versions_base"),
        UniqueConstraint("id", "dashboard_id", name="uq_dashboard_versions_identity_dashboard"),
        UniqueConstraint("dashboard_id", "version_number", name="uq_dashboard_versions_number"),
        UniqueConstraint("dashboard_id", "draft_hash", name="uq_dashboard_versions_draft_hash"),
        UniqueConstraint("approval_key", name="uq_dashboard_versions_approval_key"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.dashboards.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_version_number: Mapped[int | None] = mapped_column(Integer)
    draft_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_key: Mapped[str] = mapped_column(String(128), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="RESTRICT"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('running','needs_clarification','awaiting_approval','completed','failed')", name="ck_conversations_status"),
        CheckConstraint("draft_revision >= 0", name="ck_conversations_revision"),
        UniqueConstraint("thread_id", name="uq_conversations_thread"),
        Index("ix_conversations_owner_updated", "owner_user_id", "updated_at"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False)
    dashboard_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.dashboards.id", ondelete="SET NULL"))
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    draft_hash: Mapped[str | None] = mapped_column(String(64))
    base_version_number: Mapped[int | None] = mapped_column(Integer)
    draft_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    active_run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_process_epoch: Mapped[str | None] = mapped_column(String(36))
    draft_scope: Mapped[str | None] = mapped_column(String(8))


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint("thread_id", "checkpoint_namespace", "checkpoint_id", name="uq_checkpoints_identity"),
        Index("ix_checkpoints_conversation_created", "conversation_id", "created_at"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.conversations.id", ondelete="CASCADE"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_namespace: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GraphCheckpointWrite(Base):
    __tablename__ = "graph_checkpoint_writes"
    __table_args__ = ({"schema": APP_SCHEMA},)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.conversations.id", ondelete="CASCADE"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    write_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    task_path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("llm_calls >= 0 AND input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_usd >= 0 AND query_count >= 0 AND latency_ms >= 0", name="ck_usage_nonnegative"),
        UniqueConstraint("run_id", name="uq_usage_run"),
        Index("ix_usage_user_day", "user_id", "usage_date"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id", ondelete="RESTRICT"), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UsageCall(Base):
    """Durable reservations survive a provider timeout or process crash."""

    __tablename__ = "usage_calls"
    __table_args__ = (
        CheckConstraint("status IN ('pending','settled','unknown')", name="ck_usage_calls_status"),
        CheckConstraint("mode IN ('mock','live')", name="ck_usage_calls_mode"),
        CheckConstraint("reserved_input_tokens >= 0 AND reserved_output_tokens >= 0 AND charged_cost_usd >= 0 AND input_price >= 0 AND output_price >= 0", name="ck_usage_calls_nonnegative"),
        Index("ix_usage_calls_run_day", "run_id", "usage_date"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.usage_records.run_id", ondelete="CASCADE"), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    reserved_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_input_tokens: Mapped[int | None] = mapped_column(Integer)
    actual_output_tokens: Mapped[int | None] = mapped_column(Integer)
    input_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    output_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    charged_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_target", "target_type", "target_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app.users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
