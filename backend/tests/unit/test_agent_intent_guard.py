"""The final agent validation keeps undefined rates from reaching SQL execution."""

import pytest

from app.agent.intent_guard import (explain_unsupported_agency, guard_undefined_offer_rate,
                                    include_missing_metric, normalize_missing_comparison_metric)
from app.agent.nodes import AgentNodes
from app.schemas.agent import PlannerDecision
from scripts.seed_dashboard import demo_spec


@pytest.mark.parametrize("prompt", [
    "Cho tôi tỷ lệ offer năm ngoái.",
    "Tạo dashboard phần trăm đề nghị theo tháng.",
    "Show offer rate by year.",
])
def test_unspecified_offer_rate_needs_clarification(prompt):
    planned = PlannerDecision(outcome="plan", message="Draft", dashboard=demo_spec())
    response = AgentNodes(None, None, None).validate({
        "prompt": prompt, "decision": planned.model_dump(mode="json"),
    })["response"]
    assert response["outcome"] == "clarification"
    assert response["reason_code"] == "ambiguous_rate_definition"
    assert response["draft"] is None
    assert "chấp nhận" in response["message"]


@pytest.mark.parametrize("prompt", [
    "Cho tôi tỷ lệ đề nghị được chấp nhận trên tất cả đề nghị năm ngoái.",
    "Show accepted offer rate by month.",
    "Cho tôi số offer năm ngoái.",
])
def test_explicit_or_non_rate_offer_request_is_preserved(prompt):
    planned = PlannerDecision(outcome="plan", message="Draft", dashboard=demo_spec())
    assert guard_undefined_offer_rate(prompt, planned) is planned


def test_conversation_context_can_define_the_rate():
    planned = PlannerDecision(outcome="plan", message="Draft", dashboard=demo_spec())
    assert guard_undefined_offer_rate("Tỷ lệ offer năm ngoái", planned, has_context=True) is planned


def test_dimension_only_chart_asks_for_metric_as_well():
    decision = PlannerDecision(outcome="clarification", message="Khu vực là bang hay thành phố?",
                               reason_code="ambiguous_dimension", reason_codes=["ambiguous_dimension"])
    updated = include_missing_metric("Vẽ biểu đồ theo khu vực.", decision)
    assert set(updated.reason_codes) == {"ambiguous_dimension", "missing_metric"}
    assert "KPI" in updated.message
    assert include_missing_metric("Vẽ giá trị giao dịch theo khu vực.", decision) is decision


def test_comparison_without_measure_uses_missing_metric_reason():
    decision = PlannerDecision(outcome="clarification", message="Bạn muốn so sánh KPI nào, theo chiều nào và khi nào?",
                               reason_code="ambiguous_metric",
                               reason_codes=["ambiguous_metric", "missing_comparison_dimension", "missing_time_range"])
    response = AgentNodes(None, None, None).validate({
        "prompt": "So sánh kết quả tốt nhất giúp tôi.", "decision": decision.model_dump(mode="json"),
    })["response"]
    assert response["reason_code"] == "missing_metric"
    assert set(response["reason_codes"]) == {"missing_metric", "missing_comparison_dimension", "missing_time_range"}
    assert response["outcome"] == "clarification" and response["draft"] is None
    assert normalize_missing_comparison_metric("So sánh doanh số theo bang", decision) is decision
    assert normalize_missing_comparison_metric("So sánh kết quả tốt nhất", decision, has_context=True) is decision


def test_agency_refusal_explains_both_catalog_and_join_limitations():
    decision = PlannerDecision(outcome="unsupported", message="Không thể xếp hạng hoa hồng theo đại lý.",
                               reason_code="unsupported_agency_and_commission")
    response = AgentNodes(None, None, None).validate({"decision": decision.model_dump(mode="json")})["response"]
    assert response["outcome"] == "unsupported"
    assert response["reason_code"] == decision.reason_code
    assert response["draft"] is None
    assert response["results"] == {}
    assert "không có công thức hoa hồng" in response["message"]
    assert "lịch sử môi giới–đại lý gây thiếu khớp và fan-out" in response["message"].casefold()
    assert response["error"]["message"] == response["message"]


def test_agency_explanation_does_not_change_other_decisions_or_repeat_itself():
    other = PlannerDecision(outcome="unsupported", message="Không hỗ trợ dự báo.",
                            reason_code="unsupported_forecasting")
    assert explain_unsupported_agency(other) is other
    agency = PlannerDecision(outcome="unsupported", message="Catalog không có công thức hoa hồng. "
                             "Lịch sử môi giới–đại lý gây thiếu khớp và fan-out.",
                             reason_code="unsupported_agency_and_commission")
    assert explain_unsupported_agency(agency) is agency


def test_long_agency_refusal_keeps_both_reasons_within_message_limit():
    decision = PlannerDecision(outcome="unsupported", message="Catalog không có công thức hoa hồng. " + "X" * 1900,
                               reason_code="unsupported_agency_and_commission")
    updated = explain_unsupported_agency(decision)
    assert len(updated.message) <= 2000
    assert "không có công thức hoa hồng" in updated.message
    assert "lịch sử môi giới–đại lý gây thiếu khớp và fan-out" in updated.message.casefold()
