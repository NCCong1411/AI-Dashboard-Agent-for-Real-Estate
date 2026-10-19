"""Adversarial AST validation tests for compiler-generated SQL."""

from __future__ import annotations

import pytest
from sqlglot import exp

from app.analytics.compiler import compile_analysis_plan
from app.analytics.validator import SQLValidationError, validate_compiled_sql
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION


def valid_compiled_sql() -> str:
    plan = AnalysisPlan.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": CATALOG_VERSION,
            "kpis": ["sales_value", "transaction_count"],
            "dimensions": ["month", "state"],
            "filters": [{"field": "property_type", "op": "in", "value": ["House", "Condo"]}],
            "date_range": {"start": "2025-01-01", "end_exclusive": "2026-01-01"},
            "sort": [{"field": "sales_value", "direction": "desc"}],
            "limit": 100,
        }
    )
    return compile_analysis_plan(plan).sql


def test_validator_accepts_compiler_output() -> None:
    statement = validate_compiled_sql(valid_compiled_sql())
    assert isinstance(statement, exp.Select)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT s.sale_price AS sales_value FROM public.transactions AS s",
        "SELECT * FROM analytics.sales AS s",
        "SELECT pg_sleep(1) AS sales_value FROM analytics.sales AS s",
        "SELECT current_setting('app.scope') AS state FROM analytics.sales AS s",
        "SELECT lo_import('/tmp/x') AS sales_value FROM analytics.sales AS s",
        "SELECT SUM(s.sale_price) AS profit FROM analytics.sales AS s",
        "SELECT AVG(s.sale_price) AS sales_value FROM analytics.sales AS s",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s WHERE s.state = 'CA'",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s WHERE s.sale_price = :filter_0",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s WHERE s.state = :x",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s LIMIT 10",
        "SELECT DISTINCT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s",
        "SELECT SUM(s.sale_price) AS sales_value INTO temporary_sales FROM analytics.sales AS s",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s OFFSET 1",
        "SELECT s.state AS state FROM analytics.sales AS s GROUP BY s.state",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s(sale_price, agent_id, property_id, transaction_id)",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s TABLESAMPLE SYSTEM (10)",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s ORDER BY sales_value WITH FILL FROM RANDOM()",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s LIMIT :limit WITH TIES",
        "SELECT s.state AS state, SUM(s.sale_price) AS sales_value FROM analytics.sales AS s GROUP BY DISTINCT s.state",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s JOIN analytics.sales AS x ON true",
        "SELECT SUM(s.sale_price) AS sales_value FROM (SELECT * FROM analytics.sales) AS s",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s UNION SELECT 1",
        "WITH x AS (SELECT * FROM analytics.sales) SELECT COUNT(s.transaction_id) AS transaction_count FROM x AS s",
        "WITH deleted AS (DELETE FROM analytics.sales RETURNING *) SELECT COUNT(s.transaction_id) AS transaction_count FROM analytics.sales AS s",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s; SELECT 1",
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s; DROP TABLE analytics.sales",
        "UPDATE analytics.sales SET sale_price = 0",
        "DELETE FROM analytics.sales",
        "CREATE TABLE analytics.bad(id integer)",
        "DROP TABLE analytics.sales",
    ],
)
def test_validator_rejects_sql_outside_allowlist(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_compiled_sql(sql)


def test_validator_requires_matching_dimension_grouping_and_selected_sort_alias() -> None:
    with pytest.raises(SQLValidationError, match="GROUP BY"):
        validate_compiled_sql(
            "SELECT s.state AS state, SUM(s.sale_price) AS sales_value "
            "FROM analytics.sales AS s"
        )
    with pytest.raises(SQLValidationError, match="ORDER BY"):
        validate_compiled_sql(
            "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
            "ORDER BY state"
        )


def test_validator_requires_complete_half_open_date_pair() -> None:
    with pytest.raises(SQLValidationError, match="half-open pair"):
        validate_compiled_sql(
            "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
            "WHERE s.transaction_date >= :start_date"
        )


@pytest.mark.parametrize("sql", ["SELECT 'unterminated", "SELECT /* unterminated"])
def test_validator_wraps_tokenization_errors(sql: str) -> None:
    with pytest.raises(SQLValidationError, match="invalid PostgreSQL SQL"):
        validate_compiled_sql(sql)


@pytest.mark.parametrize("ordering", ["ASC NULLS FIRST", "DESC NULLS LAST"])
def test_validator_rejects_non_default_null_ordering(ordering: str) -> None:
    with pytest.raises(SQLValidationError, match="NULL placement"):
        validate_compiled_sql(
            "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
            f"ORDER BY sales_value {ordering} LIMIT :limit"
        )


@pytest.mark.parametrize("ordering", ["ASC", "DESC", "ASC NULLS LAST", "DESC NULLS FIRST"])
def test_validator_accepts_default_null_ordering(ordering: str) -> None:
    validate_compiled_sql(
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
        f"ORDER BY sales_value {ordering} LIMIT :limit"
    )


def test_validator_rejects_normalized_non_postgres_equality() -> None:
    with pytest.raises(SQLValidationError, match="equality operator"):
        validate_compiled_sql(
            "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
            "WHERE s.state == :filter_0"
        )
    # Comments are not operators and remain safe in the supported SQL.
    validate_compiled_sql(
        "SELECT SUM(s.sale_price) AS sales_value FROM analytics.sales AS s "
        "WHERE s.state = :filter_0 /* == is only a comment */"
    )
