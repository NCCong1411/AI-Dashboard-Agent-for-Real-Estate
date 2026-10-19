"""Real PostgreSQL/API checks for publication visibility and viewer scope."""

import importlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.analytics.executor import QueryExecutor, get_query_executor
from app.auth.dependencies import database_session
from app.auth.passwords import hash_password
from app.config import Settings, get_settings
from app.db.models import AuditEvent, Dashboard, DashboardVersion, User
from scripts.seed_dashboard import seed_dashboard


@pytest.fixture
def context(monkeypatch):
    value = os.getenv("AUTH_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("requires AUTH_TEST_DATABASE_URL for dashboard API integration")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("dashboard integration requires a separate test database")
    reader_values = {}
    for scope in ("ALL", "CA", "TX"):
        reader_value = os.getenv(f"READER_{scope}_TEST_DATABASE_URL", "")
        if not reader_value:
            pytest.skip("requires three reader test URLs")
        if make_url(reader_value).database != make_url(value).database:
            pytest.fail("dashboard/reader test databases must match")
        reader_values[f"reader_{scope.lower()}_database_url"] = reader_value
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    engine = create_engine(value, hide_parameters=True)
    executor = QueryExecutor(Settings(_env_file=None, app_env="test", **reader_values))
    suffix = uuid.uuid4().hex[:12]
    password = "dashboard integration password"
    accounts = {"builder": ("builder", "ALL"), "all": ("viewer", "ALL"), "ca": ("viewer", "CA")}
    ids = []
    with Session(engine) as database, database.begin():
        for name, (role, scope) in accounts.items():
            user = User(username=f"dash_{name}_{suffix}", role=role, scope_profile=scope, password_hash=hash_password(password))
            database.add(user)
            database.flush()
            ids.append(user.id)
        dashboard_id = seed_dashboard(database, f"dash_builder_{suffix}")
        assert seed_dashboard(database, f"dash_builder_{suffix}") == dashboard_id
        draft = Dashboard(title="PRIVATE DRAFT", owner_user_id=ids[0])
        database.add(draft)
        database.flush()
        draft_id = draft.id

    def db_session():
        with Session(engine) as database:
            yield database

    main = importlib.import_module("app.main")
    main = importlib.reload(main)
    main.app.dependency_overrides[database_session] = db_session
    main.app.dependency_overrides[get_query_executor] = lambda: executor
    with TestClient(main.app) as client:
        yield client, dashboard_id, draft_id, suffix, password, engine, executor
    main.app.dependency_overrides.clear()
    executor.close()
    with Session(engine) as database, database.begin():
        database.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(ids)))
        for dashboard in database.scalars(select(Dashboard).where(Dashboard.owner_user_id.in_(ids))):
            dashboard.current_version_id = None
        database.flush()
        database.execute(delete(Dashboard).where(Dashboard.owner_user_id.in_(ids)))
        database.execute(delete(User).where(User.id.in_(ids)))
    engine.dispose()
    get_settings.cache_clear()


def test_visibility_scope_and_query_contract(context):
    client, dashboard_id, draft_id, suffix, password, _, executor = context
    origin = {"Origin": "http://localhost:3000"}
    base = f"/api/dashboards/{dashboard_id}"
    assert client.get("/api/dashboards").status_code == 401
    assert client.post(base + "/query", json={}, headers=origin).status_code == 401
    for account, expected_count, expected_total in [
        ("builder", 1000, "870798679.64"), ("ca", 135, "107064313.73"), ("all", 1000, "870798679.64"),
    ]:
        assert client.post("/api/auth/login", json={"username": f"dash_{account}_{suffix}", "password": password}, headers=origin).status_code == 200
        listed = client.get("/api/dashboards").json()
        assert str(dashboard_id) in {item["id"] for item in listed}
        assert str(draft_id) not in {item["id"] for item in listed}
        detail = client.get(base)
        assert detail.status_code == 200
        assert ("sql" in detail.json()) == (account == "builder")
        assert "PRIVATE DRAFT" not in detail.text
        for method, path in [(client.get, f"/api/dashboards/{draft_id}"),
                             (client.post, f"/api/dashboards/{draft_id}/query")]:
            response = method(path, **({"json": {}, "headers": origin} if method == client.post else {}))
            assert response.status_code == 404
        response = client.post(base + "/query", json={}, headers=origin)
        assert response.status_code == 200, response.text
        rows = response.json()["results"]["sales"]["rows"]
        assert rows[0]["transaction_count"] == expected_count
        assert rows[0]["sales_value"] == expected_total
        assert response.json()["scope"] == ("CA" if account == "ca" else "ALL")
        for payload in [{"sql": "SELECT 1"}, {"scope": "ALL"}, {"user_id": str(uuid.uuid4())},
                        {"filters": [{"field": "secret", "op": "eq", "value": "x"}]}]:
            assert client.post(base + "/query", json=payload, headers=origin).status_code == 422
        assert client.post(base + "/query", json={}, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/api/dashboards", json={"spec": {}}, headers=origin).status_code == 405
        assert client.put(base, json={"title": "changed"}, headers=origin).status_code == 405
    # One query's limit failure does not discard successful sibling results.
    executor.settings.analytics_max_rows = 1
    response = client.post(base + "/query", json={}, headers=origin).json()
    assert response["results"]["sales"]["status"] == "ok"
    assert response["results"]["by_month"]["error_code"] == "row_limit"


def test_empty_filters_and_incompatible_published_version(context):
    client, dashboard_id, _, suffix, password, engine, _ = context
    origin = {"Origin": "http://localhost:3000"}
    client.post("/api/auth/login", json={"username": f"dash_ca_{suffix}", "password": password}, headers=origin)
    base = f"/api/dashboards/{dashboard_id}"
    response = client.post(base + "/query", headers=origin, json={
        "filters": [{"field": "state", "op": "eq", "value": "TX"}],
    }).json()
    assert all(result["status"] == "no_data" for result in response["results"].values())
    response = client.post(base + "/query", headers=origin, json={
        "date_range": {"start": "2030-01-01", "end_exclusive": "2031-01-01"},
    }).json()
    assert all(result["status"] == "no_data" for result in response["results"].values())
    with Session(engine) as database, database.begin():
        version = database.scalar(select(DashboardVersion).where(DashboardVersion.dashboard_id == dashboard_id))
        version.catalog_version = "incompatible"
    assert client.get(base).status_code == 409
    assert client.post(base + "/query", headers=origin, json={}).status_code == 409
