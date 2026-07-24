from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import yuxi.services.agent_run_service as agent_run_service
from yuxi.services.input_message_service import (
    build_chat_input_message,
    build_chat_input_message_from_openai_content,
    restore_chat_input_message,
)


def _chat_input(content: str, image_content: str | None = None):
    return build_chat_input_message(content, image_content)


def _sse_data(chunk: str) -> dict:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"SSE chunk has no data line: {chunk}")


def test_openai_content_parts_build_and_restore_multimodal_message():
    input_message = build_chat_input_message_from_openai_content(
        [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
        ]
    )

    assert input_message.content == "看图"
    assert input_message.message_type == "multimodal_image"
    assert input_message.image_content is None
    raw_message = input_message.raw_message()
    assert raw_message["content"][1]["image_url"]["url"] == "https://example.test/image.png"

    restored = restore_chat_input_message(
        content=input_message.content, image_content=None, metadata={"raw_message": raw_message}
    )
    assert restored.message_type == "multimodal_image"
    assert restored.require_langchain_message().content == raw_message["content"]


def test_prepare_run_input_message_keeps_invocation_meta_namespaced():
    input_message = agent_run_service._prepare_run_input_message(
        run_type="chat",
        input_message=build_chat_input_message("hello"),
        resume=None,
        request_id="req-1",
        model_spec="provider:model",
        meta={
            "source": "agent_call",
            "agent_invocation_meta": {"trace_id": "trace-1"},
            "evaluation": {"dataset_name": "legacy-top-level"},
            "custom_variables": {"system_prompt": "legacy"},
        },
    )

    assert input_message.extra_metadata["source"] == "agent_call"
    assert input_message.extra_metadata["agent_invocation_meta"] == {"trace_id": "trace-1"}
    assert input_message.require_langchain_message().id == "request:req-1"
    assert input_message.raw_message()["id"] == "request:req-1"
    assert "evaluation" not in input_message.extra_metadata
    assert "custom_variables" not in input_message.extra_metadata


def test_restore_chat_input_message_adds_stable_id_to_legacy_payload():
    """旧消息缺少 LangGraph ID 时按 request_id 恢复，重试不会重复追加。"""
    restored = restore_chat_input_message(
        content="hello",
        image_content=None,
        metadata={
            "request_id": "legacy-request",
            "raw_message": {"type": "human", "content": "hello"},
        },
    )

    assert restored.require_langchain_message().id == "request:legacy-request"


def _progress_event(seq: str, chunks: list[dict]) -> dict:
    return {
        "seq": seq,
        "event_type": "messages",
        "payload": {
            "schema_version": 1,
            "run_id": "run-1",
            "thread_id": "thread-1",
            "event": "messages",
            "payload": {"items": chunks},
            "created_at": "2026-06-30T00:00:00+00:00",
        },
        "ts": 1700000000000,
    }


def _progress_chunk(stream_event: dict) -> dict:
    return {"status": "loading", "stream_event": stream_event}


@pytest.mark.asyncio
async def test_get_agent_run_progress_returns_empty_progress_for_empty_events(monkeypatch: pytest.MonkeyPatch):
    async def fake_list_recent_run_stream_events(run_id: str, *, limit: int):
        assert run_id == "run-1"
        assert limit == agent_run_service.RUN_PROGRESS_RECENT_EVENT_SCAN_LIMIT
        return []

    monkeypatch.setattr(agent_run_service, "list_recent_run_stream_events", fake_list_recent_run_stream_events)

    assert await agent_run_service.get_agent_run_progress("run-1") == {"last_seq": "0-0", "messages": []}


@pytest.mark.asyncio
async def test_get_agent_run_progress_extracts_recent_message_delta_events(monkeypatch: pytest.MonkeyPatch):
    async def fake_list_recent_run_stream_events(run_id: str, *, limit: int):
        del run_id, limit
        return [
            _progress_event(
                "2-0",
                [
                    _progress_chunk({"type": "message_delta", "message_id": "msg-1", "content": " world"}),
                    _progress_chunk({"type": "message_delta", "message_id": "msg-2", "content": "new"}),
                ],
            ),
            _progress_event(
                "1-0",
                [_progress_chunk({"type": "message_delta", "message_id": "msg-1", "content": "hello"})],
            ),
        ]

    monkeypatch.setattr(agent_run_service, "list_recent_run_stream_events", fake_list_recent_run_stream_events)

    progress = await agent_run_service.get_agent_run_progress("run-1")

    assert progress["last_seq"] == "2-0"
    assert progress["messages"] == [
        {
            "seq": "1-0",
            "kind": "assistant_message",
            "message_id": "msg-1",
            "content": "hello",
        },
        {
            "seq": "2-0",
            "kind": "assistant_message",
            "message_id": "msg-1",
            "content": "world",
        },
        {
            "seq": "2-0",
            "kind": "assistant_message",
            "message_id": "msg-2",
            "content": "new",
        },
    ]


