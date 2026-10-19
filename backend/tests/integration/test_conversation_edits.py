from copy import deepcopy
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agent.mock import MockPlanner
from app.db.models import UsageRecord, User
from test_agent import context, create, send


def usage_count(service, response):
    with Session(service.engine) as database:
        return database.scalar(select(UsageRecord.query_count).where(UsageRecord.run_id == uuid.UUID(response["run_id"])))


def test_five_edits_keep_ids_content_and_reuse_only_unchanged_plans(context):
    client, login, service, _, _ = context
    login("all")
    observed = []
    class RecordingPlanner:
        async def generate(self, *args, **kwargs):
            observed.append(kwargs.get("history", []))
            return await MockPlanner().generate(*args, **kwargs)
    service.nodes.planner = RecordingPlanner()
    conversation = create(client)
    initial = send(client, conversation, "Tạo tổng quan giao dịch năm 2025").json()
    assert initial["results"]["totals"]["rows"][0]["transaction_count"] == 97
    initial_widgets = initial["draft"]["widgets"]
    filtered = send(client, conversation, "Lọc California").json()
    assert filtered["status"] == "awaiting_approval", filtered
    assert filtered["draft"]["widgets"] == initial_widgets
    assert filtered["results"]["totals"]["rows"][0]["transaction_count"] == 15
    assert usage_count(service, filtered) == 4
    reformatted = send(client, conversation, "Đổi biểu đồ theo loại bất động sản thành cột ngang").json()
    assert usage_count(service, reformatted) == 0
    assert len(reformatted["reused_query_ids"]) == 4
    assert reformatted["results"] == filtered["results"]
    old = {widget["id"]: widget for widget in filtered["draft"]["widgets"]}
    for widget in reformatted["draft"]["widgets"]:
        assert widget["id"] in old
        if widget.get("dimension") == "property_type":
            assert widget["chart"] == "horizontal_bar"
        else:
            assert widget == old[widget["id"]]
    added = send(client, conversation, "Thêm số bất động sản có giao dịch").json()
    assert added["status"] == "awaiting_approval", added
    assert len(added["draft"]["widgets"]) == 7
    assert added["results"]["totals"]["rows"][0]["sold_property_count"] == 7
    assert usage_count(service, added) == 1 and len(added["reused_query_ids"]) == 3
    removed = send(client, conversation, "Bỏ biểu đồ theo bang").json()
    assert len(removed["draft"]["widgets"]) == 6
    assert not any(widget.get("dimension") == "state" for widget in removed["draft"]["widgets"])
    assert usage_count(service, removed) == 0
    changed_period = send(client, conversation, "Đổi khoảng thời gian năm 2024").json()
    assert changed_period["status"] == "awaiting_approval", changed_period
    assert changed_period["draft"]["widgets"] == removed["draft"]["widgets"]
    assert usage_count(service, changed_period) == 3
    assert changed_period["draft_revision"] == 6
    with service.executor._engine("CA").connect() as connection:
        expected = connection.execute(text("SELECT COUNT(transaction_id) FROM public.transactions WHERE transaction_date >= '2024-01-01' AND transaction_date < '2025-01-01'")).scalar_one()
    assert changed_period["results"]["totals"]["rows"][0]["transaction_count"] == expected
    reset = send(client, conversation, "Bỏ lọc bang").json()
    assert all(query["plan"]["filters"] == [] for query in reset["draft"]["queries"])
    all_time = send(client, conversation, "Đổi về toàn bộ thời gian").json()
    assert all_time["results"]["totals"]["rows"][0]["transaction_count"] == 1000
    assert all_time["draft_revision"] == 8
    assert all(len(history) <= 8 and all(len(item["content"]) <= 500 for item in history) for history in observed)
    before = deepcopy(all_time["draft"])
    clarification = send(client, conversation, "Đổi biểu đồ này thành cột ngang").json()
    assert clarification["status"] == "needs_clarification" and usage_count(service, clarification) == 0
    restored = client.get(f"/api/conversations/{conversation}").json()
    assert restored["draft"] == before and restored["draft_revision"] == 8
    assert restored["preview"]["results"] == all_time["results"]


def test_scope_change_invalidates_every_cached_query(context):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    send(client, conversation, "Tổng quan giao dịch năm 2025")
    with Session(service.engine) as database, database.begin():
        database.get(User, users["all"]).scope_profile = "TX"
    response = send(client, conversation, "Đổi biểu đồ theo loại bất động sản thành cột ngang").json()
    assert response["status"] == "awaiting_approval", response
    assert response["scope"] == "TX" and response["reused_query_ids"] == []
    assert usage_count(service, response) == 4
    assert response["results"]["totals"]["rows"][0]["transaction_count"] == 23
