from app.schemas.dashboard import DashboardQueryRequest
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION
from app.services.dashboard import apply_filters
import pytest
from pydantic import ValidationError
from app.schemas.dashboard import DashboardSpec, QueryResult
from scripts.seed_dashboard import demo_spec


def test_saved_date_and_state_filters_are_never_widened():
    plan = AnalysisPlan(
        schema_version=SCHEMA_VERSION, catalog_version=CATALOG_VERSION, kpis=["sales_value"],
        date_range={"start": "2025-01-01", "end_exclusive": "2026-01-01"},
        filters=[{"field": "state", "op": "eq", "value": "CA"}],
    )
    request = DashboardQueryRequest(
        date_range={"start": "2024-01-01", "end_exclusive": "2027-01-01"},
        filters=[{"field": "state", "op": "eq", "value": "TX"}],
    )
    merged = apply_filters(plan, request)
    assert merged.date_range == plan.date_range
    assert [item.value for item in merged.filters] == ["CA", "TX"]
    assert len(plan.filters) == 1
    assert apply_filters(plan, DashboardQueryRequest(date_range={"start": "2026-01-01", "end_exclusive": "2027-01-01"})) is None
    assert apply_filters(plan, DashboardQueryRequest(date_range={})).date_range == plan.date_range


@pytest.mark.parametrize("mutation", ["grouped_card", "missing_chart_dimension", "grid_overflow", "grid_overlap"])
def test_renderer_contract_rejects_misleading_or_overlapping_widgets(mutation):
    spec = demo_spec().model_dump(mode="python")
    if mutation == "grouped_card":
        spec["widgets"][0]["query_id"] = "by_state"
    elif mutation == "missing_chart_dimension":
        spec["widgets"][3]["dimension"] = None
    elif mutation == "grid_overflow":
        spec["layout"][0]["width"] = 12
        spec["layout"][0]["x"] = 1
    else:
        spec["layout"][1]["x"] = 0
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate(spec)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_money_is_not_a_valid_query_result(value):
    with pytest.raises(ValidationError):
        QueryResult(columns=[{"name": "sales_value", "type": "decimal"}],
                    rows=[{"sales_value": value}], latency_ms=0, status="ok")