@pytest.mark.asyncio
async def test_get_agent_run_progress_keeps_latest_three_readable_items(monkeypatch: pytest.MonkeyPatch):
    events = [
        _progress_event(
            f"{seq}-0",
            [_progress_chunk({"type": "message_delta", "message_id": f"msg-{seq}", "content": f"message-{seq}"})],
        )
        for seq in range(4, 0, -1)
    ]

    async def fake_list_recent_run_stream_events(run_id: str, *, limit: int):
        del run_id, limit
        return events

    monkeypatch.setattr(agent_run_service, "list_recent_run_stream_events", fake_list_recent_run_stream_events)

    progress = await agent_run_service.get_agent_run_progress("run-1")

    assert progress["last_seq"] == "4-0"
    assert [item["message_id"] for item in progress["messages"]] == ["msg-2", "msg-3", "msg-4"]


@pytest.mark.asyncio
async def test_get_agent_run_progress_extracts_tool_call_events(monkeypatch: pytest.MonkeyPatch):
    async def fake_list_recent_run_stream_events(run_id: str, *, limit: int):
        del run_id, limit
        return [
            _progress_event(
                "3-0",
                [
                    _progress_chunk(
                        {
                            "type": "tool_call",
                            "message_id": "msg-3",
                            "tool_call_id": "call-3",
                            "name": "write_file",
                            "args": {"path": "/home/gem/user-data/outputs/report.md"},
                        }
                    )
                ],
            ),
            _progress_event(
                "2-0",
                [
                    _progress_chunk(
                        {
                            "type": "tool_call_delta",
                            "message_id": "msg-2",
                            "tool_call_id": "call-2",
                            "name": "read_file",
                            "args_delta": '{"path":',
                        }
                    )
                ],
            ),
        ]

    monkeypatch.setattr(agent_run_service, "list_recent_run_stream_events", fake_list_recent_run_stream_events)

    progress = await agent_run_service.get_agent_run_progress("run-1")

    assert progress["messages"][0]["kind"] == "tool_call_delta"
    assert progress["messages"][0]["tool_call_id"] == "call-2"
    assert progress["messages"][0]["content"] == "正在准备工具 read_file"
    assert progress["messages"][1]["kind"] == "tool_call"
    assert progress["messages"][1]["tool_call_id"] == "call-3"
    assert progress["messages"][1]["content"] == "调用工具 write_file"


class _FakeContext:
    def __init__(self):
        self.model = "agent-default-model"
        self.tool_approval_mode = "default"

    def update_from_dict(self, data: dict):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


class _FakeBackend:
    context_schema = _FakeContext


class _UserResult:
    def scalar_one_or_none(self):
        return SimpleNamespace(uid="user-1", role="user")


class _CreateRunDb:
    def __init__(
        self,
        *,
        message_id: int = 10,
        active_run: SimpleNamespace | None = None,
        active_run_after_rollback: SimpleNamespace | None = None,
        existing_run: SimpleNamespace | None = None,
        existing_run_after_rollback: SimpleNamespace | None = None,
        runs_by_id: dict[str, SimpleNamespace] | None = None,
        latest_run: SimpleNamespace | None = None,
        raise_create_integrity_error: bool = False,
    ):
        self.added = []
        self.deleted = []
        self.committed = False
        self.created_run = None
        self.created_run_kwargs = None
        self.enqueued: list[tuple[str, str, str]] = []
        self.order: list[str] = []
        self.request_id_lookups: list[str] = []
        self.active_run_lookup = None
        self.active_run = active_run
        self.active_run_after_rollback = active_run_after_rollback
        self.existing_run = existing_run
        self.existing_run_after_rollback = existing_run_after_rollback
        self.runs_by_id = runs_by_id or {}
        self.latest_run = latest_run
        self.raise_create_integrity_error = raise_create_integrity_error
        self._message_id = message_id

    async def execute(self, stmt):
        del stmt
        return _UserResult()

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.order.append("flush")
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = self._message_id

    async def commit(self):
        self.order.append("commit")
        self.committed = True

    async def rollback(self):
        self.order.append("rollback")

    async def delete(self, item):
        self.deleted.append(item)
        self.order.append("delete")

    def begin_nested(self):
        db = self

        class NestedTransaction:
            async def __aenter__(self):
                db.order.append("begin_nested")
                return self

            async def __aexit__(self, exc_type, exc, tb):
                if exc_type is agent_run_service.IntegrityError:
                    db.order.append("rollback_savepoint")
                else:
                    db.order.append("release_savepoint")
                return False

        return NestedTransaction()


