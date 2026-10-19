from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.usage import estimate_cost, input_token_bound


def test_cost_rounds_up_at_microdollar_boundary_and_includes_all_output():
    assert estimate_cost(1, 1, Decimal("0.01"), Decimal("0.01")) == Decimal("0.000001")
    assert estimate_cost(1000, 2000, Decimal("2"), Decimal("10")) == Decimal("0.022000")
    assert estimate_cost(0, 0, Decimal("2"), Decimal("10")) == Decimal(0)


def test_token_reservation_accounts_for_schema_and_prompt_growth():
    now = datetime(2026, 10, 15, tzinfo=UTC)
    small = input_token_bound("Tổng quan", None, now)
    large = input_token_bound("Tổng quan " + "é" * 100, None, now)
    assert 2048 < small < 24000 - 4096
    assert large >= small + 200


@pytest.mark.parametrize("field,value", [("llm_max_calls_per_run", 4), ("llm_max_tokens_per_run", 24001),
    ("llm_timeout_seconds", 61), ("llm_budget_per_run_usd", "NaN"), ("llm_budget_per_day_usd", -1)])
def test_hard_run_limits_cannot_be_disabled_by_settings(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", **{field: value})
