from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import yuxi.services.agent_run_service as agent_run_service
from yuxi.services.input_message_service import (
    build_chat_input_message_from_openai_content,
    restore_chat_input_message,
)


def _sse_data(chunk: str) -> dict:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"SSE chunk has no data line: {chunk}")


def _run_state(status: str = "running", *, cleanup_pending: bool = False):
    return SimpleNamespace(
        status=status,
        conversation_thread_id="thread-1",
        request_id="req-1",
        runtime_cleanup_pending=cleanup_pending,
    )


def _run_stream_event(seq: str, event_type: str, payload: dict) -> dict:
    return {
        "seq": seq,
        "event_type": event_type,
        "payload": {
            "schema_version": 1,
            "run_id": "run-1",
            "thread_id": "thread-1",
            "event": event_type,
            "payload": payload,
            "created_at": "2026-09-04T00:00:00+00:00",
        },
        "ts": int(seq.partition("-")[0]),
    }


def test_run_sse_poll_interval_caps_short_and_long_idle_periods():
    interval = agent_run_service.RUN_SSE_ACTIVE_POLL_SECONDS
    short_idle_intervals = []
    for _ in range(6):
        interval = agent_run_service._next_run_sse_poll_interval(interval, idle_seconds=30)
        short_idle_intervals.append(interval)

    assert short_idle_intervals == [0.2, 0.4, 0.8, 1.0, 1.0, 1.0]
    assert agent_run_service._next_run_sse_poll_interval(1.0, idle_seconds=120) == 2.0
    assert agent_run_service._next_run_sse_poll_interval(2.0, idle_seconds=120) == 4.0
    assert agent_run_service._next_run_sse_poll_interval(4.0, idle_seconds=120) == 4.0


def test_run_sse_poll_jitter_stays_within_twenty_percent(monkeypatch: pytest.MonkeyPatch):
    multipliers = iter([0.8, 1.2])
    monkeypatch.setattr(agent_run_service, "uniform", lambda _low, _high: next(multipliers))

    assert agent_run_service._jitter_run_sse_poll_interval(1.0) == 0.8
    assert agent_run_service._jitter_run_sse_poll_interval(1.0) == 1.2


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


