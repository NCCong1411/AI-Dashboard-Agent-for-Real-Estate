import json

from app.agent.filter_values import US_STATE_CODES, canonical_state, normalize_planner_state_filters
from app.agent.nodes import AgentNodes
from app.agent.prompts import SYSTEM_PROMPT, planner_input
from app.schemas.agent import PlannerDecision
from app.schemas.dashboard import DashboardSpec
from app.services.edits import apply_edits
from scripts.seed_dashboard import demo_spec


def test_state_metadata_covers_fifty_states_and_dc():
    assert len(US_STATE_CODES) == 51
    assert len(set(US_STATE_CODES.values())) == 51
    assert US_STATE_CODES["District of Columbia"] == "DC"
    context = json.loads(planner_input("Transactions in California"))
    assert context["state_filter_values"]["name_to_code"] == US_STATE_CODES


def test_planner_sort_guidance_keeps_every_temporal_chart_chronological():
    assert "day/month/quarter/year" in SYSTEM_PROMPT
    assert "sort chiều thời gian tăng dần" in SYSTEM_PROMPT
    assert "sort KPI giảm dần" in SYSTEM_PROMPT
    assert "Card/pie/donut không cần sort mặc định" in SYSTEM_PROMPT
    assert 'Một năm cố định là filter, không là dimension' in SYSTEM_PROMPT
    assert 'theo từng năm dùng bar' in SYSTEM_PROMPT
    assert '"tỷ lệ đề nghị được chấp nhận" đã nêu rõ loại tỷ lệ' in SYSTEM_PROMPT
    assert "chart thời gian trong dashboard tổng hợp" in SYSTEM_PROMPT
    assert "Không thêm sort mặc định nếu người dùng không yêu cầu" not in SYSTEM_PROMPT


def test_ambiguous_comparison_guidance_checks_all_missing_fields():
    assert "kiểm tra riêng KPI, chiều so sánh và khoảng" in SYSTEM_PROMPT
    assert "hỏi đủ mọi phần còn thiếu" in SYSTEM_PROMPT
    assert "Không mặc định toàn bộ thời gian" in SYSTEM_PROMPT


def test_overview_guidance_distinguishes_title_from_explicit_widget_limit():
    assert "Dashboard sales theo month/state/property_type" in SYSTEM_PROMPT
    assert "sales_value/transaction_count/average_sale_value" in SYSTEM_PROMPT
    assert '"giá trị giao dịch"' in SYSTEM_PROMPT
    assert 'Chỉ bỏ card khi yêu cầu rõ "chỉ"' in SYSTEM_PROMPT
    assert '"Bảng" dùng table; so sánh theo nhóm dùng bar' in SYSTEM_PROMPT
    assert '"năm ngoái" là\nnăm dương lịch trước, không hỏi lại kỳ' in SYSTEM_PROMPT


def test_exact_aliases_and_codes_only():
    assert canonical_state("cAlIfOrNiA") == "CA"
    assert canonical_state("tx") == "TX"
    assert canonical_state("NEW YORK") == "NY"
    assert canonical_state("District of Columbia") == "DC"
    for value in ("California ", "North", "CA' OR 1=1 --", "../../Texas", "california; DROP TABLE x", "Unknown"):
        assert canonical_state(value) == value


def test_new_dashboard_normalizes_state_eq_and_in_without_touching_other_fields():
    source = demo_spec().model_dump(mode="python")
    source["queries"][0]["plan"]["filters"] = [
        {"field": "state", "op": "in", "value": ["Texas", "ca", "Unknown", "CA' OR 1=1 --"]},
        {"field": "city", "op": "eq", "value": "Texas"},
    ]
    decision = PlannerDecision(outcome="plan", message="draft", dashboard=source)
    normalized = normalize_planner_state_filters(decision)
    filters = normalized.dashboard.queries[0].plan.filters
    assert filters[0].value == ["TX", "CA", "Unknown", "CA' OR 1=1 --"]
    assert filters[1].value == "Texas"
    assert decision.dashboard.queries[0].plan.filters[0].value[0] == "Texas"
    assert normalized.dashboard.queries[0].id == decision.dashboard.queries[0].id
    assert normalized.dashboard.widgets == decision.dashboard.widgets
    assert normalized.dashboard.layout == decision.dashboard.layout


def test_edit_normalizes_only_new_set_filter_and_preserves_saved_spec():
    saved = demo_spec().model_dump(mode="python")
    saved["queries"][0]["plan"]["filters"] = [{"field": "state", "op": "eq", "value": "California"}]
    persisted = DashboardSpec.model_validate(saved)
    assert persisted.queries[0].plan.filters[0].value == "California"
    patch = PlannerDecision(outcome="plan", message="edit", patch=[
        {"op": "set_filter", "filter": {"field": "state", "op": "eq", "value": "Texas"}},
    ])
    normalized = normalize_planner_state_filters(patch)
    assert normalized.patch[0].filter.value == "TX"
    assert patch.patch[0].filter.value == "Texas"
    assert persisted.queries[0].plan.filters[0].value == "California"
    edited = apply_edits(persisted, normalized.patch)
    assert all(query.plan.filters[0].value == "TX" for query in edited.queries)
    assert edited.widgets == persisted.widgets and edited.layout == persisted.layout


def test_non_plan_decision_is_unchanged():
    decision = PlannerDecision(outcome="clarification", message="California?", reason_code="ambiguous_dimension")
    assert normalize_planner_state_filters(decision) is decision


def test_graph_validate_normalizes_new_planner_decision_before_draft():
    source = demo_spec().model_dump(mode="python")
    source["queries"][0]["plan"]["filters"] = [
        {"field": "state", "op": "eq", "value": "California"},
    ]
    decision = PlannerDecision(outcome="plan", message="draft", dashboard=source)
    result = AgentNodes(None, None, None).validate({
        "decision": decision.model_dump(mode="json"), "current_spec": None,
    })
    assert "response" not in result
    assert result["draft"]["queries"][0]["plan"]["filters"] == [
        {"field": "state", "op": "eq", "value": "CA"},
    ]
    assert decision.dashboard.queries[0].plan.filters[0].value == "California"
