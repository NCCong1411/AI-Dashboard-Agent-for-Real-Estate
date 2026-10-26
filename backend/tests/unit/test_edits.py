from datetime import UTC, datetime
import asyncio
import json

from pydantic import TypeAdapter, ValidationError
import pytest

from app.agent.llm import strict_output_schema
from app.agent.mock import MockPlanner
from app.agent.prompts import planner_input
from app.schemas.edits import EditOperation
from app.services.edits import apply_edits, changes_between, query_fingerprint
from app.services.usage import input_token_bound
from scripts.seed_dashboard import demo_spec


def operations(*values):
    return TypeAdapter(list[EditOperation]).validate_python(list(values))


def test_chart_edit_preserves_every_unrelated_widget_query_and_layout():
    original = demo_spec()
    updated = apply_edits(original, operations({"op": "set_chart", "widget_id": "type-sales", "chart": "horizontal_bar"}))
    assert updated.queries == original.queries and updated.layout == original.layout
    assert updated.widgets[:-1] == original.widgets[:-1]
    assert updated.widgets[-1].id == original.widgets[-1].id
    assert updated.widgets[-1].chart == "horizontal_bar"
    changes = changes_between(original, updated)
    assert changes.presentation_only and changes.changed_queries == []
    assert [item.widget_id for item in changes.widgets] == ["type-sales"]


def test_invalid_patch_is_atomic_and_cannot_execute_code_or_remove_everything():
    original = demo_spec()
    with pytest.raises(ValueError):
        apply_edits(original, operations({"op": "set_filter", "filter": {"field": "state", "op": "eq", "value": "CA"}},
                                          {"op": "remove_widget", "widget_id": "foreign"}))
    assert original == demo_spec()
    with pytest.raises(ValidationError):
        operations({"op": "execute_sql", "sql": "SELECT * FROM clients"})
    with pytest.raises(ValueError):
        apply_edits(original, operations(*({"op": "remove_widget", "widget_id": widget.id} for widget in original.widgets)))


def test_filter_date_reset_and_cache_key_include_scope_without_mutating_source():
    original = demo_spec()
    updated = apply_edits(original, operations(
        {"op": "set_filter", "filter": {"field": "state", "op": "eq", "value": "CA"}},
        {"op": "set_dates", "date_range": {"start": "2025-01-01", "end_exclusive": "2026-01-01"}},
    ))
    assert not original.queries[0].plan.filters and original.queries[0].plan.date_range.start is None
    assert all(query.plan.filters[0].value == "CA" for query in updated.queries)
    key = query_fingerprint(updated.queries[0].plan, "CA")
    assert key != query_fingerprint(updated.queries[0].plan, "TX")
    reset = apply_edits(updated, operations({"op": "clear_filters"}, {"op": "set_dates", "date_range": {}}))
    assert reset == original


def test_edit_schema_is_small_and_history_is_bounded_by_the_prompt_builder():
    creation, editing = strict_output_schema(), strict_output_schema(editing=True)
    assert creation["properties"]["patch"] == {"type": "null"}
    assert editing["properties"]["dashboard"] == {"type": "null"}
    assert "DashboardSpec" not in editing.get("$defs", {})
    history = [{"role": "user", "content": "x" * 10000} for _ in range(100)]
    now = datetime(2026, 10, 15, tzinfo=UTC)
    context = json.loads(planner_input("Đổi chart", demo_spec(), now=now, history=history))
    assert sum(len(item["content"]) for item in context["recent_messages"]) <= 2000
    assert len(context["recent_messages"]) <= 8
    assert input_token_bound("Đổi chart", demo_spec(), now, history) + 4096 <= 24000
    vietnamese = [{"role": "user", "content": "Thêm giá trị giao dịch theo bang. " * 1000}] * 100
    context = json.loads(planner_input("Đổi chart", demo_spec(), now=now, history=vietnamese))
    assert sum(len(item["content"].encode("utf-8")) for item in context["recent_messages"]) <= 1700
    assert input_token_bound("Đổi chart", demo_spec(), now, vietnamese) + 4096 <= 24000


def test_sales_alias_needs_a_unique_chart_and_does_not_replace_other_widgets():
    original = demo_spec()
    ambiguous = asyncio.run(MockPlanner().generate("Đổi biểu đồ doanh số thành cột ngang", original)).decision
    assert ambiguous.outcome == "clarification"
    single = apply_edits(original, operations(
        {"op": "remove_widget", "widget_id": "monthly-sales"},
        {"op": "remove_widget", "widget_id": "type-sales"},
    ))
    decision = asyncio.run(MockPlanner().generate("Đổi biểu đồ doanh số thành cột ngang", single)).decision
    updated = apply_edits(single, decision.patch)
    assert updated.queries == single.queries and updated.layout == single.layout
    assert [item for item in updated.widgets if item.id != "state-sales"] == [
        item for item in single.widgets if item.id != "state-sales"]
    assert next(item for item in updated.widgets if item.id == "state-sales").chart == "horizontal_bar"


def test_missing_website_source_does_not_invent_metrics_or_change_current_draft():
    original = demo_spec()
    result = asyncio.run(MockPlanner().generate("Thêm số lượt khách xem tin bất động sản trên website", original)).decision
    assert result.outcome == "unsupported" and result.dashboard is None and result.patch is None
    assert "nguồn dữ liệu lượt xem" in result.message
    assert original == demo_spec()
