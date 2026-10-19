from copy import deepcopy
import json

from app.config import Settings
from scripts import evaluate_agent as evaluator


def test_evaluation_records_only_public_error_code():
    assert evaluator.public_error_code({"error": {"code": "invalid_plan", "message": "private prompt"}}) == "invalid_plan"
    assert evaluator.public_error_code({"error": "private prompt"}) is None
    assert evaluator.public_error_code({"message": "private prompt"}) is None


def test_grader_requires_correct_plan_and_independent_numbers(monkeypatch):
    case = next(case for case in json.loads(evaluator.CORPUS.read_text(encoding="utf-8"))["cases"] if case["id"] == "valid_005")
    plan = {"kpis": ["sales_value"], "dimensions": [], "filters": case["oracle"]["filters"],
            "date_range": {"start": "2025-01-01", "end_exclusive": "2026-01-01"}, "sort": [], "limit": None}
    response = {"outcome": "plan", "scope": "CA", "draft": {"queries": [{"id": "q", "plan": plan}],
        "widgets": [{"id": "w", "kpi": "sales_value", "chart": "card", "query_id": "q"}]},
        "results": {"q": {"status": "ok", "rows": [{"sales_value": "123.45"}]}}}
    monkeypatch.setattr(evaluator, "reference_rows", lambda *args: {"status": "ok", "rows": [{"sales_value": "123.45"}]})
    settings = Settings(_env_file=None, app_env="test")
    assert evaluator.assess(case, response, 200, 1, settings) == []
    incorrect = deepcopy(response)
    incorrect["results"]["q"]["rows"][0]["sales_value"] = "123.46"
    assert any(diff["field"] == "w.rows" for diff in evaluator.assess(case, incorrect, 200, 1, settings))
    incorrect = deepcopy(response)
    incorrect["draft"]["queries"][0]["plan"]["filters"] = []
    assert any(diff["field"] == "q.filters" for diff in evaluator.assess(case, incorrect, 200, 1, settings))


def test_grader_denial_rejects_exposed_draft_and_comparison_ignores_unordered_rows():
    case = next(case for case in json.loads(evaluator.CORPUS.read_text(encoding="utf-8"))["cases"] if case["id"] == "unsupported_005")
    config = Settings(_env_file=None, app_env="test")
    assert evaluator.assess(case, {"detail": {"code": "authorization_denied_viewer",
        "message": "Viewer không được tạo hoặc chỉnh sửa dashboard bằng Agent."}}, 403, 0, config) == []
    assert evaluator.assess(case, {"draft": {"secret": "data"}}, 403, 0, config)
    rows = [{"state": "CA", "sales_value": "1.00"}, {"state": "TX", "sales_value": "2.00"}]
    assert evaluator.compare_rows(list(reversed(rows)), rows, "sales_value", "state", False)
    assert not evaluator.compare_rows(list(reversed(rows)), rows, "sales_value", "state", True)


def test_execution_accuracy_checks_unrendered_query_and_every_metric(monkeypatch):
    monkeypatch.setattr(evaluator, "reference_rows", lambda *args: {"status": "ok", "rows": [
        {"state": "CA", "offer_count": 3, "accepted_offer_rate": "0.33333333333333333333"}]})
    response = {"scope": "CA", "draft": {"widgets": [], "queries": [{"id": "unrendered", "plan": {
        "kpis": ["offer_count", "accepted_offer_rate"], "dimensions": ["state"], "filters": [],
        "date_range": {"start": None, "end_exclusive": None}, "sort": [], "limit": None,
    }}]}, "results": {"unrendered": {"status": "ok", "rows": [
        {"state": "CA", "offer_count": 3, "accepted_offer_rate": "0.33333333333333333333"}]}}}
    settings = Settings(_env_file=None, app_env="test")
    correct = evaluator.verify_executed_queries(response, settings)
    assert correct["checked_queries"] == 1 and correct["all_correct"]
    response["results"]["unrendered"]["rows"][0]["accepted_offer_rate"] = "0"
    incorrect = evaluator.verify_executed_queries(response, settings)
    assert not incorrect["all_correct"]
    assert incorrect["differences"] == [{"query_id": "unrendered", "field": "accepted_offer_rate.rows"}]
