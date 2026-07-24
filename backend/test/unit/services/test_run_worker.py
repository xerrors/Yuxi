from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import yuxi.services.run_worker as run_worker


class _RaisingAsyncIter:
    def __init__(self, exc: Exception):
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


class _BytesAsyncIter:
    def __init__(self, values: list[bytes]):
        self._values = list(values)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._values):
            raise StopAsyncIteration
        value = self._values[self._idx]
        self._idx += 1
        return value


def _build_run() -> SimpleNamespace:
    return SimpleNamespace(
        id="run-1",
        status="pending",
        request_id="req-1",
        input_payload={"model_spec": "provider:model"},
        input_message_id=10,
        run_type="chat",
        agent_slug="ChatbotAgent",
        uid="user-1",
        conversation_thread_id="thread-1",
        created_by_run_id=None,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, run_obj: SimpleNamespace):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_noop(*args, **kwargs):
        del args, kwargs
        return None

    async def fake_get_run(run_id: str):
        del run_id
        return run_obj

    async def fake_load_user(uid: str):
        del uid
        return SimpleNamespace(id=1, uid="user-1")

    async def fake_load_input_message(message_id: int | None):
        assert message_id == 10
        return SimpleNamespace(content="hello", image_content=None, extra_metadata={})

    async def fake_not_cancelled(self):
        del self
        return False

    async def fake_no_handoff(run_id: str):
        return SimpleNamespace(target_run_id=run_id, request_id=None, new_run_id=None, changed=False)

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "_get_run", fake_get_run)
    monkeypatch.setattr(run_worker, "_load_user", fake_load_user)
    monkeypatch.setattr(run_worker, "_load_input_message", fake_load_input_message)
    monkeypatch.setattr(run_worker, "mark_run_running", fake_noop)
    monkeypatch.setattr(run_worker, "clear_cancel_signal", fake_noop)
    monkeypatch.setattr(run_worker, "finalize_ready_steer_handoff", fake_no_handoff)
    monkeypatch.setattr(run_worker, "settle_target_steer_after_terminal", fake_noop)
    monkeypatch.setattr(run_worker, "stream_agent_chat", lambda **kwargs: object())
    monkeypatch.setattr(run_worker.RunContext, "start", fake_noop)
    monkeypatch.setattr(run_worker.RunContext, "close", fake_noop)
    monkeypatch.setattr(run_worker.RunContext, "is_cancelled", fake_not_cancelled)


