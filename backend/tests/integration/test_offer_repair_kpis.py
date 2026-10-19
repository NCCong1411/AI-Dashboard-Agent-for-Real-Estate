"""Actual scoped readers: independent goldens and grouped direct-fact SQL."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Dashboard, DashboardVersion
from app.schemas.analysis import CATALOG_VERSION, LEGACY_CATALOG_VERSION
from test_executor import CASES, executor, plan
from test_agent import ORIGIN, context, create, send
from test_approval import approve


@pytest.mark.parametrize("case", [item for item in CASES if "expected" in item], ids=lambda item: item["id"])
def test_offer_repair_independent_fixture(executor, case):
    period = {"start": case["start_date"], "end_exclusive": case["end_date_exclusive"]}
    offers = executor.execute(plan(kpis=["offer_count", "accepted_offer_rate"], date_range=period), case["scope"])
    repairs = executor.execute(plan(kpis=["repair_cost"], date_range=period), case["scope"])
    expected = case["expected"]
    assert offers.status == expected["offers"]["status"], offers
    assert repairs.status == expected["repairs"]["status"], repairs
    assert offers.rows[0]["offer_count"] == expected["offers"]["offer_count"]
    for actual, value in [(offers.rows[0]["accepted_offer_rate"], expected["offers"]["accepted_offer_rate"]),
                           (repairs.rows[0]["repair_cost"], expected["repairs"]["repair_cost"])]:
        if value is None:
            assert actual is None
        else:
            assert isinstance(actual, str) and abs(Decimal(actual) - Decimal(value)) < Decimal("1e-18")
    assert [item.type for item in offers.columns] == ["integer", "decimal"]
    assert offers.columns[1].unit == "ratio"
    assert all("_fact_count" not in row for row in offers.rows + repairs.rows)


@pytest.mark.parametrize("scope", ["ALL", "CA", "TX"])
@pytest.mark.parametrize("dimension", ["day", "month", "quarter", "year", "state", "city", "property_type"])
@pytest.mark.parametrize("fact", ["offers", "repairs"])
def test_grouped_fact_matches_independent_public_sql(executor, scope, dimension, fact):
    day = "offer_date" if fact == "offers" else "repair_date"
    kpis = ["offer_count", "accepted_offer_rate"] if fact == "offers" else ["repair_cost"]
    request = plan(kpis=kpis, dimensions=[dimension], filters=[{"field": "property_type", "op": "in", "value": ["condo", "duplex"]}],
                   date_range={"start": "2022-01-01", "end_exclusive": "2026-01-01"}, sort=[{"field": dimension, "direction": "asc"}])
    result = executor.execute(request, scope)
    assert result.status in {"ok", "no_data"}, result
    grouping = {"day": f"f.{day}", "state": "p.state", "city": "p.city", "property_type": "pt.type_name"}
    grouping.update({name: f"date_trunc('{name}', f.{day})::date" for name in ("month", "quarter", "year")})
    expression = ("COUNT(f.offer_id) AS offer_count, SUM(CASE WHEN f.status='accepted' THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(f.offer_id),0) AS accepted_offer_rate"
                  if fact == "offers" else "SUM(f.cost) AS repair_cost")
    sql = f"SELECT {grouping[dimension]} AS dimension, {expression} FROM public.{fact} f JOIN public.properties p ON p.property_id=f.property_id JOIN public.property_types pt ON pt.property_type_id=p.property_type_id WHERE f.{day} >= :start AND f.{day} < :end AND pt.type_name IN (:a,:b) GROUP BY {grouping[dimension]} ORDER BY dimension"
    with executor._engine(scope).connect() as connection:
        expected = connection.execute(text(sql), {"start": date(2022, 1, 1), "end": date(2026, 1, 1), "a": "condo", "b": "duplex"}).mappings().all()
    assert len(result.rows) == len(expected)
    for actual, oracle in zip(result.rows, expected, strict=True):
        assert str(actual[dimension]) == str(oracle["dimension"])
        for kpi in kpis:
            if kpi == "offer_count":
                assert actual[kpi] == oracle[kpi]
            else:
                assert abs(Decimal(actual[kpi]) - oracle[kpi]) < Decimal("1e-18")


def test_mixed_fact_mock_preview_publishes_separate_queries_and_viewer_scope(context):
    client, login, _, _, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Giá trị giao dịch, số đề nghị, tỷ lệ đề nghị được chấp nhận và chi phí sửa chữa năm 2025").json()
    assert preview["status"] == "awaiting_approval", preview
    assert len(preview["draft"]["queries"]) == 3 and len(preview["draft"]["widgets"]) == 4
    assert preview["results"]["totals_sales"]["rows"][0]["sales_value"] == "81983427.35"
    assert preview["results"]["totals_offers"]["rows"][0]["offer_count"] == 9
    assert preview["results"]["totals_repairs"]["rows"][0]["repair_cost"] == "367957.16"
    receipt = approve(client, conversation, preview).json()
    assert receipt["checkpoint_synced"] is True
    login("viewer")
    result = client.post(f"/api/dashboards/{receipt['dashboard_id']}/query", json={}, headers=ORIGIN).json()
    assert result["scope"] == "CA"
    assert result["results"]["totals_sales"]["rows"][0]["sales_value"] == "10321229.42"
    assert result["results"]["totals_offers"]["rows"][0]["offer_count"] == 2
    assert Decimal(result["results"]["totals_offers"]["rows"][0]["accepted_offer_rate"]) == 0
    assert result["results"]["totals_repairs"]["rows"][0]["repair_cost"] == "25985.57"


def test_legacy_dashboard_read_edit_and_explicit_new_kpi_upgrade(context):
    client, login, service, _, dashboard_id = context
    with Session(service.engine) as database, database.begin():
        dashboard = database.get(Dashboard, dashboard_id)
        version = database.get(DashboardVersion, dashboard.current_version_id)
        spec = dict(version.spec, catalog_version=LEGACY_CATALOG_VERSION)
        for query in spec["queries"]:
            query["plan"]["catalog_version"] = LEGACY_CATALOG_VERSION
        version.spec, version.catalog_version = spec, LEGACY_CATALOG_VERSION
    login("all")
    detail = client.get(f"/api/dashboards/{dashboard_id}").json()
    assert detail["spec"]["catalog_version"] == LEGACY_CATALOG_VERSION
    conversation = create(client, dashboard_id=str(dashboard_id))
    preview = send(client, conversation, "Lọc California").json()
    assert preview["status"] == "awaiting_approval", preview
    assert preview["draft"]["catalog_version"] == LEGACY_CATALOG_VERSION
    previous_widgets = preview["draft"]["widgets"]
    upgraded = send(client, conversation, "Thêm số đề nghị").json()
    assert upgraded["status"] == "awaiting_approval", upgraded
    assert upgraded["draft"]["catalog_version"] == CATALOG_VERSION
    assert upgraded["draft"]["widgets"][:len(previous_widgets)] == previous_widgets
    assert all(query["plan"]["catalog_version"] == CATALOG_VERSION for query in upgraded["draft"]["queries"])
    assert approve(client, conversation, upgraded).json()["version_number"] == 2