class _CreateRunRepo:
    def __init__(self, db_session):
        self.db = db_session

    async def get_run_by_request_id(self, request_id: str):
        self.db.request_id_lookups.append(request_id)
        if "rollback_savepoint" in self.db.order and self.db.existing_run_after_rollback:
            return self.db.existing_run_after_rollback
        return self.db.existing_run

    async def get_active_run_by_thread_for_user(self, *, agent_slug: str, conversation_thread_id: str, uid: str):
        self.db.active_run_lookup = {
            "agent_slug": agent_slug,
            "conversation_thread_id": conversation_thread_id,
            "uid": uid,
        }
        if "rollback_savepoint" in self.db.order and self.db.active_run_after_rollback:
            return self.db.active_run_after_rollback
        return self.db.active_run

    async def get_run_for_user(self, run_id: str, uid: str):
        assert uid == "user-1"
        return self.db.runs_by_id.get(run_id)

    async def get_latest_chat_or_resume_run(self, *, uid: str, agent_slug: str, conversation_thread_id: str):
        assert uid == "user-1"
        assert agent_slug == "default"
        assert conversation_thread_id == "thread-1"
        return self.db.latest_run or self.db.runs_by_id.get("parent-run")

    async def create_run(self, **kwargs):
        self.db.created_run_kwargs = kwargs
        if self.db.raise_create_integrity_error:
            raise agent_run_service.IntegrityError("insert agent_run", kwargs, Exception("duplicate request_id"))
        self.db.created_run = SimpleNamespace(
            id=kwargs["run_id"],
            conversation_thread_id=kwargs["conversation_thread_id"],
            agent_slug=kwargs["agent_slug"],
            status="pending",
            request_id=kwargs["request_id"],
            uid=kwargs["uid"],
            run_type=kwargs["run_type"],
            created_by_run_id=kwargs.get("created_by_run_id"),
            subagent_thread_relation_id=kwargs.get("subagent_thread_relation_id"),
        )
        return self.db.created_run


@pytest.mark.asyncio
async def test_stream_agent_run_events_emits_error_on_db_error(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class BrokenRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            raise RuntimeError("db down")

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", BrokenRepo)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert '"reason": "db_error"' in chunks[0]


@pytest.mark.asyncio
async def test_stream_agent_run_events_reads_redis_and_ends_on_end_event(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", conversation_thread_id="thread-1")

    calls = {"count": 0}

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {
                    "seq": "1700000000000-0",
                    "event_type": "messages",
                    "payload": {
                        "schema_version": 1,
                        "run_id": "run-1",
                        "thread_id": "thread-1",
                        "event": "messages",
                        "payload": {"items": [{"status": "loading", "response": "你"}]},
                        "created_at": "2026-05-27T00:00:00+00:00",
                    },
                    "ts": 1700000000000,
                },
                {
                    "seq": "1700000000001-0",
                    "event_type": "end",
                    "payload": {
                        "schema_version": 1,
                        "run_id": "run-1",
                        "thread_id": "thread-1",
                        "event": "end",
                        "payload": {"status": "completed"},
                        "created_at": "2026-05-27T00:00:01+00:00",
                    },
                    "ts": 1700000000001,
                },
            ]
        return []

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "SSE_POLL_INTERVAL_SECONDS", 0)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert chunks[0].startswith("event: messages")
    assert "id: 1700000000000-0" in chunks[0]
    assert chunks[-1].startswith("event: end")
    assert "id: 1700000000001-0" in chunks[-1]


