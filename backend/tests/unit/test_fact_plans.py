"""Fact boundaries, legacy semantics, and independent SQL allowlist attacks."""

import pytest

from app.analytics.compiler import compile_analysis_plan
from app.analytics.validator import SQLValidationError, validate_compiled_sql
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, LEGACY_CATALOG_VERSION, SCHEMA_VERSION


def plan(kpis, **updates):
    return AnalysisPlan.model_validate({"schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
                                       "kpis": kpis, **updates})


@pytest.mark.parametrize("kpis,date,source", [
    (["offer_count", "accepted_offer_rate"], "offer_date", "offers"),
    (["repair_cost"], "repair_date", "repairs"),
])
def test_fact_date_and_hidden_count(kpis, date, source):
    compiled = compile_analysis_plan(plan(kpis, dimensions=["month", "state"],
        date_range={"start": "2025-01-01", "end_exclusive": "2026-01-01"}), include_fact_count=True)
    assert f"FROM analytics.{source} AS s" in compiled.sql
    assert f"s.{date} >= :start_date" in compiled.sql
    assert f"date_trunc('month', s.{date})::date AS month" in compiled.sql
    assert "transaction_date" not in compiled.sql and "_fact_count" in compiled.sql
    assert "JOIN" not in compiled.sql
    validate_compiled_sql(compiled.sql)


@pytest.mark.parametrize("kpis,updates", [
    (["sales_value", "offer_count"], {}), (["offer_count", "repair_cost"], {}),
    (["repair_cost"], {"dimensions": ["agent"]}),
    (["offer_count"], {"filters": [{"field": "agent", "op": "eq", "value": 1}]}),
    (["offer_count"], {"catalog_version": LEGACY_CATALOG_VERSION}),
    (["repair_cost"], {"catalog_version": LEGACY_CATALOG_VERSION}),
])
def test_mixed_fact_unsupported_dimension_filter_and_legacy_new_kpi_fail(kpis, updates):
    with pytest.raises(ValueError):
        compile_analysis_plan(plan(kpis, **updates))


def test_legacy_sales_sql_is_identical_and_version_not_rewritten():
    old = plan(["sales_value", "transaction_count"], dimensions=["month"], catalog_version=LEGACY_CATALOG_VERSION)
    new = plan(["sales_value", "transaction_count"], dimensions=["month"])
    assert compile_analysis_plan(old) == compile_analysis_plan(new)
    assert old.catalog_version == LEGACY_CATALOG_VERSION


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(s.offer_id) AS offer_count FROM analytics.sales AS s",
    "SELECT SUM(s.sale_price) AS sales_value FROM analytics.offers AS s",
    "SELECT SUM(s.cost) AS repair_cost FROM analytics.repairs AS s WHERE s.repair_date >= :start_date",
    "SELECT SUM(s.cost) AS repair_cost FROM analytics.repairs AS s WHERE s.agent_id = :filter_0",
    "SELECT COUNT(s.offer_id) AS offer_count FROM public.offers AS s",
    "SELECT COUNT(s.repair_id) AS _fact_count FROM analytics.repairs AS s",
    "SELECT SUM(s.cost) AS repair_cost FROM analytics.repairs AS s JOIN analytics.offers o ON true",
    "SELECT COUNT(s.offer_id)::numeric / NULLIF(COUNT(s.offer_id),0) AS accepted_offer_rate FROM analytics.offers AS s",
    "SELECT COUNT(s.offer_id) FILTER (WHERE s.status='rejected')::numeric / NULLIF(COUNT(s.offer_id),0) AS accepted_offer_rate FROM analytics.offers AS s",
])
def test_cross_fact_and_tampered_sql_rejected(sql):
    with pytest.raises(SQLValidationError):
        validate_compiled_sql(sql)