@pytest.mark.asyncio
async def test_process_agent_run_restores_invocation_meta(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    captured: dict[str, object] = {}
    events: list[dict] = []
    terminal_statuses: list[str] = []

    async def fake_load_input_message(message_id: int | None):
        assert message_id == 10
        return SimpleNamespace(
            content="hello",
            image_content=None,
            extra_metadata={
                "source": "agent_call",
                "agent_invocation_meta": {"trace_id": "trace-1"},
                "evaluation": {"dataset_name": "legacy-top-level"},
                "custom_variables": {"system_prompt": "legacy"},
            },
        )

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del kwargs
        events.append({"run_id": run_id, "event_type": event_type, "payload": payload})

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, error_type, error_message
        terminal_statuses.append(status)
        return run_worker.TerminalTransition(status=status, changed=True)

    def fake_stream_agent_chat(**kwargs):
        captured.update(kwargs)
        return _BytesAsyncIter([b'{"status":"finished","request_id":"req-1","thread_id":"thread-1"}\n'])

    monkeypatch.setattr(run_worker, "_load_input_message", fake_load_input_message)
    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    meta = captured["meta"]
    assert meta["source"] == "agent_call"
    assert meta["agent_invocation_meta"] == {"trace_id": "trace-1"}
    assert "evaluation" not in meta
    assert "custom_variables" not in meta
    metadata_event = next(event for event in events if event["event_type"] == "metadata")
    assert metadata_event["payload"]["agent_invocation_meta"] == {"trace_id": "trace-1"}
    assert "evaluation" not in metadata_event["payload"]
    assert "custom_variables" not in metadata_event["payload"]
    assert terminal_statuses == ["completed"]


@pytest.mark.asyncio
async def test_process_agent_run_non_retryable_error_marks_failed(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    terminal_statuses: list[str] = []
    events: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, payload, kwargs
        events.append(event_type)

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, error_type, error_message
        terminal_statuses.append(status)
        return run_worker.TerminalTransition(status=status, changed=True)

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(
        run_worker,
        "_consume_stream_with_cancel",
        lambda stream, run_ctx: _RaisingAsyncIter(RuntimeError("boom")),
    )

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert "error" in events
    assert terminal_statuses == ["failed"]


@pytest.mark.asyncio
async def test_process_agent_run_retryable_error_retries_then_completes(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    terminal_statuses: list[str] = []
    events: list[dict] = []
    attempts = {"count": 0}

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append({"event_type": event_type, "payload": payload})

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, error_type, error_message
        terminal_statuses.append(status)
        return run_worker.TerminalTransition(status=status, changed=True)

    def fake_consume(stream, run_ctx):
        del stream, run_ctx
        attempts["count"] += 1
        if attempts["count"] == 1:
            return _RaisingAsyncIter(run_worker.RetryableRunError("temporary failure"))
        return _BytesAsyncIter([b'{"status":"finished","request_id":"req-1"}\n'])

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "_consume_stream_with_cancel", fake_consume)

    with pytest.raises(run_worker.RetryableRunError):
        await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert terminal_statuses == []
    assert any(
        item["event_type"] == "error" and item["payload"]["chunk"].get("error_type") == "retryable_worker_error"
        for item in events
    )

    await run_worker.process_agent_run({"job_try": 2}, "run-1")
    assert terminal_statuses == ["completed"]


@pytest.mark.asyncio
async def test_ready_steer_retry_handoffs_without_replaying_input(monkeypatch: pytest.MonkeyPatch):
    """旧 job 在 steer_ready 后重试时直接交接，不再次进入 Graph。"""
    run_obj = _build_run()
    run_obj.status = "running"
    _patch_common(monkeypatch, run_obj)
    events: list[tuple[str, dict]] = []
    enqueued: list[str] = []

    async def fake_handoff(run_id: str):
        return SimpleNamespace(
            target_run_id=run_id,
            request_id="steer-request",
            new_run_id="replacement-run",
            changed=True,
        )

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append((event_type, payload))

    async def fake_enqueue(run_id: str):
        enqueued.append(run_id)

    def fail_stream_agent_chat(**kwargs):
        del kwargs
        raise AssertionError("steer_ready retry must not replay the original input")

    monkeypatch.setattr(run_worker, "finalize_ready_steer_handoff", fake_handoff)
    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "enqueue_agent_run", fake_enqueue)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fail_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 2}, "run-1")

    assert events == [
        (
            "end",
            {
                "status": "cancelled",
                "reason": "steered",
                "replacement_request_id": "steer-request",
                "replacement_run_id": "replacement-run",
            },
        )
    ]
    assert enqueued == ["replacement-run"]


@pytest.mark.asyncio
async def test_terminal_steered_retry_recovers_existing_replacement(monkeypatch: pytest.MonkeyPatch):
    """旧 Run 已提交交接时，job 重试只恢复既有 replacement。"""
    run_obj = _build_run()
    run_obj.status = "cancelled"
    run_obj.error_type = "steered"
    _patch_common(monkeypatch, run_obj)
    recovered: list[str] = []

    async def fake_recover(run_id: str):
        recovered.append(run_id)
        return "replacement-run"

    async def fail_settle(run_id: str):
        raise AssertionError(f"steered terminal run must not settle pending request: {run_id}")

    monkeypatch.setattr(run_worker, "recover_steered_replacement", fake_recover)
    monkeypatch.setattr(run_worker, "settle_target_steer_after_terminal", fail_settle)

    await run_worker.process_agent_run({"job_try": 2}, "run-1")

    assert recovered == ["run-1"]