@pytest.mark.asyncio
async def test_stream_agent_run_events_compacts_verbose_false(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", conversation_thread_id="thread-1")

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return [
            {
                "seq": "1700000000000-0",
                "event_type": "metadata",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "metadata",
                    "payload": {
                        "request_id": "req-1",
                        "agent_slug": "deep-research",
                        "backend_id": "ChatbotAgent",
                        "uid": "user-1",
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000000,
            },
            {
                "seq": "1700000000001-0",
                "event_type": "custom",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "custom",
                    "payload": {
                        "name": "yuxi.init",
                        "chunk": {
                            "request_id": "req-1",
                            "response": None,
                            "thread_id": "thread-1",
                            "status": "init",
                            "meta": {"query": "写一个冒泡排序", "uid": "user-1"},
                            "msg": {
                                "role": "user",
                                "content": "写一个冒泡排序",
                                "type": "human",
                                "image_content": "base64-image-data",
                                "extra_metadata": {
                                    "request_id": "req-1",
                                    "attachments": [],
                                    "debug": "drop-me",
                                },
                            },
                        },
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000001,
            },
            {
                "seq": "1700000000002-0",
                "event_type": "custom",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "custom",
                    "payload": {
                        "name": "yuxi.agent_state",
                        "chunk": {
                            "request_id": "req-1",
                            "response": None,
                            "thread_id": "thread-1",
                            "status": "agent_state",
                            "agent_state": {
                                "todos": [],
                                "files": {},
                                "artifacts": [],
                                "subagent_runs": [],
                            },
                            "meta": {"uid": "user-1"},
                        },
                        "agent_state": {
                            "todos": [],
                            "files": {},
                            "artifacts": [],
                            "subagent_runs": [],
                        },
                    },
                    "created_at": "2026-05-27T00:00:00+00:00",
                },
                "ts": 1700000000002,
            },
            {
                "seq": "1700000000003-0",
                "event_type": "messages",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "messages",
                    "payload": {
                        "items": [
                            {
                                "request_id": "req-1",
                                "response": "你",
                                "thread_id": "thread-1",
                                "status": "loading",
                                "stream_event": {
                                    "type": "tool_call",
                                    "message_id": "msg-1",
                                    "tool_call_id": "call-1",
                                    "name": "ls",
                                    "args": {"path": "/home/gem/user-data/outputs"},
                                    "thread_id": "thread-1",
                                    "namespace": [],
                                },
                                "metadata": {
                                    "langfuse_user_id": "user-1",
                                    "langgraph_checkpoint_ns": "model:checkpoint",
                                },
                            }
                        ]
                    },
                    "created_at": "2026-05-27T00:00:01+00:00",
                },
                "ts": 1700000000003,
            },
            {
                "seq": "1700000000004-0",
                "event_type": "end",
                "payload": {
                    "schema_version": 1,
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "event": "end",
                    "payload": {
                        "status": "completed",
                        "chunk": {"status": "finished", "request_id": "req-1", "meta": {"uid": "user-1"}},
                    },
                    "created_at": "2026-05-27T00:00:02+00:00",
                },
                "ts": 1700000000004,
            },
        ]

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert len(chunks) == 3

    init_data = _sse_data(chunks[0])
    init_chunk = init_data["payload"]["chunk"]
    assert init_data["request_id"] == "req-1"
    assert init_data["payload"]["name"] == "yuxi.init"
    assert "meta" not in init_chunk
    assert "request_id" not in init_chunk
    assert "response" not in init_chunk
    assert "thread_id" not in init_chunk
    assert "image_content" not in init_chunk["msg"]
    assert "extra_metadata" not in init_chunk["msg"]

    message_data = _sse_data(chunks[1])
    message_chunk = message_data["payload"]["items"][0]
    assert message_data["request_id"] == "req-1"
    assert "request_id" not in message_chunk
    assert "metadata" not in message_chunk
    assert "response" not in message_chunk
    assert "thread_id" not in message_chunk
    assert message_chunk["stream_event"]["tool_call_id"] == "call-1"
    assert "thread_id" not in message_chunk["stream_event"]
    assert "namespace" not in message_chunk["stream_event"]

    end_data = _sse_data(chunks[2])
    assert end_data["request_id"] == "req-1"
    assert end_data["payload"]["status"] == "completed"
    assert "request_id" not in end_data["payload"]["chunk"]
    assert "meta" not in end_data["payload"]["chunk"]


@pytest.mark.asyncio
async def test_stream_agent_run_events_compact_fallback_end_keeps_request_id(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(status="completed", conversation_thread_id="thread-1", request_id="req-1")

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return []

    async def fake_get_last_run_stream_seq(run_id: str):
        del run_id
        return "1700000000004-0"

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "get_last_run_stream_seq", fake_get_last_run_stream_seq)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].startswith("event: end")
    assert "id: 1700000000004-0" in chunks[0]
    data = _sse_data(chunks[0])
    assert data["request_id"] == "req-1"
    assert data["payload"] == {"status": "completed"}


