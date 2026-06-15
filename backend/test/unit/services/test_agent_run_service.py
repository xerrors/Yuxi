from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import yuxi.services.agent_run_service as agent_run_service


def _sse_data(chunk: str) -> dict:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"SSE chunk has no data line: {chunk}")


class _FakeContext:
    def __init__(self):
        self.model = "agent-default-model"

    def update_from_dict(self, data: dict):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


class _FakeBackend:
    context_schema = _FakeContext


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
            return SimpleNamespace(status="completed", thread_id="thread-1")

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
            return SimpleNamespace(status="completed", thread_id="thread-1")

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
                        "agent_id": "deep-research",
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
            return SimpleNamespace(status="completed", thread_id="thread-1", request_id="req-1")

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
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.order: list[str] = []
            self.committed = False
            self.added = []

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            self.order.append("flush")
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 10

        async def commit(self):
            self.order.append("commit")
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    db = FakeDB()
    captured = {}
    created_run = SimpleNamespace(
        id="",
        thread_id="thread-1",
        status="pending",
        request_id="req-1",
        uid="user-1",
    )

    class RunRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            assert kwargs["request_id"] == "req-1"
            assert kwargs["conversation_id"] == 1
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            assert run_id == created_run.id
            assert message_id == 10
            return created_run

    class ConvRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            assert job_name == "process_agent_run"
            assert run_id == created_run.id
            assert _job_id == f"run:{created_run.id}"
            db.order.append("enqueue")
            assert db.committed is True

    async def fake_get_arq_pool():
        return Queue()

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)

    result = await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
    )

    assert db.order[-2:] == ["commit", "enqueue"]
    assert result["run_id"] == created_run.id
    assert result["request_id"] == "req-1"
    assert db.added[0].run_id == created_run.id
    assert db.added[0].request_id == "req-1"
    assert captured["input_payload"]["model_spec"] == "agent-default-model"
    assert db.added[0].extra_metadata["model_spec"] == "agent-default-model"


@pytest.mark.asyncio
async def test_create_resume_run_marks_input_message_source(monkeypatch: pytest.MonkeyPatch):
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.order: list[str] = []
            self.committed = False
            self.added = []

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            self.order.append("flush")
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 11

        async def commit(self):
            self.order.append("commit")
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    db = FakeDB()
    created_run = SimpleNamespace(
        id="",
        thread_id="thread-1",
        status="pending",
        request_id="resume-req",
        uid="user-1",
    )

    class RunRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "parent-run"
            assert uid == "user-1"
            return SimpleNamespace(id=run_id, thread_id="thread-1", status="interrupted", input_payload={})

        async def get_resume_run(self, parent_run_id: str, resume_request_id: str):
            assert parent_run_id == "parent-run"
            assert resume_request_id == "resume-req"
            return None

        async def get_run_by_request_id(self, request_id: str):
            assert request_id == "resume-req"
            return None

        async def create_run(self, **kwargs):
            assert kwargs["run_type"] == "resume"
            assert kwargs["parent_run_id"] == "parent-run"
            assert kwargs["resume_request_id"] == "resume-req"
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            assert run_id == created_run.id
            assert message_id == 11
            return created_run

    class ConvRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            self.db = db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            assert job_name == "process_agent_run"
            assert run_id == created_run.id
            assert _job_id == f"run:{created_run.id}"
            assert db.committed is True

    async def fake_get_arq_pool():
        return Queue()

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)

    result = await agent_run_service.create_agent_run_view(
        query=None,
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        image_content=None,
        current_uid="user-1",
        db=db,
        resume={"language": "python"},
        parent_run_id="parent-run",
        resume_request_id="resume-req",
    )

    assert result["run_id"] == created_run.id
    assert db.added[0].message_type == "resume"
    assert db.added[0].extra_metadata["source"] == "ask_user_question_resume"


# ==================== 对话级模型覆盖 model_spec ====================


def test_validate_model_spec_returns_none_when_empty():
    assert agent_run_service._validate_model_spec(None) is None
    assert agent_run_service._validate_model_spec("") is None


def test_validate_model_spec_rejects_unknown_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", lambda spec: None)
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_model_spec("nope")
    assert exc.value.status_code == 422


def test_validate_model_spec_rejects_non_chat_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="embedding"),
    )
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_model_spec("embed-1")
    assert exc.value.status_code == 422


def test_validate_model_spec_accepts_chat_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    assert agent_run_service._validate_model_spec("gpt-x") == "gpt-x"


