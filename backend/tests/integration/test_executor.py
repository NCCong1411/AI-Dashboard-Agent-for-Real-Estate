"""Executor/golden comparisons using actual scoped PostgreSQL test logins."""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from app.analytics.executor import QueryExecutor
from app.config import Settings
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION


CASES = [case for case in json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "golden_results.json").read_text(encoding="utf-8")
)["cases"] if "expected" in case or "expected_sales" in case]


def plan(**updates):
    return AnalysisPlan.model_validate({
        "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
        "kpis": ["sales_value", "transaction_count", "average_sale_value", "sold_property_count"],
        **updates,
    })


@pytest.fixture
def executor():
    values = {}
    for scope in ("ALL", "CA", "TX"):
        value = os.getenv(f"READER_{scope}_TEST_DATABASE_URL", "")
        if not value:
            pytest.skip(f"requires READER_{scope}_TEST_DATABASE_URL for actual executor integration")
        if "test" not in (make_url(value).database or "").lower():
            pytest.fail("executor integration requires a separate database containing 'test'")
        values[f"reader_{scope.lower()}_database_url"] = value
    instance = QueryExecutor(Settings(_env_file=None, app_env="test", **values))
    yield instance
    instance.close()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_executor_matches_independent_golden_sales(executor, case):
    response = executor.execute(plan(date_range={
        "start": case["start_date"], "end_exclusive": case["end_date_exclusive"],
    }), case["scope"])
    expected = case["expected"]["sales"] if "expected" in case else case["expected_sales"]
    assert response.status == expected["status"], response.model_dump()
    row = response.rows[0]
    for field in ("transaction_count", "sold_property_count"):
        assert row[field] == expected[field]
    for field in ("sales_value", "average_sale_value"):
        if expected[field] is None:
            assert row[field] is None
        else:
            assert abs(Decimal(row[field]) - Decimal(expected[field])) < Decimal("0.01")


def test_scopes_alternate_without_leakage_and_empty_intersection(executor):
    for scope, count in [("ALL", 1000), ("CA", 135), ("TX", 132), ("CA", 135), ("ALL", 1000)]:
        response = executor.execute(plan(kpis=["transaction_count"]), scope)
        assert response.status == "ok" and response.rows == [{"transaction_count": count}]
    response = executor.execute(plan(filters=[{"field": "state", "op": "eq", "value": "TX"}]), "CA")
    assert response.status == "no_data" and response.rows[0]["transaction_count"] == 0
    response = executor.execute(plan(filters=[{"field": "city", "op": "eq", "value": "x' OR 1=1 --"}]), "ALL")
    assert response.status == "no_data"


@pytest.mark.parametrize("dimension", ["day", "month", "quarter", "year", "state", "city", "property_type", "agent"])
def test_every_dimension_executes_without_fanout(executor, dimension):
    response = executor.execute(plan(dimensions=[dimension], sort=[{"field": dimension}]), "ALL")
    assert response.status == "ok", response.model_dump()
    assert sum(row["transaction_count"] for row in response.rows) == 1000
    assert sum(Decimal(row["sales_value"]) for row in response.rows) == Decimal("870798679.64")


def test_live_cost_and_row_limit_gates(executor):
    executor.settings.analytics_max_query_cost = 0.0001
    assert executor.execute(plan(), "ALL").error_code == "cost_limit"
    executor.settings.analytics_max_query_cost = 100000
    executor.settings.analytics_max_rows = 1
    response = executor.execute(plan(dimensions=["state"]), "ALL")
    assert response.error_code == "row_limit" and response.rows == []
    # Gate failure must release the connection for the next request.
    assert executor.execute(plan(), "ALL").status == "ok"


def test_transaction_is_read_only_and_timeout_is_local(executor):
    engine = executor._engine("ALL")
    observed = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("EXPLAIN"):
            # Same DBAPI connection and transaction, using a separate cursor.
            with connection.connection.cursor() as probe:
                probe.execute("SHOW transaction_read_only")
                readonly = probe.fetchone()[0]
                probe.execute("SHOW statement_timeout")
                observed.append((readonly, probe.fetchone()[0]))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert executor.execute(plan(), "ALL").status == "ok"
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert observed == [("on", "5s")]
    with engine.connect() as connection:
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "off"
        assert connection.execute(text("SHOW statement_timeout")).scalar_one() != "5s"


def test_real_lock_timeout_rolls_back_and_pool_recovers(executor):
    value = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("requires MIGRATION_TEST_DATABASE_URL to hold a test-only table lock")
    if make_url(value).database != make_url(os.environ["READER_ALL_TEST_DATABASE_URL"]).database:
        pytest.fail("locking connection must address the same isolated test database")
    owner = create_engine(value)
    executor.settings.analytics_statement_timeout_ms = 30
    try:
        with owner.connect() as connection, connection.begin():
            connection.execute(text("LOCK TABLE public.transactions IN ACCESS EXCLUSIVE MODE"))
            response = executor.execute(plan(), "ALL")
            assert response.error_code == "timeout" and response.rows == []
        assert executor.execute(plan(), "ALL").status == "ok"
    finally:
        owner.dispose()