@pytest.mark.asyncio
async def test_create_agent_run_persists_input_before_enqueue(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(monkeypatch)

    result = await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert db.order[-2:] == ["commit", "enqueue"]
    assert result["run_id"] == db.created_run.id
    assert result["request_id"] == "req-1"
    assert db.request_id_lookups == ["req-1"]
    assert db.created_run_kwargs["request_id"] == "req-1"
    assert db.created_run_kwargs["conversation_id"] == 1
    assert db.created_run_kwargs["input_message_id"] == 10
    assert db.added[0].run_id == db.created_run.id
    assert db.added[0].request_id == "req-1"
    assert db.enqueued == [("process_agent_run", db.created_run.id, f"run:{db.created_run.id}")]
    assert db.created_run_kwargs["input_payload"] == {
        "model_spec": "agent-default-model",
        "tool_approval_mode": "default",
    }
    assert "model_spec" not in db.added[0].extra_metadata
    assert db.added[0].extra_metadata["raw_message"]["type"] == "human"
    assert db.added[0].extra_metadata["raw_message"]["content"] == "hello"
    assert "run_id" not in db.added[0].extra_metadata
    assert "run_type" not in db.added[0].extra_metadata


@pytest.mark.asyncio
async def test_create_agent_run_reuses_existing_only_with_same_request_scope(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="thread-1",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="chat",
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(monkeypatch, existing_run=existing_run)

    result = await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result["run_id"] == "existing-run"
    assert db.active_run_lookup is None
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_create_agent_run_rejects_request_id_scope_mismatch(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="other-thread",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="chat",
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(monkeypatch, existing_run=existing_run)

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=_chat_input("hello"),
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "req-1"},
            current_uid="user-1",
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "request_id 冲突"
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_create_agent_run_integrity_error_reuses_same_request_scope(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="thread-1",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="chat",
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        existing_run_after_rollback=existing_run,
        raise_create_integrity_error=True,
    )

    result = await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result["run_id"] == "existing-run"
    assert "rollback_savepoint" in db.order
    assert "rollback" not in db.order
    assert db.deleted == [db.added[0]]
    assert "commit" not in db.order
    assert "enqueue" not in db.order


@pytest.mark.asyncio
async def test_create_agent_run_integrity_error_rejects_scope_mismatch(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="other-thread",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="chat",
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        existing_run_after_rollback=existing_run,
        raise_create_integrity_error=True,
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=_chat_input("hello"),
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "req-1"},
            current_uid="user-1",
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "request_id 冲突"
    assert "rollback_savepoint" in db.order
    assert "rollback" not in db.order
    assert "commit" not in db.order


@pytest.mark.asyncio
async def test_create_agent_run_integrity_error_returns_run_busy_for_active_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _patch_agent_run_creation(
        monkeypatch,
        active_run_after_rollback=SimpleNamespace(id="active-run", status="pending"),
        raise_create_integrity_error=True,
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=_chat_input("hello"),
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "req-2"},
            current_uid="user-1",
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "run_busy"
    assert exc.value.detail["active_run_id"] == "active-run"
    assert db.request_id_lookups == ["req-2", "req-2"]
    assert "rollback_savepoint" in db.order
    assert "rollback" not in db.order
    assert "commit" not in db.order


@pytest.mark.asyncio
async def test_create_resume_run_marks_input_message_source(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        message_id=11,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
    )

    result = await agent_run_service.create_agent_run_view(
        input_message=None,
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        current_uid="user-1",
        db=db,
        resume={"language": "python"},
        created_by_run_id="parent-run",
    )

    assert result["run_id"] == db.created_run.id
    assert db.created_run_kwargs["run_type"] == "resume"
    assert db.created_run_kwargs["created_by_run_id"] == "parent-run"
    assert db.created_run_kwargs["input_message_id"] == 11
    assert db.added[0].message_type == "resume"
    assert db.added[0].extra_metadata["source"] == "ask_user_question_resume"


@pytest.mark.asyncio
async def test_create_resume_run_without_request_id_reuses_stable_key(monkeypatch: pytest.MonkeyPatch):
    parent_run = SimpleNamespace(
        id="parent-run",
        conversation_thread_id="thread-1",
        status="interrupted",
        input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
    )
    first_db = _patch_agent_run_creation(monkeypatch, parent_run=parent_run)

    await agent_run_service.create_agent_run_view(
        input_message=None,
        agent_slug="default",
        thread_id="thread-1",
        meta={},
        current_uid="user-1",
        db=first_db,
        resume={"language": "python", "answer": "ok"},
        created_by_run_id="parent-run",
    )

    request_id = first_db.created_run_kwargs["request_id"]
    existing_run = SimpleNamespace(
        id="existing-resume-run",
        conversation_thread_id="thread-1",
        agent_slug="default",
        status="pending",
        request_id=request_id,
        uid="user-1",
        run_type="resume",
        created_by_run_id="parent-run",
        subagent_thread_relation_id=None,
    )
    retry_db = _patch_agent_run_creation(monkeypatch, existing_run=existing_run, parent_run=parent_run)

    result = await agent_run_service.create_agent_run_view(
        input_message=None,
        agent_slug="default",
        thread_id="thread-1",
        meta={},
        current_uid="user-1",
        db=retry_db,
        resume={"answer": "ok", "language": "python"},
        created_by_run_id="parent-run",
    )

    assert result["run_id"] == "existing-resume-run"
    assert request_id.startswith("resume:")
    assert len(request_id) <= 64
    assert retry_db.request_id_lookups == [request_id]
    assert retry_db.created_run_kwargs is None
    assert retry_db.order[-2:] == ["commit", "enqueue"]
    assert retry_db.enqueued == [("process_agent_run", "existing-resume-run", "run:existing-resume-run")]


@pytest.mark.asyncio
async def test_create_resume_run_requires_parent_run_id(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(monkeypatch)

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=None,
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "resume-req"},
            current_uid="user-1",
            db=db,
            resume={"language": "python"},
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "created_by_run_id 不能为空"
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_create_resume_run_rejects_non_interrupted_parent(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="running",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=None,
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "resume-req"},
            current_uid="user-1",
            db=db,
            resume={"language": "python"},
            created_by_run_id="parent-run",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "只有 interrupted run 可以恢复"
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_create_resume_run_rejects_superseded_interrupt(monkeypatch: pytest.MonkeyPatch):
    parent_run = SimpleNamespace(
        id="parent-run",
        conversation_thread_id="thread-1",
        status="interrupted",
        input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
    )
    newer_run = SimpleNamespace(id="newer-run", status="failed")
    db = _patch_agent_run_creation(monkeypatch, parent_run=parent_run, latest_run=newer_run)

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=None,
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "resume-req"},
            current_uid="user-1",
            db=db,
            resume={"language": "python"},
            created_by_run_id="parent-run",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "resume_superseded"


@pytest.mark.asyncio
async def test_create_resume_run_rejects_parent_without_model_snapshot(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={},
        ),
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=None,
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "resume-req"},
            current_uid="user-1",
            db=db,
            resume={"language": "python"},
            created_by_run_id="parent-run",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "被恢复的运行任务缺少模型快照"
    assert db.created_run_kwargs is None


