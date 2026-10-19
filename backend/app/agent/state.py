"""Internal graph state; identity/scope are populated by the server only."""

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    run_id: str
    thread_id: str
    scope: Literal["ALL", "CA", "TX"]
    prompt: str
    current_spec: dict[str, Any] | None
    decision: dict[str, Any] | None
    draft: dict[str, Any] | None
    sql: dict[str, str]
    results: dict[str, dict[str, Any]]
    query_count: int
    response: dict[str, Any] | None
    history: list[dict[str, str]]
    cached_results: dict[str, dict[str, Any]]
    changes: dict[str, Any] | None
    reused_query_ids: list[str]
    approval: dict[str, Any] | None
