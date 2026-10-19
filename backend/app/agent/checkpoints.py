"""LangGraph checkpoint protocol backed by PostgreSQL app metadata.

Uses the existing SQLAlchemy/psycopg runtime pool with short transactions and
no runtime DDL. A saver is bound to one server-authorized conversation/owner;
every operation rechecks the binding before reading or deserializing state.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future
from threading import Lock
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP, get_checkpoint_metadata, writes_sort_key
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import AgentCheckpoint, Conversation, GraphCheckpointWrite, User


class PostgresConversationSaver(BaseCheckpointSaver[int]):
    def __init__(self, engine: Engine, conversation_id: UUID, owner_id: UUID, thread_id: str):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.engine, self.conversation_id, self.owner_id, self.thread_id = engine, conversation_id, owner_id, thread_id
        self._writes_lock = Lock()
        self._pending_writes: list[Future] = []

    def _keys(self, config):
        configurable = config.get("configurable", {})
        if configurable.get("thread_id") != self.thread_id:
            raise PermissionError("checkpoint thread does not match the bound conversation")
        namespace = configurable.get("checkpoint_ns", "")
        if not isinstance(namespace, str) or len(namespace) > 120:
            raise ValueError("checkpoint namespace is invalid")
        return "graph:" + namespace, configurable.get("checkpoint_id")

    def _authorize(self, database: Session):
        allowed = database.scalar(select(Conversation.id).join(User, User.id == Conversation.owner_user_id).where(
            Conversation.id == self.conversation_id, Conversation.owner_user_id == self.owner_id,
            Conversation.thread_id == self.thread_id, User.role == "builder", User.is_active.is_(True),
        ))
        if allowed is None:
            raise PermissionError("checkpoint conversation is not accessible")

    def _pack(self, value):
        kind, data = self.serde.dumps_typed(value)
        return {"type": kind, "data": base64.b64encode(data).decode("ascii")}

    def _unpack(self, value):
        return self.serde.loads_typed((value["type"], base64.b64decode(value["data"], validate=True)))

    def _config(self, namespace, checkpoint_id):
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": namespace.removeprefix("graph:"), "checkpoint_id": checkpoint_id}}

    def get_tuple(self, config):
        namespace, checkpoint_id = self._keys(config)
        with Session(self.engine) as database, database.begin():
            self._authorize(database)
            statement = select(AgentCheckpoint).where(
                AgentCheckpoint.conversation_id == self.conversation_id, AgentCheckpoint.thread_id == self.thread_id,
                AgentCheckpoint.checkpoint_namespace == namespace,
            )
            if checkpoint_id is not None:
                statement = statement.where(AgentCheckpoint.checkpoint_id == checkpoint_id)
            record = database.scalar(statement.order_by(AgentCheckpoint.checkpoint_id.desc()).limit(1))
            if record is None:
                return None
            payload, identifier = record.payload, record.checkpoint_id
            writes = list(database.scalars(select(GraphCheckpointWrite).where(
                GraphCheckpointWrite.conversation_id == self.conversation_id, GraphCheckpointWrite.thread_id == self.thread_id,
                GraphCheckpointWrite.checkpoint_namespace == namespace, GraphCheckpointWrite.checkpoint_id == identifier,
            )))
            writes.sort(key=lambda item: writes_sort_key(item.task_path, item.task_id, item.write_index))
            return CheckpointTuple(
                config=self._config(namespace, identifier), checkpoint=self._unpack(payload["checkpoint"]),
                metadata=self._unpack(payload["metadata"]),
                parent_config=self._config(namespace, payload["parent"]) if payload.get("parent") else None,
                pending_writes=[(item.task_id, item.channel, self._unpack(item.payload)) for item in writes],
            )

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    def put(self, config, checkpoint, metadata, new_versions):
        namespace, parent = self._keys(config)
        payload = {"checkpoint": self._pack(checkpoint), "metadata": self._pack(get_checkpoint_metadata(config, metadata)), "parent": parent}
        with Session(self.engine) as database, database.begin():
            self._authorize(database)
            statement = insert(AgentCheckpoint).values(
                conversation_id=self.conversation_id, thread_id=self.thread_id, checkpoint_namespace=namespace,
                checkpoint_id=checkpoint["id"], payload=payload,
            ).on_conflict_do_update(index_elements=["thread_id", "checkpoint_namespace", "checkpoint_id"], set_={"payload": payload})
            database.execute(statement)
        return self._config(namespace, checkpoint["id"])

    async def aput(self, config, checkpoint, metadata, new_versions):
        # LangGraph's sync durability waits for aput, but schedules pending
        # writes separately. Drain writes registered for the previous step
        # before advancing to a node that may await the provider/user.
        with self._writes_lock:
            pending, self._pending_writes = self._pending_writes, []
        if pending:
            await asyncio.gather(*(asyncio.shield(asyncio.wrap_future(item)) for item in pending))
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        namespace, checkpoint_id = self._keys(config)
        if not checkpoint_id:
            raise ValueError("intermediate writes require a checkpoint ID")
        with Session(self.engine) as database, database.begin():
            self._authorize(database)
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                values = dict(conversation_id=self.conversation_id, thread_id=self.thread_id, checkpoint_namespace=namespace,
                              checkpoint_id=checkpoint_id, task_id=task_id, write_index=write_index,
                              channel=channel, task_path=task_path, payload=self._pack(value))
                statement = insert(GraphCheckpointWrite).values(**values)
                keys = ["thread_id", "checkpoint_namespace", "checkpoint_id", "task_id", "write_index"]
                if write_index < 0:
                    statement = statement.on_conflict_do_update(index_elements=keys,
                        set_={"channel": channel, "task_path": task_path, "payload": values["payload"]})
                else:
                    statement = statement.on_conflict_do_nothing(index_elements=keys)
                database.execute(statement)

    def aput_writes(self, config, writes, task_id, task_path=""):
        # Register at invocation, before the background coroutine is scheduled.
        # A concurrent Future also permits sequential reuse across event loops.
        completed = Future()
        with self._writes_lock:
            self._pending_writes.append(completed)
        async def persist():
            try:
                await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)
            except BaseException as error:
                completed.set_exception(error)
                raise
            else:
                completed.set_result(None)
        return persist()

    def list(self, config, *, filter=None, before=None, limit=None):
        namespace, _ = self._keys(config or {"configurable": {"thread_id": self.thread_id}})
        before_id = self._keys(before)[1] if before else None
        with Session(self.engine) as database, database.begin():
            self._authorize(database)
            statement = select(AgentCheckpoint.checkpoint_namespace, AgentCheckpoint.checkpoint_id).where(
                AgentCheckpoint.conversation_id == self.conversation_id, AgentCheckpoint.thread_id == self.thread_id,
                AgentCheckpoint.checkpoint_namespace == namespace if config else AgentCheckpoint.checkpoint_namespace.startswith("graph:"),
            )
            if before_id:
                statement = statement.where(AgentCheckpoint.checkpoint_id < before_id)
            identities = list(database.execute(statement.order_by(AgentCheckpoint.checkpoint_id.desc())))
        emitted = 0
        for ns, identifier in identities:
            item = self.get_tuple(self._config(ns, identifier))
            if item is None or filter and any(item.metadata.get(key) != value for key, value in filter.items()):
                continue
            if limit is not None and emitted >= limit:
                break
            emitted += 1
            yield item

    async def alist(self, config, *, filter=None, before=None, limit=None):
        items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    def delete_thread(self, thread_id):
        self._keys({"configurable": {"thread_id": thread_id}})
        with Session(self.engine) as database, database.begin():
            self._authorize(database)
            database.execute(delete(GraphCheckpointWrite).where(GraphCheckpointWrite.conversation_id == self.conversation_id))
            database.execute(delete(AgentCheckpoint).where(AgentCheckpoint.conversation_id == self.conversation_id,
                                                          AgentCheckpoint.checkpoint_namespace.startswith("graph:")))

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)
