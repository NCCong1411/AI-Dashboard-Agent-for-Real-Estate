"""Explicit CLI-only demo publication for pre-LLM end-to-end testing."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.db.connections import create_runtime_engine
from app.db.models import AuditEvent, Dashboard, DashboardVersion, User
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION
from app.schemas.dashboard import DashboardSpec


def demo_spec() -> DashboardSpec:
    kpis = ["sales_value", "transaction_count", "average_sale_value"]
    definitions = [("sales", []), ("by_month", ["month"]), ("by_state", ["state"]), ("by_type", ["property_type"])]
    queries = [{"id": query_id, "plan": AnalysisPlan(
        schema_version=SCHEMA_VERSION, catalog_version=CATALOG_VERSION, kpis=kpis,
        dimensions=dimensions, sort=[{"field": dimensions[0], "direction": "asc"}] if dimensions else [],
    ).model_dump(mode="json")} for query_id, dimensions in definitions]
    widgets = [
        {"id": "sales-value", "title": "Giá trị giao dịch", "chart": "card", "query_id": "sales", "kpi": "sales_value"},
        {"id": "transaction-count", "title": "Số giao dịch", "chart": "card", "query_id": "sales", "kpi": "transaction_count"},
        {"id": "average-value", "title": "Giá trị giao dịch trung bình", "chart": "card", "query_id": "sales", "kpi": "average_sale_value"},
        {"id": "monthly-sales", "title": "Giá trị giao dịch theo tháng", "chart": "line", "query_id": "by_month", "kpi": "sales_value", "dimension": "month"},
        {"id": "state-sales", "title": "Giá trị giao dịch theo bang", "chart": "bar", "query_id": "by_state", "kpi": "sales_value", "dimension": "state"},
        {"id": "type-sales", "title": "Giá trị giao dịch theo loại BĐS", "chart": "donut", "query_id": "by_type", "kpi": "sales_value", "dimension": "property_type"},
    ]
    return DashboardSpec.model_validate({
        "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
        "title": "Tổng quan giao dịch bất động sản", "queries": queries, "widgets": widgets,
        "layout": [{"widget_id": widget["id"], "x": (index % 3) * 4,
                    "y": 0 if index < 3 else 2, "width": 4, "height": 2 if index < 3 else 4}
                   for index, widget in enumerate(widgets)],
    })


def seed_dashboard(database: Session, owner_username: str) -> uuid.UUID:
    owner = database.scalar(select(User).where(User.username == owner_username).with_for_update())
    if owner is None or owner.role != "builder" or not owner.is_active:
        raise ValueError("demo owner must be an active Builder")
    dashboard_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ai-dashboard-agent/demo/{owner.id}")
    existing = database.get(Dashboard, dashboard_id)
    if existing is not None:
        return existing.id
    spec = demo_spec()
    document = spec.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    dashboard = Dashboard(id=dashboard_id, owner_user_id=owner.id, title=spec.title)
    database.add(dashboard)
    database.flush()
    version = DashboardVersion(
        dashboard_id=dashboard.id, version_number=1, base_version_number=0,
        draft_hash=digest, approval_key=f"demo-seed-{dashboard_id}", spec=document,
        catalog_version=CATALOG_VERSION, schema_version=SCHEMA_VERSION, approved_by_user_id=owner.id,
    )
    database.add(version)
    database.flush()
    dashboard.current_version_id = version.id
    database.add(AuditEvent(actor_user_id=owner.id, action="demo_seed_approved",
                            target_type="dashboard", target_id=str(dashboard.id), details={"cli_only": True}))
    return dashboard.id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--confirm-demo", required=True, action="store_true")
    parser.add_argument("--use-provisioned-roles", action="store_true")
    args = parser.parse_args()
    previous_runtime = os.environ.get("RUNTIME_DATABASE_URL")
    if args.use_provisioned_roles:
        artifact = BACKEND / ".runtime" / "local-postgres-credentials.env"
        runtime_url = dotenv_values(artifact, interpolate=False).get("RUNTIME_DATABASE_URL") if artifact.is_file() else None
        if not isinstance(runtime_url, str) or not runtime_url:
            raise RuntimeError("provisioned local runtime credential is unavailable")
        os.environ["RUNTIME_DATABASE_URL"] = runtime_url
        get_settings.cache_clear()
    engine = None
    try:
        engine = create_runtime_engine(get_settings())
        with Session(engine) as database, database.begin():
            dashboard_id = seed_dashboard(database, args.owner)
        print(f"Demo dashboard ready: {dashboard_id}")
    finally:
        if engine is not None:
            engine.dispose()
        if args.use_provisioned_roles:
            if previous_runtime is None:
                os.environ.pop("RUNTIME_DATABASE_URL", None)
            else:
                os.environ["RUNTIME_DATABASE_URL"] = previous_runtime
            get_settings.cache_clear()


if __name__ == "__main__":
    main()
