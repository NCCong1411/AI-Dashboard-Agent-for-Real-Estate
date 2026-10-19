"""Durable interrupts, authorization, and actual backend process restarts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from threading import Event
import uuid

import httpx
from langgraph.types import Command
from langgraph.checkpoint.base import empty_checkpoint
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.checkpoints import PostgresConversationSaver
from app.db.models import Conversation, UsageCall, UsageRecord, User
from test_agent import HoldingPlanner, context, create, send


BACKEND = Path(__file__).resolve().parents[2]


@contextmanager
def backend_process(tmp_path, label):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with (tmp_path / f"{label}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND, env=dict(os.environ, APP_ENV="test", LLM_MODE="mock"),
            stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, f"backend exited; inspect {label}.log"
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail("backend startup timed out")
                    time.sleep(0.05)
                yield client, process.pid
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    process.kill()
            process.wait(timeout=15)


def login_child(client, service, user_id):
    with Session(service.engine) as database:
        username = database.get(User, user_id).username
    response = client.post("/api/auth/login", json={"username": username, "password": "agent integration password"},
                           headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200, response.text


def thread_for(service, conversation):
    with Session(service.engine) as database:
        return database.get(Conversation, uuid.UUID(conversation)).thread_id


def test_actual_backend_restart_restores_interrupt_and_resume_has_no_side_effects(context, tmp_path, monkeypatch):
    _, _, service, users, _ = context
    with backend_process(tmp_path, "before-restart") as (client, first_pid):
        login_child(client, service, users["all"])
        conversation = create(client)
        preview = send(client, conversation, "Tạo tổng quan giao dịch năm 2025").json()
        assert preview["status"] == "awaiting_approval", preview
        before = client.get(f"/api/conversations/{conversation}").json()
    thread = thread_for(service, conversation)
    config = {"configurable": {"thread_id": thread}}
    graph = service.graph_for(users["all"], uuid.UUID(conversation), thread)
    snapshot = asyncio.run(graph.aget_state(config))
    assert snapshot.next == ("await_approval",)
    assert snapshot.tasks[0].interrupts
    assert service.engine.pool.checkedout() == 0
    with backend_process(tmp_path, "after-restart") as (client, second_pid):
        assert first_pid != second_pid
        login_child(client, service, users["all"])
        restored = client.get(f"/api/conversations/{conversation}").json()
        assert restored == before
        assert thread_for(service, conversation) == thread
    with Session(service.engine) as database:
        usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == uuid.UUID(preview["run_id"])))
        assert usage.llm_calls == 1 and usage.query_count == 4
    def unexpected(*args, **kwargs):
        pytest.fail("resume repeated an LLM call or analytics query")
    monkeypatch.setattr(service.nodes, "plan", unexpected)
    monkeypatch.setattr(service.executor, "execute", unexpected)
    graph = service.graph_for(users["all"], uuid.UUID(conversation), thread)
    # Exercise the graph protocol directly. Publication authorization belongs
    # to the approval service, not to this internal resume marker.
    result = asyncio.run(graph.ainvoke(Command(resume={"approved": False}), config, durability="sync"))
    assert result["approval"]["approved"] is False
    assert result["response"]["status"] == "completed"
    assert asyncio.run(graph.aget_state(config)).next == ()


CRASH_WORKER = """
import asyncio, os, sys
from uuid import UUID
from sqlalchemy import create_engine
from app.config import Settings
from app.analytics.executor import QueryExecutor
from app.schemas.agent import MessageRequest
from app.services.conversation import ConversationService
settings = Settings(_env_file=None)
engine = create_engine(os.environ['AUTH_TEST_DATABASE_URL'], hide_parameters=True)
service = ConversationService(engine, settings, QueryExecutor(settings))
user, conversation = UUID(sys.argv[1]), UUID(sys.argv[2])
state = service._begin(user, conversation, MessageRequest(message='Tạo tổng quan giao dịch năm 2025'))
if sys.argv[3] == 'reserved':
    service.usage.reserve(user, UUID(state['run_id']), 100, 100)
if sys.argv[3] == 'preview':
    graph = service.graph_for(user, conversation, state['thread_id'])
    result = asyncio.run(graph.ainvoke(state, {'configurable': {'thread_id': state['thread_id']}, 'recursion_limit': 12}, durability='sync'))
    assert result['response']['status'] == 'awaiting_approval'
