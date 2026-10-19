"""Unit and golden-contract tests for the phase-2 SQL compiler."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analytics.compiler import compile_analysis_plan
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION, DateRange


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def plan(**updates: object) -> AnalysisPlan:
    value: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "kpis": ["sales_value"],
    }
    value.update(updates)
    return AnalysisPlan.model_validate(value)


def test_all_four_kpis_match_independent_golden_contract() -> None:
    compiled = compile_analysis_plan(
        plan(
            kpis=[
                "sales_value",
                "transaction_count",
                "average_sale_value",
                "sold_property_count",
            ],
            date_range={"start": "2025-01-01", "end_exclusive": "2026-01-01"},
        )
    )
    expected_expressions = (
        "SUM(s.sale_price) AS sales_value",
        "COUNT(s.transaction_id) AS transaction_count",
        "SUM(s.sale_price) / NULLIF(COUNT(s.transaction_id), 0) AS average_sale_value",
        "COUNT(DISTINCT s.property_id) AS sold_property_count",
    )
    assert all(expression in compiled.sql for expression in expected_expressions)
    assert "FROM analytics.sales AS s" in compiled.sql
    assert "JOIN" not in compiled.sql
    assert compiled.parameters == {
        "start_date": date(2025, 1, 1),
        "end_date_exclusive": date(2026, 1, 1),
    }

    golden_sql = (FIXTURES / "golden_queries.sql").read_text(encoding="utf-8")
    for expression in (
        "SUM(t.sale_price) AS sales_value",
        "COUNT(t.transaction_id) AS transaction_count",
        "SUM(t.sale_price) / NULLIF(COUNT(t.transaction_id), 0) AS average_sale_value",
        "COUNT(DISTINCT t.property_id) AS sold_property_count",
    ):
        assert expression in golden_sql

    # The import-baseline oracle demonstrates why transaction and property
    # grain must remain distinct; its calculations are independently tested by
    # test_golden_fixtures.py.
    results = json.loads((FIXTURES / "golden_results.json").read_text(encoding="utf-8"))
    all_time = next(item for item in results["cases"] if item["id"] == "all_time_all")
    sales = all_time["expected"]["sales"]
    assert sales["transaction_count"] == 1000
    assert sales["sold_property_count"] == 125
    assert sales["average_fraction"] == {
        "numerator": sales["sales_value"],
        "denominator": sales["transaction_count"],
    }


def test_dimensions_filters_sort_and_limit_use_only_fixed_mappings_and_binds() -> None:
    injection = "CA' OR 1=1 --"
    compiled = compile_analysis_plan(
        plan(
            kpis=["sales_value", "sold_property_count"],
            dimensions=["month", "state", "property_type", "agent"],
            filters=[
                {"field": "state", "op": "eq", "value": injection},
                {"field": "city", "op": "in", "value": ["Austin", "Dallas"]},
            ],
            sort=[
                {"field": "month", "direction": "asc"},
                {"field": "sales_value", "direction": "desc"},
            ],
            limit=250,
        )
    )
    assert "date_trunc('month', s.transaction_date)::date AS month" in compiled.sql
    assert "s.property_type AS property_type" in compiled.sql
    assert "s.agent_id AS agent" in compiled.sql
    assert "s.state = :filter_0" in compiled.sql
    assert "s.city IN (:filter_1_0, :filter_1_1)" in compiled.sql
    assert "month ASC" in compiled.sql and "sales_value DESC" in compiled.sql
    assert "LIMIT :limit" in compiled.sql
    assert injection not in compiled.sql
    assert compiled.parameters == {
        "filter_0": injection,
        "filter_1_0": "Austin",
        "filter_1_1": "Dallas",
        "limit": 250,
    }


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        ("day", "s.transaction_date AS day"),
        ("month", "date_trunc('month', s.transaction_date)::date AS month"),
        ("quarter", "date_trunc('quarter', s.transaction_date)::date AS quarter"),
        ("year", "date_trunc('year', s.transaction_date)::date AS year"),
        ("state", "s.state AS state"),
        ("city", "s.city AS city"),
        ("property_type", "s.property_type AS property_type"),
        ("agent", "s.agent_id AS agent"),
    ],
)
def test_every_dimension_has_a_catalog_owned_projection_and_grouping(
    dimension: str, expected: str
) -> None:
    compiled = compile_analysis_plan(plan(dimensions=[dimension]))  # type: ignore[list-item]
    assert expected in compiled.sql
    assert "GROUP BY" in compiled.sql
    assert expected.removesuffix(f" AS {dimension}") in compiled.sql.split("GROUP BY", 1)[1]


def test_all_time_plan_has_no_invented_date_bound() -> None:
    compiled = compile_analysis_plan(plan())
    assert "WHERE" not in compiled.sql
    assert compiled.parameters == {}


def test_compiler_rejects_unvalidated_or_semantically_invalid_input() -> None:
    with pytest.raises(TypeError, match="AnalysisPlan"):
        compile_analysis_plan({"kpis": ["sales_value"]})  # type: ignore[arg-type]
    invalid_sort = plan(sort=[{"field": "city", "direction": "asc"}])
    with pytest.raises(ValueError, match="sort field must be selected"):
        compile_analysis_plan(invalid_sort)
    bypassed = AnalysisPlan.model_construct(
        schema_version=SCHEMA_VERSION,
        catalog_version=CATALOG_VERSION,
        kpis=["profit"],
        dimensions=[],
        filters=[],
        date_range=DateRange(),
        sort=[],
        limit=None,
    )
    with pytest.raises(ValidationError):
        compile_analysis_plan(bypassed)