@pytest.mark.asyncio
async def test_cancel_requested_wins_race_before_completed(monkeypatch: pytest.MonkeyPatch):
    """handoff 未生效后若取消抢先，worker 收口为 cancelled 而不是 completed。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    terminal_attempts: list[str] = []
    settled: list[str] = []

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        return _BytesAsyncIter([b'{"status":"finished","request_id":"req-1"}\n'])

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, error_type, error_message
        terminal_attempts.append(status)
        if status == "completed":
            return run_worker.TerminalTransition(status="cancel_requested", changed=False)
        return run_worker.TerminalTransition(status="cancelled", changed=True)

    async def fake_settle(run_id: str):
        settled.append(run_id)

    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "settle_target_steer_after_terminal", fake_settle)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert terminal_attempts == ["completed", "cancelled"]
    assert settled == ["run-1"]


@pytest.mark.asyncio
async def test_finish_run_terminal_loser_does_not_append_end_event(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple[str, dict]] = []

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, status, error_type, error_message
        return run_worker.TerminalTransition(status="cancelled", changed=False)

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append((event_type, payload))

    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)

    transition = await run_worker._finish_run(
        "run-1",
        "completed",
        thread_id="thread-1",
        chunk={"status": "finished"},
    )

    assert transition == run_worker.TerminalTransition(status="cancelled", changed=False)
    assert events == []


@pytest.mark.asyncio
async def test_process_subagent_run_restores_runtime_context(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    run_obj.run_type = "subagent"
    run_obj.agent_slug = "worker"
    run_obj.conversation_thread_id = "child-thread"
    run_obj.created_by_run_id = "parent-run"
    run_obj.input_payload = {
        "model_spec": "provider:model",
        "runtime": {
            "parent_thread_id": "parent-thread",
            "file_thread_id": "shared-file-thread",
            "skills_thread_id": "child-thread",
        },
    }
    _patch_common(monkeypatch, run_obj)

    captured: dict[str, object] = {}
    terminal_statuses: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, event_type, payload, kwargs

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        del run_id, error_type, error_message
        terminal_statuses.append(status)
        return run_worker.TerminalTransition(status=status, changed=True)

    def fake_stream_agent_chat(**kwargs):
        captured.update(kwargs)
        return _BytesAsyncIter([b'{"status":"finished","request_id":"req-1","thread_id":"child-thread"}\n'])

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    meta = captured["meta"]
    assert meta["run_type"] == "subagent"
    assert meta["parent_thread_id"] == "parent-thread"
    assert meta["file_thread_id"] == "shared-file-thread"
    assert meta["skills_thread_id"] == "child-thread"
    assert captured["agent_slug"] == "worker"
    assert captured["thread_id"] == "child-thread"
    assert captured["input_message"].content == "hello"
    assert captured["input_message"].langchain_message.content == "hello"
    assert "image_content" not in captured
    assert terminal_statuses == ["completed"]


@pytest.mark.asyncio
async def test_process_agent_run_rejects_unknown_run_type(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    run_obj.run_type = "unknown"
    _patch_common(monkeypatch, run_obj)

    terminal_errors: list[dict] = []

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        terminal_errors.append(
            {
                "run_id": run_id,
                "status": status,
                "error_type": error_type,
                "error_message": error_message,
            }
        )
        return run_worker.TerminalTransition(status=status, changed=True)

    def fail_stream_agent_chat(**kwargs):
        del kwargs
        raise AssertionError("unknown run_type must not enter chat stream")

    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fail_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert terminal_errors == [
        {
            "run_id": "run-1",
            "status": "failed",
            "error_type": "invalid_run_type",
            "error_message": "不支持的 run_type: unknown",
        }
    ]


@pytest.mark.asyncio
async def test_process_agent_run_rejects_invalid_raw_input_message(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    terminal_errors: list[dict] = []

    async def fake_load_input_message(message_id: int | None):
        assert message_id == 10
        return SimpleNamespace(
            content="hello",
            image_content=None,
            extra_metadata={"raw_message": {"type": "human", "content": object()}},
        )

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None):
        terminal_errors.append(
            {
                "run_id": run_id,
                "status": status,
                "error_type": error_type,
                "error_message": error_message,
            }
        )
        return run_worker.TerminalTransition(status=status, changed=True)

    def fail_stream_agent_chat(**kwargs):
        del kwargs
        raise AssertionError("invalid input message must not enter chat stream")

    monkeypatch.setattr(run_worker, "_load_input_message", fake_load_input_message)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fail_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert terminal_errors == [
        {
            "run_id": "run-1",
            "status": "failed",
            "error_type": "invalid_input_message",
            "error_message": "invalid raw_message for chat input message",
        }
    ]


@pytest.mark.asyncio
async def test_chunked_event_writer_flushes_loading_chunks_by_thread(monkeypatch: pytest.MonkeyPatch):
    events: list[dict] = []

    async def fake_append_run_event(run_id: str, event_type: str, payload: dict, *, thread_id: str | None = None):
        events.append({"run_id": run_id, "event_type": event_type, "payload": payload, "thread_id": thread_id})

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_run_event)

    writer = run_worker.ChunkedEventWriter("run-1", "parent-thread")
    await writer.append({"status": "loading", "response": "parent", "thread_id": "parent-thread"})
    await writer.append({"status": "loading", "response": "child", "thread_id": "child-thread"})
    await writer.flush()

    assert events == [
        {
            "run_id": "run-1",
            "event_type": "messages",
            "payload": {"items": [{"status": "loading", "response": "parent", "thread_id": "parent-thread"}]},
            "thread_id": "parent-thread",
        },
        {
            "run_id": "run-1",
            "event_type": "messages",
            "payload": {"items": [{"status": "loading", "response": "child", "thread_id": "child-thread"}]},
            "thread_id": "child-thread",
        },
    ]


@pytest.mark.asyncio
async def test_chunked_event_writer_flushes_semantic_tool_call_immediately(monkeypatch: pytest.MonkeyPatch):
    events: list[dict] = []

    async def fake_append_run_event(run_id: str, event_type: str, payload: dict, *, thread_id: str | None = None):
        events.append({"run_id": run_id, "event_type": event_type, "payload": payload, "thread_id": thread_id})

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_run_event)

    writer = run_worker.ChunkedEventWriter("run-1", "parent-thread")
    chunk = {
        "status": "loading",
        "response": "",
        "thread_id": "parent-thread",
        "stream_event": {
            "type": "tool_call",
            "message_id": "msg-1",
            "tool_call_id": "call-1",
            "name": "task",
            "args": {"description": "do work"},
            "index": 0,
            "thread_id": "parent-thread",
            "namespace": [],
        },
    }
    await writer.append(chunk)

    assert events == [
        {
            "run_id": "run-1",
            "event_type": "messages",
            "payload": {"items": [chunk]},
            "thread_id": "parent-thread",
        }
    ]


def test_chunk_thread_id_uses_fallback_for_unstable_nested_metadata():
    assert (
        run_worker._chunk_thread_id(
            {"metadata": {"configurable": {"thread_id": "child-thread"}}},
            "parent-thread",
        )
        == "parent-thread"
    )


@pytest.mark.asyncio
async def test_worker_startup_ensures_builtin_mcp_servers(monkeypatch: pytest.MonkeyPatch):
    from yuxi.config import options

    calls: list[str] = []

    def fake_initialize():
        calls.append("initialize")

    async def fake_create_business_tables():
        calls.append("create_business_tables")

    async def fake_ensure_business_schema():
        calls.append("ensure_business_schema")

    async def fake_ensure_builtin_mcp_servers_in_db():
        calls.append("ensure_builtin_mcp_servers_in_db")

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_init_builtin_skills(session):
        del session
        calls.append("init_builtin_skills")

    async def fake_ensure_options_in_db(session):
        del session

    def fake_start_runtime_sync():
        calls.append("start_runtime_sync")

    async def fake_recover_pending_dispatches():
        calls.append("recover_pending_dispatches")

    monkeypatch.setattr(run_worker.pg_manager, "initialize", fake_initialize)
    monkeypatch.setattr(run_worker.pg_manager, "create_business_tables", fake_create_business_tables)
    monkeypatch.setattr(run_worker.pg_manager, "ensure_business_schema", fake_ensure_business_schema)
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "ensure_builtin_mcp_servers_in_db", fake_ensure_builtin_mcp_servers_in_db)
    monkeypatch.setattr(run_worker, "init_builtin_skills", fake_init_builtin_skills)
    monkeypatch.setattr(options, "ensure_options_in_db", fake_ensure_options_in_db)
    monkeypatch.setattr(run_worker.sys_config, "start_runtime_sync", fake_start_runtime_sync)
    monkeypatch.setattr(run_worker, "recover_pending_dispatches", fake_recover_pending_dispatches)

    await run_worker._worker_startup({})

    assert calls == [
        "initialize",
        "create_business_tables",
        "ensure_business_schema",
        "ensure_builtin_mcp_servers_in_db",
        "init_builtin_skills",
        "start_runtime_sync",
        "recover_pending_dispatches",
    ]
