"""Commit one approval receipt/version before resuming a pure graph node."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import logging
from uuid import UUID

from fastapi import HTTPException
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.compiler import compile_analysis_plan
from app.db.models import AgentCheckpoint, AuditEvent, ConversationMessage, Dashboard, DashboardVersion
from app.schemas.agent import AgentResponse, ApprovalRequest, ApprovalResponse
from app.schemas.dashboard import DashboardSpec
from app.services.conversation import ConversationService, draft_hash


logger = logging.getLogger(__name__)


def receipt_id(revision: int, content_hash: str) -> str:
    return f"{revision}:{content_hash}"


class ApprovalService:
    def __init__(self, conversations: ConversationService):
        self.conversations = conversations
        self.engine = conversations.engine

    def _commit(self, user_id: UUID, conversation_id: UUID, request: ApprovalRequest):
        service = self.conversations
        with Session(self.engine) as database, database.begin():
            # Same lock order as messages/recovery. Serializes double clicks
            # and competing conversations belonging to the same Builder.
            user = service._builder(database, user_id)
            record = service._owned(database, conversation_id, user_id)
            if (record.draft_revision != request.draft_revision or record.draft_hash != request.draft_hash
                    or record.base_version_number != request.base_version_number):
                raise HTTPException(409, "Draft revision/hash/base version is stale")
            if record.draft_scope != user.scope_profile:
                raise HTTPException(409, "Draft scope has changed; refresh the preview")
            identifier = receipt_id(request.draft_revision, request.draft_hash)
            existing = database.scalar(select(AgentCheckpoint).where(
                AgentCheckpoint.conversation_id == record.id, AgentCheckpoint.thread_id == record.thread_id,
                AgentCheckpoint.checkpoint_namespace == "approval", AgentCheckpoint.checkpoint_id == identifier,
            ))
            if existing:
                if existing.payload["request"] != request.model_dump(mode="json"):
                    raise HTTPException(409, "This draft already has a different approval decision")
                return dict(existing.payload), record.thread_id
            if record.status != "awaiting_approval" or record.active_run_id is not None:
                raise HTTPException(409, "Draft is not awaiting approval")
            preview = service._cached_preview(database, record, user.scope_profile)
            if preview is None or preview.run_id is None:
                raise HTTPException(409, "Current preview is unavailable")
            spec = DashboardSpec.model_validate(record.draft_spec)
            if draft_hash(spec) != request.draft_hash:
                raise HTTPException(409, "Stored draft hash does not match")
            for query in spec.queries:
                compile_analysis_plan(query.plan)
            # Read/authorize graph only after ownership checks and before any
            # publication. No application transaction spans an await/provider.
            # Saver authorization uses its own short read-only transaction.
            saved = service.graph_for(user_id, record.id, record.thread_id).get_state(
                {"configurable": {"thread_id": record.thread_id}})
            if (saved.next != ("await_approval",) or saved.values.get("run_id") != str(preview.run_id)
                    or saved.values.get("scope") != user.scope_profile):
                raise HTTPException(409, "Graph is not waiting for this preview")
            dashboard, version_number = None, None
            if record.dashboard_id is not None:
                dashboard = database.scalar(select(Dashboard).where(
                    Dashboard.id == record.dashboard_id, Dashboard.owner_user_id == user_id,
                ).with_for_update())
                if dashboard is None:
                    raise HTTPException(404, "Dashboard not found")
                current = database.get(DashboardVersion, dashboard.current_version_id) if dashboard.current_version_id else None
                if current is None or current.version_number != request.base_version_number:
                    raise HTTPException(409, {"code": "base_version_conflict", "message": "Published base version has changed"})
                version_number = current.version_number + 1
            elif request.base_version_number is not None:
                raise HTTPException(409, "New dashboard cannot have a base version")
            # Stable identity includes content and the exact server draft,
            # allowing a later intentional revert to identical content without
            # violating dashboard/draft_hash uniqueness.
            approval_key = hashlib.sha256(json.dumps([str(record.id), request.model_dump(mode="json")],
                                                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if request.decision == "approve":
                if dashboard is None:
                    dashboard = Dashboard(owner_user_id=user_id, title=spec.title)
                    database.add(dashboard)
                    database.flush()
                    version_number = 1
                version = DashboardVersion(
                    dashboard_id=dashboard.id, version_number=version_number,
                    base_version_number=request.base_version_number, draft_hash=approval_key, approval_key=approval_key,
                    spec=spec.model_dump(mode="json"), schema_version=spec.schema_version,
                    catalog_version=spec.catalog_version, approved_by_user_id=user_id,
                )
                database.add(version)
                database.flush()
                dashboard.current_version_id = version.id
                dashboard.title, dashboard.updated_at = spec.title, datetime.now(UTC)
                record.dashboard_id = dashboard.id
            result = ApprovalResponse(
                decision=request.decision, conversation_id=record.id, draft_revision=record.draft_revision,
                draft_hash=record.draft_hash,
                dashboard_id=dashboard.id if request.decision == "approve" else None,
                version_number=version_number if request.decision == "approve" else None,
            )
            payload = {"request": request.model_dump(mode="json"), "response": result.model_dump(mode="json"),
                       "run_id": str(preview.run_id), "scope": user.scope_profile}
            database.add(AgentCheckpoint(conversation_id=record.id, thread_id=record.thread_id,
                                         checkpoint_namespace="approval", checkpoint_id=identifier, payload=payload))
            record.status, record.updated_at = "completed", datetime.now(UTC)
            message = "Dashboard đã được phát hành." if request.decision == "approve" else "Đã từ chối bản nháp; không phát hành dashboard."
            database.add(ConversationMessage(conversation_id=record.id, role="assistant", content=message))
            preview.message, preview.status = message, "completed"
            preview.results, preview.sql, preview.draft = {}, {}, None
            database.add(AgentCheckpoint(conversation_id=record.id, thread_id=record.thread_id,
                checkpoint_namespace="preview", checkpoint_id="approval:" + approval_key,
                payload=preview.model_dump(mode="json")))
            database.add(AuditEvent(actor_user_id=user_id, action="draft_approved" if request.decision == "approve" else "draft_rejected",
                target_type="conversation", target_id=str(record.id), details={
                    "draft_revision": record.draft_revision, "content_hash": request.draft_hash,
                    "approval_key": approval_key, "dashboard_id": str(result.dashboard_id) if result.dashboard_id else None,
                    "version_number": result.version_number,
                }))
            return payload, record.thread_id

    async def decide(self, user_id: UUID, conversation_id: UUID, request: ApprovalRequest) -> ApprovalResponse:
        payload, thread_id = await asyncio.to_thread(self._commit, user_id, conversation_id, request)
        result = ApprovalResponse.model_validate(payload["response"])
        if result.checkpoint_synced:
            return result
        marker = {"approved": result.decision == "approve", "dashboard_id": str(result.dashboard_id) if result.dashboard_id else None,
                  "version_number": result.version_number}
        try:
            from langsmith import tracing_context
            graph = self.conversations.graph_for(user_id, conversation_id, thread_id)
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 12}
            with tracing_context(enabled=False):
                snapshot = await graph.aget_state(config)
                if snapshot.values.get("run_id") != payload["run_id"]:
                    raise ValueError("approval no longer matches graph run")
                if snapshot.next == ("await_approval",):
                    async with asyncio.timeout(self.conversations.settings.agent_run_timeout_seconds):
                        await graph.ainvoke(Command(resume=marker), config=config, durability="sync")
                snapshot = await graph.aget_state(config)
                if snapshot.next or snapshot.values.get("approval") != marker:
                    raise ValueError("approval graph did not finish with the committed decision")
            await asyncio.to_thread(self._acknowledge, user_id, conversation_id, request, thread_id)
            result.checkpoint_synced = True
        except Exception:
            # The publication/receipt already committed. A retry of the same
            # request only finishes this pure graph step; never publish twice.
            logger.warning("approval_checkpoint_pending", extra={"conversation_id": str(conversation_id)})
        return result

    def _acknowledge(self, user_id, conversation_id, request, thread_id):
        with Session(self.engine) as database, database.begin():
            self.conversations._builder(database, user_id)
            record = self.conversations._owned(database, conversation_id, user_id)
            receipt = database.scalar(select(AgentCheckpoint).where(
                AgentCheckpoint.conversation_id == record.id, AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_namespace == "approval",
                AgentCheckpoint.checkpoint_id == receipt_id(request.draft_revision, request.draft_hash),
            ))
            if receipt is None or receipt.payload["request"] != request.model_dump(mode="json"):
                raise HTTPException(409, "Approval receipt has changed")
            receipt.payload = {**receipt.payload, "response": {**receipt.payload["response"], "checkpoint_synced": True}}