@pytest.mark.asyncio
async def test_resume_input_message_keeps_invocation_meta_namespaced(monkeypatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model"},
        ),
    )
    await agent_run_service.create_resume_run_view(
        agent_slug="default",
        thread_id="thread-1",
        current_uid="user-1",
        db=db,
        resume={"answer": "ok"},
        created_by_run_id="parent-run",
        meta={
            "source": "agent_call",
            "agent_invocation_meta": {"trace_id": "trace-1"},
            "evaluation": {"dataset_name": "legacy-top-level"},
            "custom_variables": {"system_prompt": "legacy"},
        },
    )

    input_message = db.added[0]
    assert input_message.extra_metadata["source"] == "ask_user_question_resume"
    assert input_message.extra_metadata["agent_invocation_meta"] == {"trace_id": "trace-1"}
    assert "evaluation" not in input_message.extra_metadata
    assert "custom_variables" not in input_message.extra_metadata


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

    def update_config(self, data: dict):
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
async def test_stream_agent_run_events_authorizes_before_reading_redis(monkeypatch: pytest.MonkeyPatch):
    async def fake_load_run(run_id: str, uid: str):
        del run_id, uid
        return None

    async def unexpected_list_events(*_args, **_kwargs):
        pytest.fail("未授权连接不得读取 Redis Run 事件")

    monkeypatch.setattr(agent_run_service, "_load_stream_run_for_user", fake_load_run)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", unexpected_list_events)

    chunks = [
        chunk
        async for chunk in agent_run_service.stream_agent_run_events(
            run_id="run-1",
            after_seq="0",
            current_uid="other-user",
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert "运行任务不存在" in chunks[0]


@pytest.mark.asyncio
async def test_stream_agent_run_events_emits_error_when_status_refresh_fails(monkeypatch: pytest.MonkeyPatch):
    async def fake_load_run(run_id: str, uid: str):
        del run_id, uid
        return _run_state()

    async def broken_refresh(run_id: str):
        del run_id
        raise RuntimeError("db down")

    async def fake_list_events(*_args, **_kwargs):
        return []

    monkeypatch.setattr(agent_run_service, "_load_stream_run_for_user", fake_load_run)
    monkeypatch.setattr(agent_run_service, "_load_stream_run", broken_refresh)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "RUN_SSE_STATUS_POLL_SECONDS", 0.0)

    chunks = [
        chunk
        async for chunk in agent_run_service.stream_agent_run_events(
            run_id="run-1",
            after_seq="0",
            current_uid="user-1",
        )
    ]

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
async def test_stream_agent_run_events_decouples_pg_checks_from_redis_polling(
    monkeypatch: pytest.MonkeyPatch,
):
    """高频 Redis 空轮询不得同步放大 PostgreSQL 可见性查询。"""
    pg_reads = 0

    async def fake_load_run(run_id: str, uid: str):
        nonlocal pg_reads
        del run_id, uid
        pg_reads += 1
        return _run_state()

    async def fake_refresh_run(run_id: str):
        nonlocal pg_reads
        del run_id
        pg_reads += 1
        return _run_state()

    redis_reads = 0

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        nonlocal redis_reads
        del run_id, after_seq, limit
        redis_reads += 1
        if redis_reads < 4:
            return []
        return [_run_stream_event("1700000000004-0", "end", {"status": "completed"})]

    sleep_intervals = []

    async def fake_sleep(seconds: float):
        sleep_intervals.append(seconds)

    monkeypatch.setattr(agent_run_service, "_load_stream_run_for_user", fake_load_run)
    monkeypatch.setattr(agent_run_service, "_load_stream_run", fake_refresh_run)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(agent_run_service, "monotonic", lambda: 0.0)
    monkeypatch.setattr(agent_run_service, "uniform", lambda _low, _high: 1.0)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert pg_reads == 1
    assert redis_reads == 4
    assert sleep_intervals == [0.1, 0.2, 0.4]
    assert chunks[-1].startswith("event: end")


@pytest.mark.asyncio
async def test_stream_agent_run_events_resets_adaptive_poll_after_event(
    monkeypatch: pytest.MonkeyPatch,
):
    """任一 Run 事件都必须把退避后的轮询恢复到低延迟档。"""

    async def fake_load_run(run_id: str, uid: str):
        del run_id, uid
        return _run_state()

    async def fake_refresh_run(run_id: str):
        del run_id
        return _run_state()

    redis_results = iter(
        [
            [],
            [],
            [_run_stream_event("1700000000001-0", "messages", {"items": []})],
            [],
            [_run_stream_event("1700000000002-0", "end", {"status": "completed"})],
        ]
    )

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return next(redis_results)

    sleep_intervals = []

    async def fake_sleep(seconds: float):
        sleep_intervals.append(seconds)

    monkeypatch.setattr(agent_run_service, "_load_stream_run_for_user", fake_load_run)
    monkeypatch.setattr(agent_run_service, "_load_stream_run", fake_refresh_run)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(agent_run_service, "monotonic", lambda: 0.0)
    monkeypatch.setattr(agent_run_service, "uniform", lambda _low, _high: 1.0)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
    ):
        chunks.append(chunk)

    assert sleep_intervals == [0.1, 0.2, 0.1, 0.1]
    assert [chunk.splitlines()[0] for chunk in chunks] == ["event: messages", "event: end"]


