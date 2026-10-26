"""SDK/HTTP contract tests with synthetic responses, never live paid calls."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
from uuid import uuid4

import httpx2
import pytest
from openai import AsyncOpenAI as RealAsyncOpenAI
from pydantic import ValidationError

from app.agent import llm as module
from app.agent.llm import AdapterConfigurationError, OpenAIPlanner, create_planner, strict_output_schema
from app.agent.mock import MockPlanner
from app.agent.nodes import AgentNodes
from app.agent.prompts import SYSTEM_PROMPT, planner_input
from app.config import Settings
from app.schemas.agent import PlannerDecision
from scripts.seed_dashboard import demo_spec


def settings(**updates):
    return Settings(_env_file=None, app_env="test", llm_mode="live", llm_provider="openai",
                    llm_endpoint="https://provider.example/v1", llm_model="test-model",
                    llm_api_key="unit-test-key-never-live", llm_input_price_per_million="2",
                    llm_output_price_per_million="10", llm_pricing_source="unit-test-pricing",
                    llm_pricing_verified_on="2026-10-15", **updates)


def response_body(output=None, *, status="completed", usage=True):
    return {
        "id": "resp_unit_test", "object": "response", "created_at": 1, "model": "test-model", "status": status,
        "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": output or PlannerDecision(
                        outcome="plan", message="Test", dashboard=demo_spec()).model_dump_json(), "annotations": []}]}],
        "usage": {"input_tokens": 50, "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 150} if usage else None,
    }


def install_transport(monkeypatch, handler):
    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        return RealAsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(module, "AsyncOpenAI", client)


def test_sdk_request_schema_catalog_and_usage(monkeypatch):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body())
    install_transport(monkeypatch, handle)
    call = asyncio.run(OpenAIPlanner(settings()).generate("Tổng quan năm 2025"))
    assert call.decision.outcome == "plan" and call.error_code is None
    assert (call.input_tokens, call.output_tokens) == (50, 100)  # includes reasoning, not 140
    assert len(calls) == 1
    request = calls[0]
    assert request["store"] is False and request["max_output_tokens"] == 6144
    assert request["reasoning"] == {"effort": "low"}
    assert request["service_tier"] == "default"  # explicit Standard pricing, not project auto tier
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["type"] == "json_schema"
    assert "tools" not in request and "temperature" not in request
    context = json.loads(request["input"])
    assert context["catalog"][0]["id"] == "sales_value"
    assert "scope" not in context and "rows" not in context
    assert request["instructions"] == SYSTEM_PROMPT


def test_edit_uses_smaller_output_reservation(monkeypatch):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body())
    install_transport(monkeypatch, handle)
    asyncio.run(OpenAIPlanner(settings()).generate("Đổi chart", current=demo_spec()))
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 4096


@pytest.mark.parametrize("status,code,retryable", [(429, "provider_rate_limit", True), (500, "provider_error", True),
    (401, "provider_authentication_failed", False), (403, "provider_permission_denied", False)])
def test_http_errors_are_sanitized_and_not_automatically_retried(monkeypatch, caplog, status, code, retryable):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx2.Response(status, json={"error": {"message": "sensitive raw provider body"}})
    install_transport(monkeypatch, handle)
    call = asyncio.run(OpenAIPlanner(settings()).generate("test"))
    assert call.error_code == code and call.retryable is retryable
    assert len(requests) == 1 and call.decision is None
    assert "sensitive raw provider body" not in repr(call) + caplog.text


def test_async_wall_clock_timeout_is_enforced(monkeypatch):
    async def handle(request):
        await asyncio.sleep(2)
        return httpx2.Response(200, json=response_body())
    install_transport(monkeypatch, handle)
    call = asyncio.run(OpenAIPlanner(settings()).generate("test", timeout_seconds=0.01))
    assert call.error_code == "provider_timeout" and call.input_tokens is None


@pytest.mark.parametrize("output", ["not JSON", '{"outcome":"plan","message":"bad","dashboard":null}',
    '{"outcome":"unsupported","message":"bad","reason_code":"unknown","sql":"DROP TABLE x"}'])
def test_invalid_structured_output_preserves_usage_without_a_plan(monkeypatch, output):
    install_transport(monkeypatch, lambda request: httpx2.Response(200, json=response_body(output)))
    call = asyncio.run(OpenAIPlanner(settings()).generate("test"))
    assert call.error_code == "invalid_output" and call.decision is None
    assert call.output_tokens == 100


def test_refusal_and_incomplete_are_distinct(monkeypatch):
    document = response_body(status="incomplete")
    install_transport(monkeypatch, lambda request: httpx2.Response(200, json=document))
    assert asyncio.run(OpenAIPlanner(settings()).generate("test")).error_code == "incomplete_output"
    document["status"] = "completed"
    document["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
    assert asyncio.run(OpenAIPlanner(settings()).generate("test")).error_code == "provider_refusal"


def test_missing_usage_is_not_invented_as_zero(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx2.Response(200, json=response_body(usage=False)))
    call = asyncio.run(OpenAIPlanner(settings()).generate("test"))
    assert call.decision is not None
    assert call.input_tokens is call.output_tokens is None


@pytest.mark.parametrize("field", ["llm_provider", "llm_endpoint", "llm_model", "llm_api_key",
    "llm_input_price_per_million", "llm_output_price_per_million", "llm_pricing_source", "llm_pricing_verified_on"])
def test_live_requires_complete_configuration_without_exposing_secrets(field):
    config = settings()
    setattr(config, field, None)
    with pytest.raises(AdapterConfigurationError) as error:
        create_planner(config)
    assert "unit-test-key" not in str(error.value)


def test_production_live_requires_explicit_shared_spending_cap():
    config = settings()
    config.app_env = "production"
    with pytest.raises(AdapterConfigurationError, match="global daily budget"):
        create_planner(config)
    config.llm_global_budget_per_day_usd = Decimal("0.10")
    assert isinstance(create_planner(config), OpenAIPlanner)


def test_mock_has_no_key_or_network_and_outcomes_are_deterministic(monkeypatch):
    monkeypatch.setattr(module, "AsyncOpenAI", lambda **kwargs: pytest.fail("mock attempted network"))
    config = Settings(_env_file=None, app_env="test")
    config.llm_mode = "mock"
    config.llm_api_key = None
    planner = create_planner(config)
    assert isinstance(planner, MockPlanner)
    for prompt, expected in [("Tổng quan giao dịch năm 2025", "plan"), ("Doanh thu năm nay", "clarification"),
                             ("Tính lợi nhuận", "unsupported"), ("DROP TABLE transactions", "unsupported")]:
        first = asyncio.run(planner.generate(prompt))
        second = asyncio.run(planner.generate(prompt))
        assert first == second and first.decision.outcome == expected
        assert first.input_tokens == first.output_tokens == 0


def test_strict_schema_and_local_semantic_exclusivity():
    def check(value):
        if isinstance(value, dict):
            assert not {"oneOf", "discriminator", "default"} & value.keys()
            assert not isinstance(value.get("title"), str)
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value.get("properties", {}))
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(strict_output_schema())
    assert "title" in strict_output_schema()["$defs"]["DashboardSpec"]["properties"]
    assert "title" in strict_output_schema(editing=True)["$defs"]["RenameWidget"]["properties"]
    with pytest.raises(ValidationError):
        PlannerDecision(outcome="unsupported", message="no", reason_code="x", dashboard=demo_spec())
    with pytest.raises(ValidationError):
        PlannerDecision(outcome="clarification", message="what?")


def test_prompt_uses_request_clock_and_only_catalog_metadata():
    context = json.loads(planner_input("yesterday", now=datetime(2026, 10, 15, 18, tzinfo=UTC)))
    assert context["current_date"] == "2026-10-16" and context["timezone"] == "Asia/Ho_Chi_Minh"
    assert all("sql_expression" not in kpi for kpi in context["catalog"])
    assert {item["dataset"] for item in context["catalog"]} == {"sales", "offers", "repairs"}
    assert all(item["definition"] for item in context["catalog"])
    assert context["datasets"]["offers"]["date_field"] == "offer_date"
    assert "agent" not in context["datasets"]["offers"]["dimensions"]
    offer = next(item for item in context["catalog"] if item["id"] == "offer_count")
    assert "đề nghị mua bất động sản" in offer["definition"]
    with pytest.raises(ValueError, match="timezone-aware"):
        planner_input("test", now=datetime(2026, 1, 1))


def test_edit_context_is_compact_but_keeps_query_meaning_and_widget_ids():
    current = demo_spec()
    before = current.model_dump(mode="json")
    compact = json.loads(planner_input("Đổi biểu đồ", current))["current_dashboard"]
    assert "layout" not in compact and "schema_version" not in compact
    assert compact["widgets"] == before["widgets"]
    for original, reduced in zip(before["queries"], compact["queries"], strict=True):
        assert reduced["id"] == original["id"]
        assert reduced["plan"] == {key: value for key, value in original["plan"].items()
                                   if key not in {"schema_version", "catalog_version"}}
    assert len(json.dumps(compact)) < len(json.dumps(before)) * 0.8
    assert current.model_dump(mode="json") == before


@pytest.mark.parametrize("code", ["provider_authentication_failed", "provider_permission_denied"])
def test_provider_credentials_failure_explains_configuration_without_a_draft(code):
    class UsageStub:
        async def generate(self, *args, **kwargs):
            return module.AdapterCall(None, None, None, code)
    nodes = AgentNodes(Settings(_env_file=None, app_env="test"), UsageStub(), None, planner=MockPlanner())
    result = asyncio.run(nodes.plan({"prompt": "Tạo dashboard", "user_id": str(uuid4()), "run_id": str(uuid4())}))
    response = result["response"]
    assert response["error"]["code"] == code and response["error"]["retryable"] is False
    assert "API key" in response["message"] and response["draft"] is None
    assert response["results"] == {} and response["sql"] == {}