@pytest.mark.asyncio
async def test_create_agent_run_rejects_active_checkpoint_run(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(monkeypatch, active_run=SimpleNamespace(id="active-run", status="running"))

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_agent_run_view(
            input_message=_chat_input("hello"),
            agent_slug="default",
            thread_id="thread-1",
            meta={"request_id": "req-1"},
            current_uid="user-1",
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "run_busy"
    assert exc.value.detail["active_run_id"] == "active-run"
    assert db.active_run_lookup == {
        "agent_slug": "default",
        "conversation_thread_id": "thread-1",
        "uid": "user-1",
    }
    assert db.created_run_kwargs is None


# ==================== run 结果基础能力 ====================


@pytest.mark.asyncio
async def test_get_agent_run_result_uses_output_message_id(monkeypatch: pytest.MonkeyPatch):
    run = SimpleNamespace(
        id="run-1",
        status="completed",
        agent_slug="default-chatbot",
        conversation_thread_id="thread-1",
        conversation_id=10,
        request_id="req-1",
        output_message_id=2,
        error_type=None,
        error_message=None,
    )
    messages = [
        SimpleNamespace(id=1, role="user", content="question", extra_metadata={}),
        SimpleNamespace(id=2, role="assistant", content="older", extra_metadata={"langfuse_trace_id": "trace-old"}),
        SimpleNamespace(id=3, role="assistant", content="final", extra_metadata={"langfuse_trace_id": "trace-final"}),
    ]

    class FakeScalars:
        def unique(self):
            return self

        def all(self):
            return messages

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult()

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "run-1"
            assert uid == "user-1"
            return run

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-1", current_uid="user-1", db=FakeDB())

    assert payload["status"] == "completed"
    assert payload["output"] == "older"
    assert payload["final_message_id"] == 2
    assert payload["langfuse_trace_id"] == "trace-old"
    assert "debug" not in payload


@pytest.mark.asyncio
async def test_get_agent_run_result_missing_run_returns_failed(monkeypatch: pytest.MonkeyPatch):
    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return None

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-x", current_uid="user-1", db=object())

    assert payload["status"] == "failed"
    assert payload["error"]["type"] == "run_not_found"


@pytest.mark.asyncio
async def test_await_agent_run_result_drains_stream_then_loads_result(monkeypatch: pytest.MonkeyPatch):
    drained: list[str] = []

    async def fake_stream(*, run_id: str, after_seq: str, current_uid: str, verbose: bool):
        assert run_id == "run-1"
        assert after_seq == "0-0"
        assert current_uid == "user-1"
        assert verbose is False
        for chunk in ("event: messages\n\n", "event: end\n\n"):
            drained.append(chunk)
            yield chunk

    async def fake_load(*, run_id: str, current_uid: str):
        assert run_id == "run-1"
        assert current_uid == "user-1"
        return {"status": "completed", "output": "final"}

    monkeypatch.setattr(agent_run_service, "stream_agent_run_events", fake_stream)
    monkeypatch.setattr(agent_run_service, "load_agent_run_result", fake_load)

    payload = await agent_run_service.await_agent_run_result(run_id="run-1", current_uid="user-1")

    assert len(drained) == 2
    assert payload == {"status": "completed", "output": "final"}


@pytest.mark.asyncio
async def test_await_agent_run_result_raises_when_stream_ends_before_terminal(monkeypatch: pytest.MonkeyPatch):
    async def fake_stream(*, run_id: str, after_seq: str, current_uid: str, verbose: bool):
        assert run_id == "run-1"
        assert after_seq == "0-0"
        assert current_uid == "user-1"
        assert verbose is False
        yield ": heartbeat\n\n"

    async def fake_load(*, run_id: str, current_uid: str):
        assert run_id == "run-1"
        assert current_uid == "user-1"
        return {"status": "running", "agent_run_id": run_id, "output": ""}

    monkeypatch.setattr(agent_run_service, "stream_agent_run_events", fake_stream)
    monkeypatch.setattr(agent_run_service, "load_agent_run_result", fake_load)

    with pytest.raises(agent_run_service.AgentRunWaitTimeout) as exc_info:
        await agent_run_service.await_agent_run_result(run_id="run-1", current_uid="user-1")

    assert exc_info.value.result == {"status": "running", "agent_run_id": "run-1", "output": ""}


@pytest.mark.asyncio
async def test_cancel_agent_run_view_cascades_children(monkeypatch: pytest.MonkeyPatch):
    parent_run = SimpleNamespace(id="parent-run", uid="user-1", to_dict=lambda: {"id": "parent-run"})
    child_runs = [SimpleNamespace(id="child-1"), SimpleNamespace(id="child-2")]
    requested: list[str] = []
    signals: list[tuple[str, bool]] = []

    class Db:
        committed = False

        async def commit(self):
            self.committed = True

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "parent-run"
            assert uid == "user-1"
            return parent_run

        async def list_active_child_runs_for_user(self, created_by_run_id: str, uid: str):
            assert created_by_run_id == "parent-run"
            assert uid == "user-1"
            return child_runs

        async def request_cancel(self, run_id: str):
            requested.append(run_id)
            return parent_run if run_id == "parent-run" else SimpleNamespace(id=run_id)

    async def fake_publish_cancel_signal(run_id: str):
        signals.append((run_id, db.committed))

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "publish_cancel_signal", fake_publish_cancel_signal)
    db = Db()

    result = await agent_run_service.cancel_agent_run_view(
        run_id="parent-run",
        current_uid="user-1",
        db=db,
    )

    assert result["run"]["id"] == "parent-run"
    assert requested == ["child-1", "child-2", "parent-run"]
    assert signals == [("child-1", True), ("child-2", True), ("parent-run", True)]


def test_resolve_agent_run_model_spec_rejects_unknown_explicit_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", lambda spec: None)
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service.resolve_agent_run_model_spec("nope", SimpleNamespace(config_json={}), _FakeBackend())
    assert exc.value.status_code == 422


def test_resolve_agent_run_model_spec_rejects_non_chat_explicit_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="embedding"),
    )
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service.resolve_agent_run_model_spec("embed-1", SimpleNamespace(config_json={}), _FakeBackend())
    assert exc.value.status_code == 422