def test_validate_model_spec_strips_explicit_model(monkeypatch: pytest.MonkeyPatch):
    seen = []

    def fake_get_model_info(spec):
        seen.append(spec)
        return SimpleNamespace(model_type="chat")

    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", fake_get_model_info)

    assert agent_run_service._validate_model_spec(" gpt-x ") == "gpt-x"
    assert seen == ["gpt-x"]


def _patch_common_run_repos(
    monkeypatch: pytest.MonkeyPatch,
    run_repo_cls,
    *,
    agent_config_json: dict | None = None,
):
    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(uid="user-1", role="user")

    class FakeDB:
        def __init__(self):
            self.added = []
            self.committed = False

        async def execute(self, stmt):
            del stmt
            return FakeResult()

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            for item in self.added:
                if getattr(item, "id", None) is None:
                    item.id = 10

        async def commit(self):
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not be called")

    class ConvRepo:
        def __init__(self, db_session):
            del db_session

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return SimpleNamespace(id=1, uid="user-1", status="active", agent_id="default")

    class AgentRepo:
        def __init__(self, db_session):
            del db_session

        async def get_visible_by_slug(self, slug: str, user):
            del user
            return SimpleNamespace(
                slug=slug,
                backend_id="ChatbotAgent",
                config_json=agent_config_json or {"context": {}},
            )

    class Queue:
        async def enqueue_job(self, job_name: str, run_id: str, _job_id: str):
            del job_name, run_id, _job_id

    async def fake_get_arq_pool():
        return Queue()

    monkeypatch.setattr(agent_run_service.agent_manager, "get_agent", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(agent_run_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_run_service, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", run_repo_cls)
    monkeypatch.setattr(agent_run_service, "get_arq_pool", fake_get_arq_pool)
    return FakeDB()


@pytest.mark.asyncio
async def test_create_chat_run_persists_validated_model_spec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec="claude-x",
    )

    assert captured["input_payload"]["model_spec"] == "claude-x"


@pytest.mark.asyncio
async def test_create_chat_run_with_image_persists_multimodal_message_type(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query="看图",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content="base64-image",
        current_uid="user-1",
        db=db,
    )

    assert captured["input_payload"]["image_content"] == "base64-image"
    assert db.added[0].message_type == "multimodal_image"
    assert db.added[0].image_content == "base64-image"


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_agent_configured_model_spec(monkeypatch: pytest.MonkeyPatch):
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(
        monkeypatch,
        RunRepo,
        agent_config_json={"context": {"model": "agent-config-model"}},
    )

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert captured["input_payload"]["model_spec"] == "agent-config-model"
    assert db.added[0].extra_metadata["model_spec"] == "agent-config-model"


@pytest.mark.asyncio
async def test_create_chat_run_snapshots_system_default_when_agent_model_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service,
        "resolve_chat_model_spec",
        lambda model_spec: str(model_spec).strip() if str(model_spec or "").strip() else "system-default-model",
    )
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="req-1", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(
        monkeypatch,
        RunRepo,
        agent_config_json={"context": {"model": ""}},
    )

    await agent_run_service.create_agent_run_view(
        query="hello",
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec=None,
    )

    assert captured["input_payload"]["model_spec"] == "system-default-model"
    assert db.added[0].extra_metadata["model_spec"] == "system-default-model"


@pytest.mark.asyncio
async def test_create_resume_run_inherits_parent_model_spec(monkeypatch: pytest.MonkeyPatch):
    # 即使 resume 入参传了别的模型，也必须沿用父运行的模型
    captured = {}
    created_run = SimpleNamespace(id="", thread_id="thread-1", status="pending", request_id="resume-req", uid="user-1")

    class RunRepo:
        def __init__(self, db_session):
            del db_session

        async def get_run_for_user(self, run_id: str, uid: str):
            del uid
            return SimpleNamespace(
                id=run_id,
                thread_id="thread-1",
                status="interrupted",
                input_payload={"model_spec": "parent-model"},
            )

        async def get_resume_run(self, parent_run_id: str, resume_request_id: str):
            del parent_run_id, resume_request_id
            return None

        async def get_run_by_request_id(self, request_id: str):
            del request_id
            return None

        async def create_run(self, **kwargs):
            captured["input_payload"] = kwargs["input_payload"]
            created_run.id = kwargs["run_id"]
            return created_run

        async def set_input_message(self, run_id: str, message_id: int):
            del run_id, message_id
            return created_run

    db = _patch_common_run_repos(monkeypatch, RunRepo)

    await agent_run_service.create_agent_run_view(
        query=None,
        agent_id="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        image_content=None,
        current_uid="user-1",
        db=db,
        model_spec="ignored-model",
        resume={"language": "python"},
        parent_run_id="parent-run",
        resume_request_id="resume-req",
    )

    assert captured["input_payload"]["model_spec"] == "parent-model"
