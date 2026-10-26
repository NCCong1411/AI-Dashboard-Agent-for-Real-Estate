"""Bound graph nodes: plan, validate, compile, execute and preview."""

import asyncio
import logging
from uuid import UUID
from langgraph.types import interrupt

from app.agent.llm import AdapterConfigurationError, Planner, create_planner
from app.analytics.compiler import compile_analysis_plan
from app.analytics.executor import QueryExecutor
from app.config import Settings
from app.schemas.agent import AgentError, AgentResponse, GraphApprovalDecision, PlannerDecision
from app.schemas.dashboard import DashboardSpec
from app.services.usage import UsageLimitError, UsageService
from app.services.edits import apply_edits, changes_between, query_fingerprint
from app.schemas.dashboard import QueryResult
from .state import AgentState
from .filter_values import normalize_planner_state_filters
from .intent_guard import (explain_unsupported_agency, guard_undefined_offer_rate,
                           include_missing_metric, normalize_missing_comparison_metric)


logger = logging.getLogger(__name__)


def failure(code: str, message: str, *, retryable: bool = False) -> dict:
    return {"response": AgentResponse(status="failed", outcome="error", message=message,
                                     error=AgentError(code=code, message=message, retryable=retryable)).model_dump(mode="json")}


class AgentNodes:
    def __init__(self, settings: Settings, usage: UsageService, executor: QueryExecutor, planner: Planner | None = None):
        self.settings, self.usage, self.executor, self.planner = settings, usage, executor, planner

    def context(self, state: AgentState) -> dict:
        # Do not construct context by merging a user dict into identity/state.
        return {"decision": None, "draft": None, "sql": {}, "results": {}, "response": None, "query_count": 0,
                "changes": None, "reused_query_ids": [], "approval": None}

    async def plan(self, state: AgentState) -> dict:
        try:
            current = DashboardSpec.model_validate(state["current_spec"]) if state.get("current_spec") else None
            planner = self.planner if self.planner is not None else create_planner(self.settings)
            call = await self.usage.generate(planner, state["prompt"], UUID(state["user_id"]), UUID(state["run_id"]), current,
                                             history=state.get("history"))
        except AdapterConfigurationError:
            return failure("provider_not_configured", "Provider chưa được cấu hình đầy đủ; chưa có call LLM nào được gửi.")
        except UsageLimitError as error:
            return failure(error.code, "Lượt chạy không còn ngân sách, token hoặc thời gian cho phép.")
        if call.decision is None:
            if call.error_code in {"provider_authentication_failed", "provider_permission_denied"}:
                return failure(call.error_code, "API key không hợp lệ hoặc không có quyền truy cập model. Kiểm tra cấu hình LLM trên backend; không gửi key trong hội thoại.")
            return failure(call.error_code or "provider_error", "Không thể tạo kế hoạch. Vui lòng thử lại.", retryable=call.retryable)
        return {"decision": call.decision.model_dump(mode="json")}

    def validate(self, state: AgentState) -> dict:
        try:
            decision = normalize_planner_state_filters(PlannerDecision.model_validate(state["decision"]))
            prompt = state.get("prompt")
            if prompt is not None:
                has_context = bool(state.get("current_spec") or state.get("history"))
                decision = guard_undefined_offer_rate(prompt, decision, has_context=has_context)
                decision = include_missing_metric(prompt, decision, has_context=has_context)
                decision = normalize_missing_comparison_metric(prompt, decision, has_context=has_context)
            if decision.outcome == "clarification":
                return {"response": AgentResponse(status="needs_clarification", outcome="clarification",
                    message=decision.message, reason_code=decision.reason_code, reason_codes=decision.reason_codes).model_dump(mode="json")}
            if decision.outcome == "unsupported":
                decision = explain_unsupported_agency(decision)
                return {"response": AgentResponse(status="failed", outcome="unsupported", message=decision.message,
                    reason_code=decision.reason_code, reason_codes=decision.reason_codes, error=AgentError(code="unsupported", message=decision.message)).model_dump(mode="json")}
            previous = DashboardSpec.model_validate(state["current_spec"]) if state.get("current_spec") else None
            if previous is not None:
                if decision.patch is None:
                    raise ValueError("existing drafts require bounded edit operations")
                spec = apply_edits(previous, decision.patch)
            else:
                if decision.dashboard is None:
                    raise ValueError("new drafts require a complete dashboard")
                spec = DashboardSpec.model_validate(decision.dashboard.model_dump(mode="python"))
            # Semantic validation/compilation must succeed for every query
            # before any query executes. No partial validation bypass.
            for query in spec.queries:
                compile_analysis_plan(query.plan)
        except (ValueError, TypeError, KeyError):
            return failure("invalid_plan", "Kế hoạch không hợp lệ với catalog hoặc định nghĩa dashboard.")
        return {"draft": spec.model_dump(mode="json"), "changes": changes_between(previous, spec).model_dump(mode="json") if previous else None}

    def compile(self, state: AgentState) -> dict:
        spec = DashboardSpec.model_validate(state["draft"])
        return {"sql": {query.id: compile_analysis_plan(query.plan).sql for query in spec.queries}}

    async def execute(self, state: AgentState) -> dict:
        spec = DashboardSpec.model_validate(state["draft"])
        results, reused, executed = {}, [], 0
        for query in spec.queries:
            # Executor re-compiles the structured plan and chooses a reader
            # from the server identity; the graph SQL strings are tracing only.
            cached = state.get("cached_results", {}).get(query_fingerprint(query.plan, state["scope"]))
            if cached is not None:
                result = QueryResult.model_validate(cached)
                reused.append(query.id)
            else:
                result = await asyncio.to_thread(self.executor.execute, query.plan, state["scope"])
                executed += 1
            results[query.id] = result.model_dump(mode="json")
        logger.info("agent_queries_completed", extra={"run_id": state["run_id"], "query_count": executed, "reused_count": len(reused)})
        return {"results": results, "query_count": executed, "reused_query_ids": reused}

    def preview(self, state: AgentState) -> dict:
        if all(result["status"] == "error" for result in state["results"].values()):
            return failure("query_failed", "Các truy vấn đều thất bại; chưa có bản xem trước để duyệt.", retryable=True)
        return {"response": AgentResponse(
            status="awaiting_approval", outcome="plan", message="Bản xem trước đã sẵn sàng; dashboard chưa được phát hành.",
            draft=DashboardSpec.model_validate(state["draft"]), results=state["results"], sql=state["sql"],
            changes=state.get("changes"), reused_query_ids=state.get("reused_query_ids", []),
        ).model_dump(mode="json")}

    def await_approval(self, state: AgentState) -> dict:
        # This node deliberately has no side effects before/after interrupt.
        # Publication is an idempotent app transaction, verified before resume.
        value = interrupt({"conversation_id": state["conversation_id"], "run_id": state["run_id"],
                           "message": "Bản nháp đang chờ duyệt."})
        decision = GraphApprovalDecision.model_validate(value)
        response = AgentResponse.model_validate(state["response"]).model_copy(update={
            "status": "completed", "draft": None, "results": {}, "sql": {},
            "message": "Dashboard đã được phát hành." if decision.approved else "Đã từ chối bản nháp; không phát hành dashboard.",
        })
        return {"approval": decision.model_dump(mode="json"), "response": response.model_dump(mode="json"),
                "results": {}, "cached_results": {}}
