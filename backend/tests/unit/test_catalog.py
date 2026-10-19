"""P2-T01 contract, catalog RBAC, and generated-client tests."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.analytics.catalog import KPI_CATALOG, validate_plan_semantics
from app.auth.dependencies import current_user
from app.config import get_settings
from app.db.models import User
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION
from app.schemas.dashboard import DashboardSpec, QueryResult


def valid_plan(**updates: object) -> dict:
    value = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "kpis": ["sales_value"],
        "dimensions": ["month", "state"],
        "filters": [{"field": "state", "op": "eq", "value": "CA"}],
    }
    value.update(updates)
    return value


def test_analysis_plan_rejects_unknowns_and_requires_versions() -> None:
    plan = AnalysisPlan.model_validate(valid_plan())
    assert plan.date_range.start is None and plan.date_range.end_exclusive is None
    validate_plan_semantics(plan)
    for invalid in (
        valid_plan(kpis=["profit"]),
        valid_plan(dimensions=["agency"]),
        valid_plan(filters=[{"field": "email", "op": "eq", "value": "x"}]),
        {key: value for key, value in valid_plan().items() if key != "catalog_version"},
        valid_plan(catalog_version="future-version"),
        valid_plan(unexpected="field"),
    ):
        with pytest.raises(ValidationError):
            AnalysisPlan.model_validate(invalid)
    with pytest.raises(ValidationError, match="in requires a list"):
        AnalysisPlan.model_validate(
            valid_plan(filters=[{"field": "state", "op": "in", "value": "CA"}])
        )
    for invalid_filter in (
        {"field": "state", "op": "eq", "value": 6},
        {"field": "city", "op": "in", "value": ["Austin", 7]},
        {"field": "property_type", "op": "eq", "value": True},
        {"field": "agent", "op": "eq", "value": "42"},
        {"field": "agent", "op": "eq", "value": True},
        {"field": "agent", "op": "in", "value": [1, "2"]},
    ):
        with pytest.raises(ValidationError):
            AnalysisPlan.model_validate(valid_plan(filters=[invalid_filter]))
    for valid_filter in (
        {"field": "state", "op": "eq", "value": "CA"},
        {"field": "city", "op": "in", "value": ["Austin", "Dallas"]},
        {"field": "agent", "op": "eq", "value": 42},
        {"field": "agent", "op": "in", "value": [1, 2]},
    ):
        AnalysisPlan.model_validate(valid_plan(filters=[valid_filter]))
    with pytest.raises(ValueError, match="sort field must be selected"):
        validate_plan_semantics(
            AnalysisPlan.model_validate(valid_plan(sort=[{"field": "city", "direction": "asc"}]))
        )


def test_dashboard_references_and_decimal_json_round_trip() -> None:
    dashboard = DashboardSpec.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": CATALOG_VERSION,
            "title": "Sales",
            "queries": [{"id": "sales", "plan": valid_plan(dimensions=[])}],
            "widgets": [
                {
                    "id": "sales-card",
                    "title": "Sales value",
                    "chart": "card",
                    "query_id": "sales",
                    "kpi": "sales_value",
                }
            ],
            "layout": [{"widget_id": "sales-card", "x": 0, "y": 0, "width": 4, "height": 2}],
        }
    )
    assert dashboard.widgets[0].query_id == "sales"
    with pytest.raises(ValidationError, match="widget must reference"):
        DashboardSpec.model_validate(
            dashboard.model_dump() | {"widgets": [dashboard.widgets[0].model_dump() | {"kpi": "transaction_count"}]}
        )

    result = QueryResult.model_validate(
        {
            "columns": [{"name": "sales_value", "type": "decimal", "unit": "dataset_currency"}],
            "rows": [{"sales_value": Decimal("870798679.6400")}],
            "latency_ms": 12,
            "status": "ok",
        }
    )
    encoded = result.model_dump_json()
    assert '"870798679.6400"' in encoded
    restored = QueryResult.model_validate_json(encoded)
    assert Decimal(restored.rows[0]["sales_value"]) == Decimal("870798679.6400")


def test_catalog_is_versioned_and_hides_sql_from_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(KPI_CATALOG) == {
        "sales_value", "transaction_count", "average_sale_value", "sold_property_count",
        "offer_count", "accepted_offer_rate", "repair_cost",
    }
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    main = importlib.import_module("app.main")
    main = importlib.reload(main)

    def fetch(role: str) -> dict:
        user = User(username=role, password_hash="hidden", role=role, scope_profile="ALL")
        main.app.dependency_overrides[current_user] = lambda: user
        try:
            with TestClient(main.app) as client:
                response = client.get("/api/catalog")
            assert response.status_code == 200
            return response.json()
        finally:
            main.app.dependency_overrides.clear()

    builder = fetch("builder")
    viewer = fetch("viewer")
    assert builder["catalog_version"] == viewer["catalog_version"] == CATALOG_VERSION
    assert builder["default_date_range"] == "all_available_data"
    assert builder["money_encoding"] == "base_10_string"
    assert "sql_expression" in builder["kpis"][0] and "view" in builder["kpis"][0]
    assert "sql_expression" not in viewer["kpis"][0] and "view" not in viewer["kpis"][0]
    assert "dimension_fields" not in viewer


def test_generated_types_are_reproducible_from_openapi(tmp_path: Path) -> None:
    executable = ROOT / "frontend" / "node_modules" / ".bin" / "openapi-typescript.cmd"
    if not executable.exists():
        pytest.skip("frontend dependencies are not installed")
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    generate_command = package["scripts"]["generate:api"]
    assert generate_command.index("export_openapi.py") < generate_command.index(
        "openapi-typescript"
    )
    openapi_path = tmp_path / "openapi.json"
    generated_path = tmp_path / "api.generated.ts"
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"
    subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "export_openapi.py"), "--output", str(openapi_path)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(executable), str(openapi_path), "-o", str(generated_path)],
        cwd=ROOT / "frontend",
        check=True,
        capture_output=True,
        text=True,
    )
    checked_openapi = json.loads((ROOT / "frontend" / "openapi.json").read_text(encoding="utf-8"))
    assert json.loads(openapi_path.read_text(encoding="utf-8")) == checked_openapi
    assert generated_path.read_text(encoding="utf-8") == (
        ROOT / "frontend" / "src" / "types" / "api.generated.ts"
    ).read_text(encoding="utf-8")
