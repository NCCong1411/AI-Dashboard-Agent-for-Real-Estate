"""Publication, durable receipts, conflicts, and crash/retry correctness."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Dashboard, DashboardVersion, UsageRecord, User
from app.schemas.agent import ApprovalRequest
from app.schemas.dashboard import DashboardSpec
from app.services.approval import ApprovalService
from test_agent import ORIGIN, context, create, send
from test_checkpoint_recovery import BACKEND, backend_process, login_child


def body(preview, decision="approve"):
    return {"decision": decision, **{name: preview[name] for name in ("draft_revision", "draft_hash", "base_version_number")}}


def approve(client, conversation, preview, decision="approve"):
    return client.post(f"/api/conversations/{conversation}/approval", json=body(preview, decision), headers=ORIGIN)


def version_count(service, dashboard_id):
    with Session(service.engine) as database:
        return database.scalar(select(func.count()).select_from(DashboardVersion).where(DashboardVersion.dashboard_id == uuid.UUID(dashboard_id)))


def test_new_draft_hidden_until_approved_then_viewer_reexecutes_own_scope(context):
    client, login, service, _, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch năm 2025").json()
    before = client.get("/api/dashboards").json()
    response = approve(client, conversation, preview)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["checkpoint_synced"] is True and receipt["version_number"] == 1
    dashboard = receipt["dashboard_id"]
    assert all(item["id"] != dashboard for item in before)
    assert approve(client, conversation, preview).json() == receipt
    assert version_count(service, dashboard) == 1
    restored = client.get(f"/api/conversations/{conversation}").json()
    assert restored["status"] == "completed" and restored["approval"] == receipt
    assert len(restored["messages"]) == 3
    login("viewer")
    detail = client.get(f"/api/dashboards/{dashboard}")
    assert detail.status_code == 200 and "sql" not in detail.json()
    assert DashboardSpec.model_validate(detail.json()["spec"]).model_dump(mode="json") == preview["draft"]
    data = client.post(f"/api/dashboards/{dashboard}/query", json={}, headers=ORIGIN).json()
    assert data["scope"] == "CA" and data["results"]["totals"]["rows"][0]["transaction_count"] == 15
    assert client.get(f"/api/conversations/{conversation}").status_code == 403


def test_reject_idempotent_does_not_publish_and_cannot_change_decision(context):
    client, login, service, _, published = context
    login("all")
    conversation = create(client, dashboard_id=str(published))
    preview = send(client, conversation, "Lọc California").json()
    receipt = approve(client, conversation, preview, "reject")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["decision"] == "reject" and receipt.json()["dashboard_id"] is None
    assert receipt.json()["checkpoint_synced"] is True
    assert approve(client, conversation, preview, "reject").json() == receipt.json()
    assert approve(client, conversation, preview).status_code == 409
    assert version_count(service, str(published)) == 1
    assert client.get(f"/api/dashboards/{published}").json()["version_number"] == 1


def test_stale_revision_hash_base_and_edited_draft_are_rejected(context):
    client, login, _, _, _ = context
    login("all")
    conversation = create(client)
    first = send(client, conversation, "Tổng quan giao dịch năm 2025").json()
    for field, value in [("draft_revision", 9), ("draft_hash", "0" * 64), ("base_version_number", 1)]:
        request = body(first)
        request[field] = value
        assert client.post(f"/api/conversations/{conversation}/approval", json=request, headers=ORIGIN).status_code == 409
    second = send(client, conversation, "Lọc California").json()
    assert approve(client, conversation, first).status_code == 409
    assert approve(client, conversation, second).json()["checkpoint_synced"] is True
    third = send(client, conversation, "Bỏ lọc bang").json()
    assert third["base_version_number"] == 1 and third["draft_revision"] == 3
    assert approve(client, conversation, second).status_code == 409
    assert approve(client, conversation, third).json()["version_number"] == 2
    # Intentional reversion to an earlier content hash is a new draft/version.
    fourth = send(client, conversation, "Lọc California").json()
    assert fourth["draft_hash"] == second["draft_hash"]
    assert approve(client, conversation, fourth).json()["version_number"] == 3


def test_competing_conversations_keep_viewer_old_version_until_approval(context):
    client, login, service, _, published = context
    login("all")
    one = create(client, dashboard_id=str(published))
    two = create(client, dashboard_id=str(published))
    first = send(client, one, "Lọc California").json()
    second = send(client, two, "Lọc Texas").json()
    login("viewer")
    old = client.get(f"/api/dashboards/{published}").json()
    assert old["version_number"] == 1
    assert old["spec"] != first["draft"]
    login("all")
    accepted = approve(client, one, first)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["version_number"] == 2
    assert approve(client, two, second).status_code == 409
    assert version_count(service, str(published)) == 2
    login("viewer")
    new = client.get(f"/api/dashboards/{published}").json()
    assert new["version_number"] == 2
    assert DashboardSpec.model_validate(new["spec"]).model_dump(mode="json") == first["draft"]


def test_concurrent_duplicate_approval_creates_one_version_and_audit(context):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch").json()
    approval = ApprovalService(service)
    request = ApprovalRequest.model_validate(body(preview))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: asyncio.run(approval.decide(users["all"], uuid.UUID(conversation), request)), range(4)))
    assert len({item.dashboard_id for item in results}) == 1
    receipt = approve(client, conversation, preview).json()
    assert receipt["checkpoint_synced"] is True
    assert version_count(service, receipt["dashboard_id"]) == 1
    with Session(service.engine) as database:
        assert database.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "draft_approved", AuditEvent.target_id == conversation)) == 1
        usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == uuid.UUID(preview["run_id"])))
        assert usage.llm_calls == 1 and usage.query_count == 4


def test_approval_rbac_origin_scope_and_strict_body(context):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch").json()
    path = f"/api/conversations/{conversation}/approval"
    assert client.post(path, json=body(preview), headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post(path, json={**body(preview), "thread_id": "forged"}, headers=ORIGIN).status_code == 422
    login("other")
    assert approve(client, conversation, preview).status_code == 404
    login("viewer")
    assert approve(client, conversation, preview).status_code == 403
    login("all")
    with Session(service.engine) as database, database.begin():
        database.get(User, users["all"]).scope_profile = "TX"
    assert approve(client, conversation, preview).status_code == 409
    assert client.get(f"/api/conversations/{conversation}").json()["approval"] is None


APPROVAL_CRASH_WORKER = """
import json, os, sys
from uuid import UUID
from sqlalchemy import create_engine
from app.config import Settings
from app.analytics.executor import QueryExecutor
from app.schemas.agent import ApprovalRequest
from app.services.conversation import ConversationService
from app.services.approval import ApprovalService
settings = Settings(_env_file=None)
engine = create_engine(os.environ['AUTH_TEST_DATABASE_URL'], hide_parameters=True)
service = ConversationService(engine, settings, QueryExecutor(settings))
ApprovalService(service)._commit(UUID(sys.argv[1]), UUID(sys.argv[2]), ApprovalRequest.model_validate(json.loads(sys.argv[3])))
os._exit(17)
"""


def test_real_process_crash_after_commit_then_restart_retry_publishes_once(context, tmp_path):
    import json
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch năm 2025").json()
    process = subprocess.run([sys.executable, "-c", APPROVAL_CRASH_WORKER, str(users["all"]), conversation, json.dumps(body(preview))],
        cwd=BACKEND, env=dict(os.environ, APP_ENV="test", LLM_MODE="mock"), capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert process.returncode == 17, process.stderr
    state = client.get(f"/api/conversations/{conversation}").json()
    receipt = state["approval"]
    assert receipt["checkpoint_synced"] is False
    assert version_count(service, receipt["dashboard_id"]) == 1
    assert send(client, conversation, "Lọc California").status_code == 409
    with backend_process(tmp_path, "approval-retry") as (remote, _):
        login_child(remote, service, users["all"])
        replay = approve(remote, conversation, preview)
        assert replay.status_code == 200, replay.text
        assert replay.json()["checkpoint_synced"] is True
        assert replay.json()["dashboard_id"] == receipt["dashboard_id"]
        assert approve(remote, conversation, preview).json() == replay.json()
    assert version_count(service, receipt["dashboard_id"]) == 1


def test_resume_completed_but_ack_lost_retry_does_not_replay_query(context, monkeypatch):
    client, login, service, _, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch").json()
    original = ApprovalService._acknowledge
    monkeypatch.setattr(ApprovalService, "_acknowledge", lambda *args: (_ for _ in ()).throw(RuntimeError("simulated lost ack")))
    first = approve(client, conversation, preview).json()
    assert first["checkpoint_synced"] is False
    monkeypatch.setattr(ApprovalService, "_acknowledge", original)
    monkeypatch.setattr(service.nodes, "plan", lambda *args: pytest.fail("approval replayed LLM"))
    monkeypatch.setattr(service.executor, "execute", lambda *args: pytest.fail("approval replayed query"))
    second = approve(client, conversation, preview).json()
    assert second["checkpoint_synced"] is True and second["dashboard_id"] == first["dashboard_id"]
    assert version_count(service, first["dashboard_id"]) == 1


def test_publish_and_receipt_roll_back_together_if_audit_fails(context, monkeypatch):
    import app.services.approval as module
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    preview = send(client, conversation, "Tổng quan giao dịch").json()
    with Session(service.engine) as database:
        before = database.scalar(select(func.count()).select_from(Dashboard).where(Dashboard.owner_user_id == users["all"]))
    with monkeypatch.context() as patch:
        def fail_audit(*args, **kwargs):
            raise RuntimeError("simulated audit failure after version flush")
        patch.setattr(module, "AuditEvent", fail_audit)
        with pytest.raises(RuntimeError, match="audit failure"):
            ApprovalService(service)._commit(users["all"], uuid.UUID(conversation), ApprovalRequest.model_validate(body(preview)))
    with Session(service.engine) as database:
        assert database.scalar(select(func.count()).select_from(Dashboard).where(Dashboard.owner_user_id == users["all"])) == before
    current = client.get(f"/api/conversations/{conversation}").json()
    assert current["status"] == "awaiting_approval" and current["approval"] is None
    assert approve(client, conversation, preview).json()["checkpoint_synced"] is True