def test_resolve_agent_run_model_spec_strips_explicit_chat_model(monkeypatch: pytest.MonkeyPatch):
    seen = []

    def fake_get_model_info(spec):
        seen.append(spec)
        return SimpleNamespace(model_type="chat")

    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", fake_get_model_info)

    assert (
        agent_run_service.resolve_agent_run_model_spec(
            " gpt-x ",
            SimpleNamespace(config_json={}),
            _FakeBackend(),
        )
        == "gpt-x"
    )
    assert seen == ["gpt-x"]


def _patch_agent_run_creation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_config_json: dict | None = None,
    message_id: int = 10,
    active_run: SimpleNamespace | None = None,
    active_run_after_rollback: SimpleNamespace | None = None,
    existing_run: SimpleNamespace | None = None,
    existing_run_after_rollback: SimpleNamespace | None = None,
    parent_run: SimpleNamespace | None = None,
    latest_run: SimpleNamespace | None = None,
    raise_create_integrity_error: bool = False,
):
    runs_by_id = {
        "parent-agent-run": SimpleNamespace(
            id="parent-agent-run",
            conversation_id=99,
            conversation_thread_id="parent-thread",
        )
    }
    if parent_run:
        parent_run.agent_slug = getattr(parent_run, "agent_slug", "default")
        runs_by_id["parent-run"] = parent_run
    db = _CreateRunDb(
        message_id=message_id,
        active_run=active_run,
        active_run_after_rollback=active_run_after_rollback,
        existing_run=existing_run,
        existing_run_after_rollback=existing_run_after_rollback,
        runs_by_id=runs_by_id,
        latest_run=latest_run,
        raise_create_integrity_error=raise_create_integrity_error,
    )

    class ConvRepo:
        def __init__(self, db_session):
            del db_session

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

        async def lock_conversation_by_thread_id(self, thread_id: str):
            return await self.get_conversation_by_thread_id(thread_id)

    class AgentRepo:
        def __init__(self, db_session):
            del db_session

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user
            is_subagent = kind == "subagent"
            return SimpleNamespace(
                slug=slug,
                name="Default",
                backend_id="ChatbotAgent",
                config_json=agent_config_json or {"context": {}},
                is_subagent=is_subagent,
            )

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            assert db.committed is True
            db.order.append("enqueue")
            db.enqueued.append((job_name, run_id, _job_id))

    async def fake_get_arq_pool():
        return Queue()

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", _CreateRunRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)
    return db


@pytest.mark.asyncio
async def test_create_chat_run_persists_validated_model_spec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    db = _patch_agent_run_creation(monkeypatch)

    await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
        model_spec="claude-x",
    )

    assert db.created_run_kwargs["input_payload"]["model_spec"] == "claude-x"


