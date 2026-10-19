"""Live PostgreSQL reservations, races, retries and daily boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import uuid

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agent.llm import AdapterCall
from app.agent.mock import MockPlanner
from app.config import Settings
from app.db.models import AuditEvent, UsageCall, UsageRecord, User
from app.services.usage import UsageLimitError, UsageService


@pytest.fixture
def context():
    value = os.getenv("AUTH_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("requires AUTH_TEST_DATABASE_URL for real PostgreSQL budget locks")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("budget integration requires a separate test database")
    engine = create_engine(value, hide_parameters=True)
    with Session(engine) as database, database.begin():
        user = User(username="budget_" + uuid.uuid4().hex, password_hash="not-a-login-hash", role="builder", scope_profile="ALL")
        database.add(user)
        database.flush()
        user_id = user.id
    config = Settings(_env_file=None, app_env="test", llm_mode="live", llm_provider="openai",
                      llm_endpoint="https://provider.example/v1", llm_model="test-model", llm_api_key="not-live",
                      llm_input_price_per_million="1", llm_output_price_per_million="1",
                      llm_pricing_source="test", llm_pricing_verified_on="2026-10-15")
    service = UsageService(engine, config)
    yield service, user_id
    with Session(engine) as database, database.begin():
        database.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user_id))
        database.execute(delete(UsageRecord).where(UsageRecord.user_id == user_id))
        database.execute(delete(User).where(User.id == user_id))
    engine.dispose()


def test_reserve_reconcile_is_idempotent_and_unknown_usage_stays_charged(context):
    service, user = context
    run_id = uuid.uuid4()
    permit = service.reserve(user, run_id, 1000, 1000)
    assert service.engine.pool.checkedout() == 0
    result = AdapterCall(None, 50, 100, "invalid_output")
    assert service.reconcile(user, permit, result)
    assert service.reconcile(user, permit, result)
    with Session(service.engine) as database:
        run = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id))
        assert (run.llm_calls, run.input_tokens, run.output_tokens) == (1, 50, 100)
        assert run.estimated_cost_usd == Decimal("0.000150")
    permit = service.reserve(user, run_id, 1000, 1000)
    assert service.reconcile(user, permit, AdapterCall(None, None, None, "provider_timeout"))
    with Session(service.engine) as database:
        run = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id))
        call = database.get(UsageCall, permit.id)
        assert run.estimated_cost_usd == Decimal("0.002150")
        assert call.status == "unknown"


def test_parallel_conversations_cannot_race_daily_budget(context):
    service, user = context
    service.settings.llm_budget_per_day_usd = Decimal("0.0002")
    def attempt(_):
        try:
            return service.reserve(user, uuid.uuid4(), 100, 0)
        except UsageLimitError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(not isinstance(result, str) for result in results) == 2
    assert results.count("daily_budget") == 6


def test_calls_tokens_budget_and_deadline_are_checked_before_reservation(context):
    service, user = context
    run = uuid.uuid4()
    for _ in range(3):
        service.reserve(user, run, 0, 0)
    with pytest.raises(UsageLimitError, match="call_limit"):
        service.reserve(user, run, 0, 0)
    with pytest.raises(UsageLimitError, match="token_limit"):
        service.reserve(user, uuid.uuid4(), 24001, 0)
    service.settings.llm_budget_per_run_usd = Decimal("0.0001")
    with pytest.raises(UsageLimitError, match="run_budget"):
        service.reserve(user, uuid.uuid4(), 1000, 0)
    clock = [datetime(2026, 10, 15, tzinfo=UTC)]
    service.clock = lambda: clock[0]
    run = uuid.uuid4()
    service.reserve(user, run, 0, 0)
    clock[0] += timedelta(seconds=60)
    with pytest.raises(UsageLimitError, match="run_timeout"):
        service.reserve(user, run, 0, 0)


def test_daily_limit_rolls_over_in_vietnam_timezone(context):
    service, user = context
    clock = [datetime(2026, 10, 15, 16, 59, 59, tzinfo=UTC)]
    service.clock = lambda: clock[0]
    service.settings.llm_budget_per_day_usd = Decimal("0.0001")
    first = service.reserve(user, uuid.uuid4(), 100, 0)
    with pytest.raises(UsageLimitError, match="daily_budget"):
        service.reserve(user, uuid.uuid4(), 100, 0)
    clock[0] += timedelta(seconds=2)
    second = service.reserve(user, uuid.uuid4(), 100, 0)
    with Session(service.engine) as database:
        assert str(database.get(UsageCall, first.id).usage_date) == "2026-10-15"
        assert str(database.get(UsageCall, second.id).usage_date) == "2026-10-16"


def test_one_structured_repair_and_all_retries_are_counted(context):
    service, user = context
    service.settings.llm_mode = "mock"
    class InvalidPlanner:
        calls = 0
        async def generate(self, *args, **kwargs):
            assert service.engine.pool.checkedout() == 0
            self.calls += 1
            return AdapterCall(None, 0, 0, "invalid_output")
    planner = InvalidPlanner()
    result = asyncio.run(service.generate(planner, "test", user, uuid.uuid4()))
    assert result.error_code == "invalid_output" and planner.calls == 2
    class RateLimitedPlanner:
        calls = 0
        async def generate(self, *args, **kwargs):
            self.calls += 1
            return AdapterCall(None, 0, 0, "provider_rate_limit", True)
    planner = RateLimitedPlanner()
    run_id = uuid.uuid4()
    result = asyncio.run(service.generate(planner, "test", user, run_id))
    assert result.error_code == "provider_rate_limit" and planner.calls == 3
    with Session(service.engine) as database:
        assert database.scalar(select(UsageRecord.llm_calls).where(UsageRecord.run_id == run_id)) == 3


def test_mock_success_and_reported_usage_overrun(context):
    service, user = context
    service.settings.llm_mode = "mock"
    result = asyncio.run(service.generate(MockPlanner(), "Tổng quan năm 2025", user, uuid.uuid4()))
    assert result.decision.outcome == "plan"
    service.settings.llm_mode = "live"
    permit = service.reserve(user, uuid.uuid4(), 10, 10)
    assert service.reconcile(user, permit, AdapterCall(None, 100, 100)) is False
    assert service.reconcile(user, permit, AdapterCall(None, 100, 100)) is False


def test_unknown_principal_and_cross_owner_reservation_are_rejected(context):
    service, user = context
    with pytest.raises(UsageLimitError, match="builder_required"):
        service.reserve(uuid.uuid4(), uuid.uuid4(), 0, 0)
    run = uuid.uuid4()
    permit = service.reserve(user, run, 0, 0)
    with pytest.raises(UsageLimitError, match="reservation_not_found"):
        service.reconcile(uuid.uuid4(), permit, AdapterCall(None, 0, 0))
