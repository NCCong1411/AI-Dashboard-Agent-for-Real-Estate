"""Own conversation transitions in short transactions around graph execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import lru_cache
import hashlib
import json
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agent.graph import build_graph
from app.agent.checkpoints import PostgresConversationSaver
from app.agent.llm import Planner
from app.agent.nodes import AgentNodes, failure
from app.analytics.executor import QueryExecutor, get_query_executor
from app.auth.dependencies import require_builder, runtime_engine
from app.config import Settings, get_settings
from app.db.models import AgentCheckpoint, AuditEvent, Conversation, ConversationMessage, Dashboard, UsageRecord, User
from app.schemas.agent import AgentResponse, ApprovalResponse, ConversationMessageView, ConversationView, MessageRequest
from app.schemas.dashboard import DashboardSpec
from app.services.dashboard import load_published
from app.services.usage import UsageService
from app.services.edits import query_fingerprint


PROCESS_EPOCH = str(uuid4())


def draft_hash(spec: DashboardSpec) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(mode="json"), ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class ConversationService:
    def __init__(self, engine: Engine, settings: Settings, executor: QueryExecutor, planner: Planner | None = None):
        self.engine, self.settings, self.executor = engine, settings, executor
        self.usage = UsageService(engine, settings)
        self.nodes = AgentNodes(settings, self.usage, executor, planner)

    def graph_for(self, user_id: UUID, conversation_id: UUID, thread_id: str):
        return build_graph(self.nodes, PostgresConversationSaver(self.engine, conversation_id, user_id, thread_id))

    def recover(self, user_id: UUID, conversation_id: UUID) -> None:
        """Recover a dead process without replaying plan/query side effects.

        A process epoch is valid under this MVP's single-backend-process rule.
        A completed preview can advance only through the pure interrupt node;
        an earlier interrupted run becomes a controlled failure instead.
        """
        with Session(self.engine) as database, database.begin():
            user = self._builder(database, user_id)
            record = self._owned(database, conversation_id, user_id)
            if record.status != "running" or record.active_run_id is None:
                return
            age = (datetime.now(UTC) - record.active_run_started_at).total_seconds() if record.active_run_started_at else 0
            if record.active_process_epoch == PROCESS_EPOCH and age < self.settings.agent_run_timeout_seconds + 30:
                return
            record.active_process_epoch = PROCESS_EPOCH
            state = {"user_id": str(user_id), "conversation_id": str(record.id), "run_id": str(record.active_run_id),
                     "thread_id": record.thread_id, "scope": user.scope_profile}
        response = AgentResponse.model_validate(failure("run_interrupted", "Lượt chạy bị ngắt khi backend dừng; bạn có thể gửi lại yêu cầu.", retryable=True)["response"])
        query_count = 0
        try:
            saver = PostgresConversationSaver(self.engine, conversation_id, user_id, state["thread_id"])
            config = {"configurable": {"thread_id": state["thread_id"]}, "recursion_limit": 12}
            saved = saver.get_tuple(config)
            values = saved.checkpoint["channel_values"] if saved else {}
            if values.get("run_id") == state["run_id"] and (values.get("response") or {}).get("status") == "awaiting_approval":
                # Only the side-effect-free approval node can follow preview.
                from langsmith import tracing_context
                with tracing_context(enabled=False):
                    result = asyncio.run(self.graph_for(user_id, conversation_id, state["thread_id"]).ainvoke(None, config=config, durability="sync"))
                response = AgentResponse.model_validate(result["response"])
                query_count = result.get("query_count", 0)
                state["scope"] = values["scope"]
        except Exception:
            pass  # Never re-invoke an incomplete plan/execution node on recovery.
        try:
            self._finish(state, response, query_count, 0)
        except HTTPException as error:
            if error.status_code != 409:
                raise

    @staticmethod
    def _builder(database: Session, user_id: UUID) -> User:
        user = database.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_active:
            raise HTTPException(403, "Builder role required")
        return require_builder(user)

    @staticmethod
    def _owned(database: Session, conversation_id: UUID, user_id: UUID) -> Conversation:
        record = database.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_user_id == user_id,
        ).with_for_update())
        if record is None:
            raise HTTPException(404, "Conversation not found")
        return record

    @staticmethod
    def _approval(database: Session, record: Conversation) -> ApprovalResponse | None:
        if not record.draft_hash:
            return None
        receipt = database.scalar(select(AgentCheckpoint).where(
            AgentCheckpoint.conversation_id == record.id, AgentCheckpoint.thread_id == record.thread_id,
            AgentCheckpoint.checkpoint_namespace == "approval",
            AgentCheckpoint.checkpoint_id == f"{record.draft_revision}:{record.draft_hash}",
        ))
        if receipt and receipt.payload["request"]["base_version_number"] == record.base_version_number:
            return ApprovalResponse.model_validate(receipt.payload["response"])
        return None

    @staticmethod
    def _cached_preview(database: Session, record: Conversation, scope: str) -> AgentResponse | None:
        if not record.draft_hash or record.draft_scope != scope:
            return None
        checkpoint = database.scalar(select(AgentCheckpoint).where(
            AgentCheckpoint.conversation_id == record.id, AgentCheckpoint.thread_id == record.thread_id,
            AgentCheckpoint.checkpoint_namespace == "preview",
            AgentCheckpoint.payload["draft_hash"].astext == record.draft_hash,
            AgentCheckpoint.payload["scope"].astext == scope,
            AgentCheckpoint.payload["status"].astext == "awaiting_approval",
        ).order_by(AgentCheckpoint.id.desc()).limit(1))
        if checkpoint:
            preview = AgentResponse.model_validate(checkpoint.payload)
            if preview.draft is not None and draft_hash(preview.draft) == record.draft_hash:
                return preview
        return None

    def create(self, user_id: UUID, dashboard_id: UUID | None = None) -> ConversationView:
        with Session(self.engine) as database, database.begin():
            user = self._builder(database, user_id)
            current_spec, base_version = None, None
            if dashboard_id is not None:
                owned = database.scalar(select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.owner_user_id == user_id))
                if owned is None:
                    raise HTTPException(404, "Dashboard not found")
                _, version, spec = load_published(database, dashboard_id)
                current_spec, base_version = spec.model_dump(mode="json"), version.version_number
            record = Conversation(owner_user_id=user_id, dashboard_id=dashboard_id, thread_id=str(uuid4()),
                                  status="needs_clarification", draft_spec=current_spec, base_version_number=base_version,
                                  draft_scope=user.scope_profile if current_spec else None)
            database.add(record)
            database.flush()
            conversation_id = record.id
            database.add(AuditEvent(actor_user_id=user_id, action="conversation_created", target_type="conversation",
                                    target_id=str(conversation_id), details={"dashboard_id": str(dashboard_id) if dashboard_id else None}))
        return self.get(user_id, conversation_id)

    def get(self, user_id: UUID, conversation_id: UUID) -> ConversationView:
        self.recover(user_id, conversation_id)
        with Session(self.engine) as database, database.begin():
            user = self._builder(database, user_id)
            record = self._owned(database, conversation_id, user_id)
            # Ownership/RBAC is checked before touching checkpoint data.
            checkpoint = database.scalar(select(AgentCheckpoint).where(
                AgentCheckpoint.conversation_id == conversation_id, AgentCheckpoint.thread_id == record.thread_id,
                AgentCheckpoint.checkpoint_namespace == "preview",
            ).order_by(AgentCheckpoint.created_at.desc(), AgentCheckpoint.id.desc()).limit(1))
            response = AgentResponse.model_validate(checkpoint.payload) if checkpoint else None
            if response is not None and response.scope != user.scope_profile:
                response = response.model_copy(update={"results": {}, "sql": {}, "scope": user.scope_profile})
            preview = self._cached_preview(database, record, user.scope_profile) if response and response.status != "awaiting_approval" else None
            messages = list(database.scalars(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
            ).order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()).limit(20)))
            return ConversationView(
                id=record.id, dashboard_id=record.dashboard_id, status=record.status,
                draft_revision=record.draft_revision, draft_hash=record.draft_hash,
                base_version_number=record.base_version_number,
                draft=DashboardSpec.model_validate(record.draft_spec) if record.draft_spec else None,
                messages=[ConversationMessageView(role=item.role, content=item.content, created_at=item.created_at)
                          for item in reversed(messages)],
                response=response, preview=preview, approval=self._approval(database, record),
                mode=response.mode if response else self.settings.llm_mode,
            )

    def _begin(self, user_id: UUID, conversation_id: UUID, request: MessageRequest) -> dict:
        with Session(self.engine) as database, database.begin():
            user = self._builder(database, user_id)
            record = self._owned(database, conversation_id, user_id)
            if record.status == "running" or record.active_run_id is not None:
                raise HTTPException(409, "Conversation already has an active run")
            if request.expected_revision is not None and request.expected_revision != record.draft_revision:
                raise HTTPException(409, "Draft revision is stale")
            approval = self._approval(database, record)
            if approval is not None:
                if not approval.checkpoint_synced:
                    raise HTTPException(409, "Finish the pending approval retry before editing")
                if approval.decision == "approve":
                    # Edit from the version the user actually saw/approved.
                    # A later competing publish remains a base conflict.
                    record.base_version_number = approval.version_number
            cached = {}
            preview = self._cached_preview(database, record, user.scope_profile)
            if preview is not None:
                for query in preview.draft.queries:
                    result = preview.results.get(query.id)
                    if result is not None and result.status != "error":
                        cached[query_fingerprint(query.plan, user.scope_profile)] = result.model_dump(mode="json")
            history = list(database.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == record.id)
                                            .order_by(ConversationMessage.id.desc()).limit(8)))
            run_id = uuid4()
            record.status, record.active_run_id = "running", run_id
            record.active_run_started_at = record.updated_at = datetime.now(UTC)
            record.active_process_epoch = PROCESS_EPOCH
            database.add(ConversationMessage(conversation_id=record.id, role="user", content=request.message))
            state = {"conversation_id": str(record.id), "user_id": str(user_id), "run_id": str(run_id), "thread_id": record.thread_id,
                     "scope": user.scope_profile, "prompt": request.message, "current_spec": record.draft_spec,
                     "history": [{"role": item.role, "content": item.content[:500]} for item in reversed(history)],
                     "cached_results": cached}
        return state

    def _finish(self, state: dict, response: AgentResponse, query_count: int, elapsed_ms: int) -> AgentResponse:
        user_id, conversation_id, run_id = (UUID(state[name]) for name in ("user_id", "conversation_id", "run_id"))
        with Session(self.engine) as database, database.begin():
            user = database.scalar(select(User).where(User.id == user_id).with_for_update())
            record = self._owned(database, conversation_id, user_id)
            if record.active_run_id != run_id:
                raise HTTPException(409, "Run is no longer current")
            if user is None or not user.is_active or user.role != "builder" or user.scope_profile != state["scope"]:
                response = AgentResponse.model_validate(failure("identity_changed", "Quyền truy cập đã thay đổi; hãy đăng nhập lại.")["response"])
            if response.status == "awaiting_approval" and response.draft is not None:
                record.draft_spec = response.draft.model_dump(mode="json")
                record.draft_revision += 1
                record.draft_hash = draft_hash(response.draft)
                record.draft_scope = state["scope"]
            record.status = response.status
            record.active_run_id, record.active_run_started_at = None, None
            record.active_process_epoch = None
            record.updated_at = datetime.now(UTC)
            response = response.model_copy(update={
                "conversation_id": conversation_id, "run_id": run_id,
                "draft_revision": record.draft_revision, "draft_hash": record.draft_hash,
                "base_version_number": record.base_version_number, "scope": user.scope_profile if user else None,
                "mode": self.settings.llm_mode,
            })
            database.add(ConversationMessage(conversation_id=record.id, role="assistant", content=response.message))
            database.add(AgentCheckpoint(conversation_id=record.id, thread_id=record.thread_id, checkpoint_namespace="preview",
                                         checkpoint_id=str(run_id), payload=response.model_dump(mode="json")))
            usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id, UsageRecord.user_id == user_id))
            if usage:
                usage.query_count = query_count
                usage.latency_ms = max(usage.latency_ms, elapsed_ms)
            database.add(AuditEvent(actor_user_id=user_id, action="conversation_run_finished", target_type="conversation",
                                    target_id=str(record.id), details={"run_id": str(run_id), "status": response.status,
                                                                    "query_count": query_count, "latency_ms": elapsed_ms}))
        return response

    async def send(self, user_id: UUID, conversation_id: UUID, request: MessageRequest) -> AgentResponse:
        await asyncio.to_thread(self.recover, user_id, conversation_id)
        state = await asyncio.to_thread(self._begin, user_id, conversation_id, request)
        started = perf_counter()
        query_count = 0
        try:
            # All node values remain local; no external tracing is enabled by
            # the application. Provider calls are only through UsageService.
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                async with asyncio.timeout(self.settings.agent_run_timeout_seconds):
                    graph = self.graph_for(user_id, conversation_id, state["thread_id"])
                    result = await graph.ainvoke(state, config={"recursion_limit": 12,
                        "configurable": {"thread_id": state["thread_id"]}}, durability="sync")
            query_count = result.get("query_count", 0)
            response = AgentResponse.model_validate(result["response"])
        except asyncio.CancelledError:
            response = AgentResponse.model_validate(failure("run_cancelled", "Lượt chạy đã bị ngắt; bạn có thể thử lại.", retryable=True)["response"])
            await asyncio.shield(asyncio.to_thread(self._finish, state, response, query_count,
                                                   round((perf_counter() - started) * 1000)))
            raise
        except Exception:
            response = AgentResponse.model_validate(failure("execution_error", "Không thể hoàn tất lượt chạy; dashboard đã duyệt được giữ nguyên.", retryable=True)["response"])
        return await asyncio.to_thread(self._finish, state, response, query_count, round((perf_counter() - started) * 1000))


@lru_cache
def get_conversation_service() -> ConversationService:
    return ConversationService(runtime_engine(), get_settings(), get_query_executor())