@pytest.mark.asyncio
async def test_stream_agent_run_events_refreshes_pg_before_cleanup_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    """Redis 缺少 end 时，低频 PG 探测仍须等待 cleanup fence 后补发终态。"""
    visibility_reads = 0
    status_reads = 0

    async def fake_load_run(run_id: str, uid: str):
        nonlocal visibility_reads
        del run_id, uid
        visibility_reads += 1
        return _run_state("completed", cleanup_pending=True)

    async def fake_refresh_run(run_id: str):
        nonlocal status_reads
        del run_id
        status_reads += 1
        return _run_state("completed")

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return []

    async def fake_last_stream_seq(run_id: str):
        del run_id
        return "0-0"

    clock = 0.0
    sleep_intervals = []

    async def fake_sleep(seconds: float):
        nonlocal clock
        sleep_intervals.append(seconds)
        clock += seconds

    monkeypatch.setattr(agent_run_service, "_load_stream_run_for_user", fake_load_run)
    monkeypatch.setattr(agent_run_service, "_load_stream_run", fake_refresh_run)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service, "get_last_run_stream_seq", fake_last_stream_seq)
    monkeypatch.setattr(agent_run_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(agent_run_service, "monotonic", lambda: clock)
    monkeypatch.setattr(agent_run_service, "uniform", lambda _low, _high: 1.2)
    monkeypatch.setattr(agent_run_service, "RUN_SSE_LONG_IDLE_AFTER_SECONDS", 0.0)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert visibility_reads == 1
    assert status_reads == 1
    assert clock == agent_run_service.RUN_SSE_STATUS_POLL_SECONDS
    assert sleep_intervals[-1] < 3.2 * 1.2
    assert len(chunks) == 1
    assert chunks[0].startswith("event: end")
    assert _sse_data(chunks[0])["payload"] == {"status": "completed"}


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
                        "run_type": "chat",
                        "source": "chat",
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

    assert len(chunks) == 4

    metadata_data = _sse_data(chunks[0])
    assert metadata_data["payload"] == {"run_type": "chat", "source": "chat"}

    init_data = _sse_data(chunks[1])
    init_chunk = init_data["payload"]["chunk"]
    assert init_data["request_id"] == "req-1"
    assert init_data["payload"]["name"] == "yuxi.init"
    assert "meta" not in init_chunk
    assert "request_id" not in init_chunk
    assert "response" not in init_chunk
    assert "thread_id" not in init_chunk
    assert "image_content" not in init_chunk["msg"]
    assert "extra_metadata" not in init_chunk["msg"]

    message_data = _sse_data(chunks[2])
    message_chunk = message_data["payload"]["items"][0]
    assert message_data["request_id"] == "req-1"
    assert "request_id" not in message_chunk
    assert "metadata" not in message_chunk
    assert "response" not in message_chunk
    assert "thread_id" not in message_chunk
    assert message_chunk["stream_event"]["tool_call_id"] == "call-1"
    assert "thread_id" not in message_chunk["stream_event"]
    assert "namespace" not in message_chunk["stream_event"]

    end_data = _sse_data(chunks[3])
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
            return SimpleNamespace(
                status="completed",
                conversation_thread_id="thread-1",
                request_id="req-1",
                runtime_cleanup_pending=False,
            )

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
async def test_stream_agent_run_events_does_not_fallback_end_before_runtime_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    """PostgreSQL 已终态但 cleanup fence 未清除时，SSE 不能越过 worker 提前合成 end。"""

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            return SimpleNamespace(
                status="completed",
                conversation_thread_id="thread-1",
                request_id="req-1",
                runtime_cleanup_pending=True,
            )

    async def fake_list_events(run_id: str, *, after_seq: str, limit: int):
        del run_id, after_seq, limit
        return []

    sleep_calls = 0

    async def stop_after_one_poll(_seconds: float):
        nonlocal sleep_calls
        sleep_calls += 1
        raise agent_run_service.asyncio.CancelledError

    monkeypatch.setattr(agent_run_service.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(agent_run_service, "AgentRunRepository", Repo)
    monkeypatch.setattr(agent_run_service, "list_run_stream_events", fake_list_events)
    monkeypatch.setattr(agent_run_service.asyncio, "sleep", stop_after_one_poll)

    chunks = []
    async for chunk in agent_run_service.stream_agent_run_events(
        run_id="run-1",
        after_seq="0",
        current_uid="user-1",
        verbose=False,
    ):
        chunks.append(chunk)

    assert sleep_calls == 1
    assert not any(chunk.startswith("event: end") for chunk in chunks)


@pytest.mark.asyncio
async def test_create_resume_run_persists_input_before_enqueue(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
    )

    result = await agent_run_service.create_resume_run_view(
        resume={"answer": "ok"},
        created_by_run_id="parent-run",
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
        "model_spec": "parent-model",
        "tool_approval_mode": "default",
    }
    assert "model_spec" not in db.added[0].extra_metadata
    assert db.added[0].message_type == "resume"
    assert db.added[0].extra_metadata["resume"] == {"answer": "ok"}
    assert "run_id" not in db.added[0].extra_metadata
    assert "run_type" not in db.added[0].extra_metadata


@pytest.mark.asyncio
async def test_create_resume_run_reuses_existing_only_with_same_request_scope(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="thread-1",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="resume",
        created_by_run_id="parent-run",
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        existing_run=existing_run,
    )

    result = await agent_run_service.create_resume_run_view(
        resume={"answer": "ok"},
        created_by_run_id="parent-run",
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
async def test_create_resume_run_rejects_request_id_scope_mismatch(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="other-thread",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="resume",
        created_by_run_id="parent-run",
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        existing_run=existing_run,
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_resume_run_view(
            resume={"answer": "ok"},
            created_by_run_id="parent-run",
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
async def test_create_resume_run_integrity_error_reuses_same_request_scope(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="thread-1",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="resume",
        created_by_run_id="parent-run",
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        existing_run_after_rollback=existing_run,
        raise_create_integrity_error=True,
    )

    result = await agent_run_service.create_resume_run_view(
        resume={"answer": "ok"},
        created_by_run_id="parent-run",
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
async def test_create_resume_run_integrity_error_rejects_scope_mismatch(monkeypatch: pytest.MonkeyPatch):
    existing_run = SimpleNamespace(
        id="existing-run",
        conversation_thread_id="other-thread",
        agent_slug="default",
        status="pending",
        request_id="req-1",
        uid="user-1",
        run_type="resume",
        created_by_run_id="parent-run",
        subagent_thread_relation_id=None,
    )
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        existing_run_after_rollback=existing_run,
        raise_create_integrity_error=True,
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_resume_run_view(
            resume={"answer": "ok"},
            created_by_run_id="parent-run",
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
async def test_create_resume_run_integrity_error_returns_run_busy_for_active_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        active_run_after_rollback=SimpleNamespace(id="active-run", status="pending"),
        raise_create_integrity_error=True,
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_resume_run_view(
            resume={"answer": "ok"},
            created_by_run_id="parent-run",
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
            source="agent_call",
            channel="api",
        ),
    )

    result = await agent_run_service.create_resume_run_view(
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
    assert db.created_run_kwargs["source"] == "agent_call"
    assert db.created_run_kwargs["channel"] == "api"
    assert db.added[0].message_type == "resume"
    assert db.added[0].extra_metadata["source"] == "ask_user_question_resume"


@pytest.mark.asyncio
async def test_create_resume_run_preserves_explicit_origin_snapshot(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
            source="agent_call",
            channel="api",
            external_id="original-message",
            origin_metadata={"original": "metadata"},
        ),
    )

    await agent_run_service.create_resume_run_view(
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        current_uid="user-1",
        db=db,
        resume={"decisions": [{"type": "approve"}]},
        created_by_run_id="parent-run",
        source="chat",
        channel="web",
        external_id="approval-message",
        origin_metadata={"approval": "metadata"},
    )

    assert db.created_run_kwargs["source"] == "chat"
    assert db.created_run_kwargs["channel"] == "web"
    assert db.created_run_kwargs["external_id"] == "approval-message"
    assert db.created_run_kwargs["origin_metadata"] == {"approval": "metadata"}


@pytest.mark.asyncio
async def test_create_resume_run_without_request_id_reuses_stable_key(monkeypatch: pytest.MonkeyPatch):
    parent_run = SimpleNamespace(
        id="parent-run",
        conversation_thread_id="thread-1",
        status="interrupted",
        input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
    )
    first_db = _patch_agent_run_creation(monkeypatch, parent_run=parent_run)

    await agent_run_service.create_resume_run_view(
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

    result = await agent_run_service.create_resume_run_view(
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
        await agent_run_service.create_resume_run_view(
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
        await agent_run_service.create_resume_run_view(
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
        await agent_run_service.create_resume_run_view(
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
        await agent_run_service.create_resume_run_view(
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
async def test_create_resume_run_rejects_active_checkpoint_run(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "default"},
        ),
        active_run=SimpleNamespace(id="active-run", status="running"),
    )

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_resume_run_view(
            resume={"answer": "ok"},
            created_by_run_id="parent-run",
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
    output_message = SimpleNamespace(
        id=2,
        role="assistant",
        content="older",
        extra_metadata={"langfuse_trace_id": "trace-old"},
    )

    class RunOutputRepo:
        def __init__(self, db):
            assert db is fake_db

        async def get_output_message(self, **kwargs):
            assert kwargs == {
                "run_id": "run-1",
                "conversation_id": 10,
                "output_message_id": 2,
                "allow_legacy_fallback": True,
            }
            return output_message

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "run-1"
            assert uid == "user-1"
            return run

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunOutputRepository", RunOutputRepo)
    fake_db = object()

    payload = await agent_run_service.get_agent_run_result(run_id="run-1", current_uid="user-1", db=fake_db)

    assert payload["status"] == "completed"
    assert payload["output"] == "older"
    assert payload["final_message_id"] == 2
    assert payload["langfuse_trace_id"] == "trace-old"
    assert payload["timing"]["first_output_latency_ms"] is None
    assert "debug" not in payload


@pytest.mark.asyncio
async def test_get_agent_run_result_prefers_run_trace_without_final_message(monkeypatch: pytest.MonkeyPatch):
    run = SimpleNamespace(
        id="run-1",
        status="failed",
        agent_slug="default-chatbot",
        conversation_thread_id="thread-1",
        conversation_id=10,
        request_id="req-1",
        output_message_id=None,
        langfuse_trace_id="trace-run",
        error_type="model_error",
        error_message="failed before output",
    )

    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert (run_id, uid) == ("run-1", "user-1")
            return run

    class RunOutputRepo:
        def __init__(self, db):
            del db

        async def get_output_message(self, **_kwargs):
            return None

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunOutputRepository", RunOutputRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-1", current_uid="user-1", db=object())

    assert payload["output"] == ""
    assert payload["final_message_id"] is None
    assert payload["langfuse_trace_id"] == "trace-run"


@pytest.mark.asyncio
async def test_get_agent_run_result_does_not_fallback_when_explicit_binding_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
):
    run = SimpleNamespace(
        id="run-1",
        status="completed",
        agent_slug="default-chatbot",
        conversation_thread_id="thread-1",
        conversation_id=10,
        request_id="req-1",
        output_message_id=99,
        error_type=None,
        error_message=None,
    )
    output_queries: list[dict] = []

    class RunRepo:
        def __init__(self, db):
            del db

        async def get_run_for_user(self, run_id: str, uid: str):
            assert (run_id, uid) == ("run-1", "user-1")
            return run

    class RunOutputRepo:
        def __init__(self, db):
            del db

        async def get_output_message(self, **kwargs):
            output_queries.append(kwargs)
            return None

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "AgentRunOutputRepository", RunOutputRepo)

    payload = await agent_run_service.get_agent_run_result(run_id="run-1", current_uid="user-1", db=object())

    assert output_queries == [
        {
            "run_id": "run-1",
            "conversation_id": 10,
            "output_message_id": 99,
            "allow_legacy_fallback": True,
        }
    ]
    assert payload["output"] == ""
    assert payload["final_message_id"] is None
    assert payload["langfuse_trace_id"] is None


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
async def test_get_agent_run_langfuse_link_resolves_bound_trace(monkeypatch: pytest.MonkeyPatch):
    class FakeDb:
        committed = False

        async def commit(self):
            self.committed = True

    async def fake_result(*, run_id: str, current_uid: str, db):
        assert (run_id, current_uid, db) == ("run-1", "user-1", fake_db)
        return {"status": "completed", "langfuse_trace_id": "trace-1"}

    async def fake_trace_url(trace_id: str):
        assert trace_id == "trace-1"
        assert fake_db.committed is True
        return "https://langfuse.example/project/project-1/traces/trace-1"

    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_result)
    monkeypatch.setattr(agent_run_service, "get_trace_url_by_id_async", fake_trace_url)
    fake_db = FakeDb()

    payload = await agent_run_service.get_agent_run_langfuse_link(
        run_id="run-1",
        current_uid="user-1",
        db=fake_db,
    )

    assert payload == {
        "run_id": "run-1",
        "available": True,
        "url": "https://langfuse.example/project/project-1/traces/trace-1",
    }


@pytest.mark.asyncio
async def test_get_agent_run_langfuse_link_does_not_resolve_without_trace(monkeypatch: pytest.MonkeyPatch):
    async def fake_result(**_kwargs):
        return {"status": "completed", "langfuse_trace_id": None}

    async def unexpected_trace_url(_trace_id: str):
        raise AssertionError("无 trace 的 Run 不应调用 Langfuse")

    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_result)
    monkeypatch.setattr(agent_run_service, "get_trace_url_by_id_async", unexpected_trace_url)

    payload = await agent_run_service.get_agent_run_langfuse_link(
        run_id="run-1",
        current_uid="user-1",
        db=object(),
    )

    assert payload == {"run_id": "run-1", "available": False, "reason": "trace_not_available"}


@pytest.mark.asyncio
async def test_get_agent_run_langfuse_link_reports_optional_provider_unavailable(monkeypatch: pytest.MonkeyPatch):
    class FakeDb:
        async def commit(self):
            return None

    async def fake_result(**_kwargs):
        return {"status": "completed", "langfuse_trace_id": "trace-1"}

    async def fake_trace_url(_trace_id: str):
        return None

    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_result)
    monkeypatch.setattr(agent_run_service, "get_trace_url_by_id_async", fake_trace_url)

    payload = await agent_run_service.get_agent_run_langfuse_link(
        run_id="run-1",
        current_uid="user-1",
        db=FakeDb(),
    )

    assert payload == {"run_id": "run-1", "available": False, "reason": "langfuse_unavailable"}


@pytest.mark.asyncio
async def test_get_agent_run_langfuse_link_hides_missing_run(monkeypatch: pytest.MonkeyPatch):
    async def fake_result(**_kwargs):
        return {"status": "failed", "error": {"type": "run_not_found", "message": "运行任务不存在"}}

    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_result)

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.get_agent_run_langfuse_link(
            run_id="run-x",
            current_uid="user-1",
            db=object(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "运行任务不存在"


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
    signals: list[tuple[list[str], bool]] = []

    class Db:
        committed = False

        async def commit(self):
            self.committed = True

    class RunRepo:
        def __init__(self, db):
            self.db = db

        async def request_cancel_execution_tree(self, *, run_id: str, uid: str, cascade_descendants: bool):
            assert run_id == "parent-run"
            assert uid == "user-1"
            assert cascade_descendants is True
            requested.extend(["parent-run", *(child.id for child in child_runs)])
            return parent_run, list(requested)

    async def fake_publish_cancel_signals(run_ids: list[str]):
        signals.append((run_ids, db.committed))

    monkeypatch.setattr(agent_run_service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(agent_run_service, "publish_cancel_signals", fake_publish_cancel_signals)
    db = Db()

    result = await agent_run_service.cancel_agent_run_view(
        run_id="parent-run",
        current_uid="user-1",
        db=db,
    )

    assert result["run"]["id"] == "parent-run"
    assert requested == ["parent-run", "child-1", "child-2"]
    assert signals == [(["parent-run", "child-1", "child-2"], True)]


@pytest.mark.asyncio
async def test_resolve_agent_run_model_spec_rejects_unknown_explicit_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", lambda spec: None)
    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.resolve_agent_run_model_spec("nope", "default:model")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_agent_run_model_spec_rejects_non_chat_explicit_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="embedding"),
    )
    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.resolve_agent_run_model_spec("embed-1", "default:model")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_agent_run_model_spec_strips_explicit_chat_model(monkeypatch: pytest.MonkeyPatch):
    seen = []

    def fake_get_model_info(spec):
        seen.append(spec)
        return SimpleNamespace(model_type="chat")

    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", fake_get_model_info)

    assert await agent_run_service.resolve_agent_run_model_spec(" gpt-x ", "default:model") == "gpt-x"
    assert seen == ["gpt-x"]


@pytest.mark.asyncio
async def test_resolve_agent_run_model_spec_uses_configured_model_without_loading_system_default(
    monkeypatch: pytest.MonkeyPatch,
):
    async def unexpected_get(*_args):
        raise AssertionError("configured model should not read system default")

    monkeypatch.setattr(type(agent_run_service.system_options), "get", unexpected_get)
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat") if spec == "agent:model" else None,
    )

    assert await agent_run_service.resolve_agent_run_model_spec(None, " agent:model ") == "agent:model"


@pytest.mark.asyncio
async def test_resolve_agent_run_model_spec_validates_configured_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_run_service.model_cache, "get_model_info", lambda _spec: None)

    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.resolve_agent_run_model_spec(None, "missing:model")

    assert exc.value.status_code == 422


def _patch_agent_run_creation(
    monkeypatch: pytest.MonkeyPatch,
    *,
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
                config_json={"context": {}},
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

    async def get_system_options(_option, _db=None):
        return {"default_model": "system-default:model"}

    monkeypatch.setattr(type(agent_run_service.system_options), "get", get_system_options)
    monkeypatch.setattr(
        agent_run_service.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="chat"),
    )
    return db


@pytest.mark.asyncio
async def test_create_resume_run_inherits_parent_model_spec(monkeypatch: pytest.MonkeyPatch):
    db = _patch_agent_run_creation(
        monkeypatch,
        parent_run=SimpleNamespace(
            id="parent-run",
            conversation_thread_id="thread-1",
            status="interrupted",
            input_payload={"model_spec": "parent-model", "tool_approval_mode": "always_trust"},
        ),
    )

    await agent_run_service.create_resume_run_view(
        agent_slug="default",
        thread_id="thread-1",
        meta={"request_id": "resume-req"},
        current_uid="user-1",
        db=db,
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

    await agent_run_service.create_resume_run_view(
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
    assert agent_run_service.resolve_agent_run_tool_approval_mode("default", "always_trust") == "default"
    assert agent_run_service.resolve_agent_run_tool_approval_mode(None, "always_trust") == "always_trust"
    assert agent_run_service.resolve_agent_run_tool_approval_mode(None, None) == "default"


def test_resolve_tool_approval_mode_rejects_unknown_value():
    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service.resolve_agent_run_tool_approval_mode("unknown", None)

    assert exc.value.status_code == 422


def test_validate_resume_input_accepts_only_approve_and_reject_decisions():
    agent_run_service._validate_resume_input({"decisions": [{"type": "approve"}, {"type": "reject", "message": "no"}]})

    with pytest.raises(agent_run_service.HTTPException) as exc:
        agent_run_service._validate_resume_input({"decisions": [{"type": "edit"}]})

    assert exc.value.status_code == 422


@pytest.mark.parametrize(
    ("field", "chunk"),
    [
        (
            "compression",
            {
                "request_id": "req-1",
                "response": None,
                "thread_id": "thread-1",
                "status": "context_compression",
                "compression": {"type": "yuxi.context_compression", "status": "started"},
                "meta": {"uid": "user-1"},
            },
        ),
        (
            "approval",
            {
                "status": "human_approval_required",
                "run_id": "run-1",
                "approval": {
                    "action_requests": [{"name": "execute", "args": {"command": "pytest -q"}}],
                    "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
                },
            },
        ),
    ],
)
def test_compact_stream_chunk_retains_status_and_field(field: str, chunk: dict):
    compact = agent_run_service._compact_stream_chunk(chunk)

    assert compact["status"] == chunk["status"]
    assert compact[field] == chunk[field]


@pytest.mark.asyncio
async def test_create_resume_run_rejects_missing_resume():
    """恢复入口不接受缺失恢复内容的普通消息调用。"""
    with pytest.raises(agent_run_service.HTTPException) as exc:
        await agent_run_service.create_resume_run_view(
            agent_slug="default",
            thread_id="thread-1",
            current_uid="user-1",
            db=None,
            meta={},
            resume=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "resume 不能为空"