@pytest.mark.asyncio
async def test_create_chat_run_with_image_persists_multimodal_message_type(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(monkeypatch)

    await agent_run_service.create_agent_run_view(
        input_message=_chat_input("看图", "base64-image"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert db.created_run_kwargs["input_payload"] == {
        "model_spec": "agent-default-model",
        "tool_approval_mode": "default",
    }
    assert db.added[0].message_type == "multimodal_image"
    assert db.added[0].image_content == "base64-image"
    raw_message = db.added[0].extra_metadata["raw_message"]
    assert raw_message["type"] == "human"
    assert raw_message["content"][0] == {"type": "text", "text": "看图"}
    assert raw_message["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,base64-image"


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_agent_configured_model_spec(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        agent_config_json={"context": {"model": "agent-config-model"}},
    )

    await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert db.created_run_kwargs["input_payload"]["model_spec"] == "agent-config-model"
    assert "model_spec" not in db.added[0].extra_metadata


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_system_default_when_agent_model_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service,
        "resolve_chat_model_spec",
        lambda model_spec: str(model_spec).strip() if str(model_spec or "").strip() else "system-default-model",
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        agent_config_json={"context": {"model": ""}},
    )

    await agent_run_service.create_agent_run_view(
        input_message=_chat_input("hello"),
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert db.created_run_kwargs["input_payload"]["model_spec"] == "system-default-model"
    assert "model_spec" not in db.added[0].extra_metadata


@pytest.mark.asyncio
async def test_create_resume_run_inherits_parent_model_spec(monkeypatch: pytest.MonkeyPatch):
    # 即使 resume 入参传了别的模型，也必须沿用父运行的模型
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "always_trust"},
        ),
    )

    await agent_run_service.create_agent_run_view(
        input_message=None,
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        current_uid="user-1",
        db=db,
        model_spec="ignored-model",
        resume={"language": "python"},
        created_by_run_id="parent-run",
    )

    assert db.created_run_kwargs["input_payload"]["model_spec"] == "parent-model"
    assert db.created_run_kwargs["input_payload"]["tool_approval_mode"] == "always_trust"


@pytest.mark.asyncio
async def test_create_resume_run_defaults_tool_approval_mode_for_legacy_parent(monkeypatch: pytest.MonkeyPatch):
    # 旧版本固化的 input_payload 没有 tool_approval_mode，resume 必须回退默认值而不能报错。
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model"},
        ),
    )

    await agent_run_service.create_agent_run_view(
        input_message=None,
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        current_uid="user-1",
        db=db,
        resume={"decisions": [{"type": "approve"}]},
        created_by_run_id="parent-run",
    )

    assert db.created_run_kwargs["input_payload"]["tool_approval_mode"] == "default"


def test_resolve_tool_approval_mode_uses_request_then_agent_config_then_default():
    configured_agent = SimpleNamespace(config_json={"context": {"tool_approval_mode": "always_trust"}})
    default_agent = SimpleNamespace(config_json={})

    assert (
        agent_run_service.resolve_agent_run_tool_approval_mode("default", configured_agent, _FakeBackend()) == "default"
    )
    assert (
        agent_run_service.resolve_agent_run_tool_approval_mode(None, configured_agent, _FakeBackend()) == "always_trust"
    )
    assert agent_run_service.resolve_agent_run_tool_approval_mode(None, default_agent, _FakeBackend()) == "default"


def test_resolve_tool_approval_mode_rejects_unknown_value():
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service.resolve_agent_run_tool_approval_mode(
            "unknown", SimpleNamespace(config_json={}), _FakeBackend()
        )

    assert exc.value.status_code == 422


def test_validate_resume_input_accepts_only_approve_and_reject_decisions():
    agent_run_service._validate_resume_input({"decisions": [{"type": "approve"}, {"type": "reject", "message": "no"}]})

    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_resume_input({"decisions": [{"type": "edit"}]})

    assert exc.value.status_code == 422


def test_compact_stream_chunk_retains_compression_field():
    chunk = {
        "request_id": "req-1",
        "response": None,
        "thread_id": "thread-1",
        "status": "context_compression",
        "compression": {"type": "yuxi.context_compression", "status": "started"},
        "meta": {"uid": "user-1"},
    }

    compact = agent_run_service._compact_stream_chunk(chunk)

    assert compact["status"] == "context_compression"
    assert compact["compression"] == {"type": "yuxi.context_compression", "status": "started"}


def test_compact_stream_chunk_retains_tool_approval_payload():
    approval = {
        "action_requests": [{"name": "execute", "args": {"command": "pytest -q"}}],
        "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
    }

    compact = agent_run_service._compact_stream_chunk(
        {"status": "human_approval_required", "run_id": "run-1", "approval": approval}
    )

    assert compact["approval"] == approval
