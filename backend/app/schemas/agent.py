"""Agent response contracts shared by mock and future live adapters."""

from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dashboard import DashboardSpec, QueryResult
from .edits import DraftChanges, EditOperation


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool = False


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["needs_clarification", "awaiting_approval", "completed", "failed"]
    message: str
    draft: DashboardSpec | None = None
    error: AgentError | None = None
    outcome: Literal["plan", "clarification", "unsupported", "error"] | None = None
    reason_code: str | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=5)
    conversation_id: UUID | None = None
    run_id: UUID | None = None
    draft_revision: int = Field(default=0, ge=0)
    draft_hash: str | None = None
    base_version_number: int | None = None
    scope: Literal["ALL", "CA", "TX"] | None = None
    mode: Literal["mock", "live"] = "mock"
    results: dict[str, QueryResult] = Field(default_factory=dict)
    sql: dict[str, str] = Field(default_factory=dict)
    changes: DraftChanges | None = None
    reused_query_ids: list[str] = Field(default_factory=list)


class PlannerDecision(BaseModel):
    """One provider decision, with semantic exclusivity beyond JSON schema."""

    model_config = ConfigDict(extra="forbid")
    outcome: Literal["plan", "clarification", "unsupported"]
    message: str = Field(min_length=1, max_length=2000)
    dashboard: DashboardSpec | None = None
    patch: list[EditOperation] | None = Field(default=None, min_length=1, max_length=8)
    reason_code: str | None = Field(default=None, max_length=100)
    reason_codes: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def outcome_matches_dashboard(self) -> "PlannerDecision":
        supplied = int(self.dashboard is not None) + int(self.patch is not None)
        if supplied != (1 if self.outcome == "plan" else 0):
            raise ValueError("plan requires exactly one dashboard or patch; other outcomes require neither")
        if self.outcome != "plan" and not self.reason_code:
            raise ValueError("clarification/unsupported require a reason code")
        return self


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dashboard_id: UUID | None = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=12000)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def nonblank(self) -> "MessageRequest":
        if not self.message.strip():
            raise ValueError("message must not be blank")
        return self


class ConversationMessageView(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    draft_revision: int = Field(ge=1, strict=True)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_version_number: int | None = Field(ge=1, strict=True)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    conversation_id: UUID
    draft_revision: int
    draft_hash: str
    dashboard_id: UUID | None
    version_number: int | None
    checkpoint_synced: bool = False


class ConversationView(BaseModel):
    id: UUID
    dashboard_id: UUID | None
    status: Literal["running", "needs_clarification", "awaiting_approval", "completed", "failed"]
    draft_revision: int
    draft_hash: str | None
    base_version_number: int | None
    draft: DashboardSpec | None
    messages: list[ConversationMessageView]
    response: AgentResponse | None
    preview: AgentResponse | None = None
    approval: ApprovalResponse | None = None
    mode: Literal["mock", "live"]


class GraphApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    dashboard_id: UUID | None = None
    version_number: int | None = None
