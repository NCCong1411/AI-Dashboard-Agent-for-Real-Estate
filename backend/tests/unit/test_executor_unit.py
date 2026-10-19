"""Resource and failure-path tests; these do not prove PostgreSQL/RLS gates."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.analytics import executor as module
from app.analytics.executor import QueryExecutor, QueryRejected, explain_cost
from app.config import Settings
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION


def plan(**updates):
    return AnalysisPlan.model_validate({
        "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
        "kpis": ["sales_value"], **updates,
    })


def harness(monkeypatch, records=None, cost=10, **settings):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execution_options.return_value = connection
    result = MagicMock()
    result.__enter__.return_value = result
    result.mappings.return_value.fetchmany.return_value = records or []
    explanation = MagicMock()
    explanation.scalar_one.return_value = [{"Plan": {"Total Cost": cost}}]
    events = []

    @contextmanager
    def transaction():
        try:
            yield
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    connection.begin.side_effect = transaction

    def execute(statement, parameters=None):
        sql = str(statement)
        events.append((sql, parameters))
        if sql.startswith("EXPLAIN"):
            return explanation
        if sql.startswith("SELECT\n"):
            return result
        return MagicMock()

    connection.execute.side_effect = execute
    engine = MagicMock()
    engine.connect.return_value = connection
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(module, "create_reader_engine", factory)
    executor = QueryExecutor(Settings(_env_file=None, app_env="test", **settings))
    return executor, connection, result, factory, events


def test_exact_money_hidden_count_and_transaction_guards(monkeypatch):
    executor, connection, result, factory, events = harness(monkeypatch, [
        {"sales_value": Decimal("9007199254740993.17"), "_fact_count": 1},
    ])
    response = executor.execute(plan(filters=[{"field": "city", "op": "eq", "value": "x' --"}]), "CA")
    assert response.status == "ok"
    assert response.rows == [{"sales_value": "9007199254740993.17"}]
    assert response.columns[0].unit == "dataset_currency"
    assert events[0][0] == "SET TRANSACTION READ ONLY"
    assert events[1] == ("SELECT set_config('statement_timeout', :timeout, true)", {"timeout": "5000ms"})
    assert events[2][0].startswith("EXPLAIN (FORMAT JSON) SELECT")
    assert "COUNT(s.transaction_id) AS _fact_count" in events[2][0]
    assert events[2][1] == {"filter_0": "x' --"}
    assert "x' --" not in events[2][0]
    assert events[-1] == "commit"
    result.mappings.return_value.fetchmany.assert_called_once_with(1001)
    result.__exit__.assert_called_once()
    connection.__exit__.assert_called_once()
    factory.assert_called_once_with(executor.settings, "CA")


@pytest.mark.parametrize(("records", "status"), [
    ([], "no_data"),
    ([{"sales_value": None, "_fact_count": 0}], "no_data"),
    ([{"sales_value": Decimal("0"), "_fact_count": 2}], "ok"),
    ([{"sales_value": None, "_fact_count": 2}], "ok"),
])
def test_empty_set_is_not_confused_with_zero_or_null_prices(monkeypatch, records, status):
    executor, *_ = harness(monkeypatch, records)
    assert executor.execute(plan(), "ALL").status == status


def test_grouped_dates_and_integer_metadata(monkeypatch):
    executor, *_ = harness(monkeypatch, [{"month": date(2025, 1, 1), "transaction_count": 4, "_fact_count": 4}])
    result = executor.execute(plan(kpis=["transaction_count"], dimensions=["month"]), "TX")
    assert [item.type for item in result.columns] == ["date", "integer"]
    assert result.model_dump(mode="json")["rows"] == [{"month": "2025-01-01", "transaction_count": 4}]


@pytest.mark.parametrize("max_rows", [2, 1000])
def test_row_overflow_returns_no_partial_rows_and_rolls_back(monkeypatch, max_rows):
    executor, connection, result, _, events = harness(monkeypatch, [
        {"sales_value": Decimal("1"), "_fact_count": 1} for _ in range(max_rows + 1)
    ], analytics_max_rows=max_rows)
    response = executor.execute(plan(), "ALL")
    assert response.status == "error" and response.error_code == "row_limit"
    assert response.rows == []
    assert events[-1] == "rollback"
    result.__exit__.assert_called_once()
    connection.__exit__.assert_called_once()


def test_cost_gate_blocks_execution_and_allows_exact_threshold(monkeypatch):
    executor, connection, _, _, events = harness(monkeypatch, cost=11, analytics_max_query_cost=10)
    response = executor.execute(plan(), "TX")
    assert response.error_code == "cost_limit"
    connection.execution_options.assert_not_called()
    assert events[-1] == "rollback"
    executor.settings.analytics_max_query_cost = 11
    assert executor.execute(plan(), "TX").error_code is None


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), "2"])
def test_invalid_cost_is_rejected(value):
    with pytest.raises(QueryRejected, match="database_error"):
        explain_cost([{"Plan": {"Total Cost": value}}])


@pytest.mark.parametrize("document", [None, [], {}, [{"Plan": {}}], [{}, {}]])
def test_malformed_explain_is_rejected(document):
    with pytest.raises(QueryRejected, match="database_error"):
        explain_cost(document)


@pytest.mark.parametrize(("sqlstate", "code"), [("57014", "timeout"), ("42501", "database_error")])
def test_database_errors_are_sanitized_and_release_transaction(monkeypatch, caplog, sqlstate, code):
    executor, connection, _, _, events = harness(monkeypatch)
    error = Exception("secret password and raw SQL with private filters")
    error.sqlstate = sqlstate
    connection.execute.side_effect = OperationalError("private SQL", {"secret": "password"}, error)
    response = executor.execute(plan(), "ALL")
    assert response.error_code == code and response.rows == []
    assert "secret" not in response.model_dump_json() + caplog.text
    assert events[-1] == "rollback"
    connection.__exit__.assert_called_once()


def test_missing_configuration_is_sanitized(monkeypatch):
    executor = QueryExecutor(Settings(_env_file=None, app_env="test"))
    for field in module.READER_URL_FIELDS.values():
        setattr(executor.settings, field, None)
    response = executor.execute(plan(), "CA")
    assert response.error_code == "configuration_error"
    assert response.rows == []


def test_invalid_scope_and_constructed_plan_never_open_connection(monkeypatch):
    executor, _, _, factory, _ = harness(monkeypatch)
    with pytest.raises(ValueError, match="scope"):
        executor.execute(plan(), "bad")
    with pytest.raises(ValidationError):
        executor.execute(AnalysisPlan.model_construct(kpis=["hacked"]), "ALL")
    factory.assert_not_called()


def test_pool_routing_is_separate_and_never_caches_results(monkeypatch):
    executor, connection, _, factory, _ = harness(monkeypatch)
    for scope in ("ALL", "CA", "TX", "CA", "ALL", "TX"):
        executor.execute(plan(), scope)
    assert [call.args[1] for call in factory.call_args_list] == ["ALL", "CA", "TX"]
    assert connection.begin.call_count == 6
    executor.close()
    executor.execute(plan(), "CA")
    assert factory.call_count == 4


@pytest.mark.parametrize("field,value", [
    ("analytics_statement_timeout_ms", 5001), ("analytics_max_rows", 1001),
    ("analytics_max_query_cost", float("nan")), ("analytics_max_query_cost", 0),
    ("database_connect_timeout_seconds", 0), ("database_connect_timeout_seconds", 6),
])
def test_resource_config_cannot_disable_hard_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", **{field: value})