os._exit(17)
"""


@pytest.mark.parametrize("phase,expected", [("begin", "failed"), ("reserved", "failed"), ("preview", "awaiting_approval")])
def test_dead_process_run_recovers_without_replaying_work(context, tmp_path, phase, expected):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    process = subprocess.run([sys.executable, "-c", CRASH_WORKER, str(users["all"]), conversation, phase],
                             cwd=BACKEND, env=dict(os.environ, APP_ENV="test", LLM_MODE="mock"),
                             capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert process.returncode == 17, process.stderr
    with Session(service.engine) as database:
        record = database.get(Conversation, uuid.UUID(conversation))
        assert record.status == "running" and record.active_run_id
        run_id, thread = record.active_run_id, record.thread_id
        usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id))
        previous_calls = usage.llm_calls if usage else 0
    with backend_process(tmp_path, f"recover-{phase}") as (remote, _):
        login_child(remote, service, users["all"])
        response = remote.get(f"/api/conversations/{conversation}")
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered["status"] == expected, recovered
        assert remote.get(f"/api/conversations/{conversation}").json() == recovered
    with Session(service.engine) as database:
        record = database.get(Conversation, uuid.UUID(conversation))
        assert record.thread_id == thread and record.active_run_id is None
        usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id))
        assert (usage.llm_calls if usage else 0) == previous_calls
        if phase == "preview":
            assert usage.query_count == 4
            assert recovered["draft_revision"] == 1
            assert recovered["response"]["results"]["totals"]["rows"][0]["transaction_count"] == 97
        else:
            assert recovered["response"]["error"]["code"] == "run_interrupted"
            assert usage is None or usage.query_count == 0
        if phase == "reserved":
            call = database.scalar(select(UsageCall).where(UsageCall.run_id == run_id))
            assert usage.llm_calls == 1 and call.status == "pending"


def test_checkpoint_authorization_precedes_deserialization(context, monkeypatch):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    assert send(client, conversation, "Tổng quan giao dịch").json()["status"] == "awaiting_approval"
    thread = thread_for(service, conversation)
    saver = PostgresConversationSaver(service.engine, uuid.UUID(conversation), users["other"], thread)
    monkeypatch.setattr(saver, "_unpack", lambda _: pytest.fail("unauthorized checkpoint deserialized"))
    with pytest.raises(PermissionError):
        saver.get_tuple({"configurable": {"thread_id": thread}})
    with pytest.raises(PermissionError):
        saver.get_tuple({"configurable": {"thread_id": "another-thread"}})
    login("viewer")
    assert client.get(f"/api/conversations/{conversation}").status_code == 403


def test_checkpoint_pending_writes_retry_and_parent_protocol(context):
    client, login, service, users, _ = context
    login("all")
    conversation = create(client)
    thread = thread_for(service, conversation)
    saver = PostgresConversationSaver(service.engine, uuid.UUID(conversation), users["all"], thread)
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": "protocol"}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"number": 42}
    stored = saver.put(config, checkpoint, {"source": "input", "step": -1, "parents": {}}, {})
    saver.put_writes(stored, [("answer", "first")], "task", "a")
    saver.put_writes(stored, [("answer", "retry")], "task", "a")
    saver.put_writes(stored, [("__error__", "old")], "task", "a")
    saver.put_writes(stored, [("__error__", "new")], "task", "a")
    result = saver.get_tuple(stored)
    assert result.checkpoint["channel_values"] == {"number": 42}
    assert ("task", "answer", "first") in result.pending_writes
    assert ("task", "__error__", "new") in result.pending_writes
    assert len(result.pending_writes) == 2
    successor = empty_checkpoint()
    next_config = saver.put(stored, successor, {"source": "loop", "step": 0, "parents": {}}, {})
    assert saver.get_tuple(next_config).parent_config == stored
    assert len(list(saver.list(config))) == 2
    assert len(list(saver.list(config, filter={"source": "input"}))) == 1
    assert len(list(saver.list(config, before=next_config, limit=1))) == 1


def test_sync_checkpoint_drains_pending_writes_before_provider_wait(context, monkeypatch):
    client, login, service, _, _ = context
    login("all")
    conversation = create(client)
    planner = HoldingPlanner()
    service.nodes.planner = planner
    entered, release = Event(), Event()
    original = PostgresConversationSaver.put_writes
    def slow_context_write(self, config, writes, task_id, task_path=""):
        if any(channel == "approval" for channel, _ in writes):
            entered.set()
            assert release.wait(10), "pending write was not released"
        return original(self, config, writes, task_id, task_path)
    monkeypatch.setattr(PostgresConversationSaver, "put_writes", slow_context_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sending = pool.submit(send, client, conversation, "Tổng quan giao dịch năm 2025")
        try:
            assert entered.wait(5)
            assert not planner.entered.wait(0.2), "provider began before context writes finished"
            release.set()
            assert planner.entered.wait(5)
            assert service.engine.pool.checkedout() == 0
        finally:
            release.set()
            planner.release.set()
        assert sending.result(timeout=15).json()["status"] == "awaiting_approval"
