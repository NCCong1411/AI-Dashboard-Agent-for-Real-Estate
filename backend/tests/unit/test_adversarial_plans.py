"""Reject malicious structured plans before connection acquisition."""

from copy import deepcopy

import pytest

from app.analytics.executor import QueryExecutor
from app.config import Settings
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION


BASE = {"schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION, "kpis": ["sales_value"]}
ATTACKS = [
    {"sql": "SELECT email FROM public.clients"},
    {"view": "public.clients"}, {"scope": "ALL"},
    {"kpis": ["SUM(sale_price); DROP TABLE public.transactions"]},
    {"dimensions": ["state; SELECT pg_sleep(10)"]},
    {"filters": [{"field": "client_id", "op": "eq", "value": 1}]},
    {"filters": [{"field": "state", "op": "raw", "value": "1=1"}]},
    {"sort": [{"field": "pg_sleep(10)", "direction": "asc"}]},
    {"limit": 1001}, {"limit": -1},
    {"catalog_version": "future-unvalidated"},
    {"kpis": ["repair_cost", "sales_value"]},
    {"kpis": ["accepted_offer_rate"], "dimensions": ["agent"]},
    {"kpis": ["repair_cost"], "filters": [{"field": "agent", "op": "eq", "value": 1}]},
    {"date_range": {"start": "2025-01-01", "end_exclusive": "2024-01-01"}},
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_untrusted_plan_and_model_construct_cannot_reach_database(monkeypatch, recwarn, attack):
    executor = QueryExecutor(Settings(_env_file=None, app_env="test"))
    monkeypatch.setattr(executor, "_engine", lambda *args: pytest.fail("invalid plan opened a connection"))
    payload = deepcopy(BASE) | attack
    with pytest.raises((ValueError, TypeError)):
        executor.execute(payload, "ALL")
    # Unknown extras are intentionally dropped by model_construct; prove the
    # public executor rejects raw dicts and semantic bypasses separately.
    if set(attack) <= {"kpis", "dimensions", "catalog_version", "limit", "sort", "filters", "date_range"}:
        forged = AnalysisPlan.model_construct(**payload)
        with pytest.raises((ValueError, TypeError)):
            executor.execute(forged, "ALL")
    assert not recwarn.list, "invalid plan values leaked through serializer warnings"


@pytest.mark.parametrize("value", ["CA' OR true --", "TX'); DROP TABLE public.offers; --", "<img src=x onerror=alert(1)>"])
def test_hostile_filter_values_remain_bound_data(value):
    from app.analytics.compiler import compile_analysis_plan
    compiled = compile_analysis_plan(AnalysisPlan.model_validate(BASE | {
        "filters": [{"field": "state", "op": "eq", "value": value}],
    }))
    assert value not in compiled.sql
    assert compiled.parameters == {"filter_0": value}
