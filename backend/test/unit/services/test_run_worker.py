from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yuxi.services.run_worker as run_worker
from arq.worker import RetryJob
from yuxi.config import options as config_options


@pytest.fixture(autouse=True)
def api_key_derivation_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-that-is-at-least-32-chars")
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "test-api-key-derivation-secret-32-chars")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "test-sandbox-token-that-is-at-least-32-chars")


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
        conversation_id=7,
        conversation_thread_id="thread-1",
        runtime_scope_id="thread-1",
        runtime_cleanup_pending=False,
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )


@pytest.mark.asyncio
async def test_validate_run_workdir_binding_rejects_top_level_foreign_runtime_scope(
    monkeypatch: pytest.MonkeyPatch,
):
    run = _build_run()
    run.runtime_scope_id = "other-thread"

    @asynccontextmanager
    async def fake_session():
        yield object()

    async def fake_resolve(**_kwargs):
        return SimpleNamespace(conversation_id=run.conversation_id)

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(run_worker, "resolve_authorized_workdir", fake_resolve)

    with pytest.raises(run_worker.NonRetryableRunError, match="Chat AgentRun"):
        await run_worker._validate_run_workdir_binding(run)


@pytest.mark.asyncio
async def test_validate_run_workdir_binding_requires_subagent_creator_tree(
    monkeypatch: pytest.MonkeyPatch,
):
    run = _build_run()
    run.run_type = "subagent"
    run.conversation_thread_id = "child-thread"
    run.runtime_scope_id = "root-thread"
    run.created_by_run_id = "creator-run"
    run.subagent_thread_relation_id = 3
    creator = SimpleNamespace(
        id="creator-run",
        run_type="chat",
        conversation_thread_id="root-thread",
        runtime_scope_id="root-thread",
        conversation_id=2,
        created_by_run_id=None,
        subagent_thread_relation_id=None,
    )

    @asynccontextmanager
    async def fake_session():
        yield object()

    async def fake_resolve(**kwargs):
        if kwargs["thread_id"] == "child-thread":
            return SimpleNamespace(
                conversation_id=run.conversation_id,
                workdir_path="projects/shared",
                project_id="project-1",
            )
        assert kwargs["thread_id"] == "root-thread"
        return SimpleNamespace(
            conversation_id=creator.conversation_id,
            workdir_path="projects/shared",
            project_id="project-1",
        )

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_subagent_run_with_creator(self, **kwargs):
            assert kwargs == {
                "uid": "user-1",
                "created_by_run_id": "creator-run",
                "run_id": "run-1",
            }
            return creator, run

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(run_worker, "resolve_authorized_workdir", fake_resolve)
    monkeypatch.setattr(run_worker, "AgentRunRepository", RunRepo)

    binding = await run_worker._validate_run_workdir_binding(run)
    assert binding.conversation_id == run.conversation_id

    original_resolve = fake_resolve

    async def resolve_different_project(**kwargs):
        binding = await original_resolve(**kwargs)
        if kwargs["thread_id"] == "child-thread":
            binding.project_id = "project-2"
        return binding

    monkeypatch.setattr(run_worker, "resolve_authorized_workdir", resolve_different_project)
    with pytest.raises(run_worker.NonRetryableRunError, match="SubAgent Run"):
        await run_worker._validate_run_workdir_binding(run)

    monkeypatch.setattr(run_worker, "resolve_authorized_workdir", fake_resolve)
    creator.runtime_scope_id = "corrupted-root"
    with pytest.raises(run_worker.NonRetryableRunError, match="SubAgent Run"):
        await run_worker._validate_run_workdir_binding(run)


@pytest.mark.asyncio
async def test_cancelling_subagent_preserves_shared_runtime(monkeypatch: pytest.MonkeyPatch):
    run = _build_run()
    run.run_type = "subagent"

    async def fake_noop(*args, **kwargs):
        del args, kwargs
        return None

    async def fake_tree_finished(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_mark_terminal(*args, **kwargs):
        del args, kwargs
        return run_worker.TerminalTransition(status="cancelled", changed=True)

    monkeypatch.setattr(run_worker, "_flush_writer_best_effort", fake_noop)
    monkeypatch.setattr(run_worker, "_finish_execution_tree_children", fake_tree_finished)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "_append_run_event_best_effort", fake_noop)
    monkeypatch.setattr(run_worker, "_append_end_event", fake_noop)

    await run_worker._finish_user_cancel(
        run_id=run.id,
        request_id=run.request_id,
        thread_id=run.conversation_thread_id,
        current_user=None,
        worker_id="worker-1",
        writer=SimpleNamespace(),
        run=run,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, run_obj: SimpleNamespace):
    @asynccontextmanager
    async def fake_session_ctx():
        yield SimpleNamespace(commit=AsyncMock())

    async def fake_noop(*args, **kwargs):
        del args, kwargs
        return None

    async def fake_cleanup(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_mark_run_running(*args, **kwargs):
        del args, kwargs
        return True

    async def fake_get_run(run_id: str):
        del run_id
        return run_obj

    async def fake_load_user(uid: str):
        del uid
        return SimpleNamespace(id=1, uid="user-1")

    async def fake_load_input_message(message_id: int | None):
        assert message_id == 10
        return SimpleNamespace(content="hello", image_content=None, extra_metadata={})

    async def fake_get_agent_state_view(**kwargs):
        del kwargs
        return {"agent_state": None}

    async def fake_not_cancelled(self):
        del self
        return False

    async def fake_tree_finished(*args, **kwargs):
        del args, kwargs
        return True

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "_get_run", fake_get_run)
    monkeypatch.setattr(run_worker, "_load_user", fake_load_user)
    monkeypatch.setattr(run_worker, "_load_input_message", fake_load_input_message)
    monkeypatch.setattr(run_worker, "get_agent_state_view", fake_get_agent_state_view)
    monkeypatch.setattr(run_worker, "mark_run_running", fake_mark_run_running)
    monkeypatch.setattr(run_worker, "release_run_lease_for_retry", fake_mark_run_running)
    monkeypatch.setattr(run_worker, "persist_run_manifest", fake_noop)
    monkeypatch.setattr(
        run_worker,
        "_validate_run_workdir_binding",
        AsyncMock(
            return_value=SimpleNamespace(
                workdir_path="projects/11111111-1111-4111-8111-111111111111",
                virtual_path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
            )
        ),
    )
    monkeypatch.setattr(run_worker, "_finish_execution_tree_children", fake_tree_finished)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fake_cleanup)
    monkeypatch.setattr(run_worker, "clear_cancel_signal", fake_noop)
    monkeypatch.setattr(run_worker, "stream_agent_chat", lambda **kwargs: object())
    monkeypatch.setattr(run_worker.RunContext, "start", fake_noop)
    monkeypatch.setattr(run_worker.RunContext, "close", fake_noop)
    monkeypatch.setattr(run_worker.RunContext, "is_cancelled", fake_not_cancelled)
    monkeypatch.setattr(
        run_worker,
        "get_sandbox_provider",
        lambda: SimpleNamespace(release=lambda *_args, **_kwargs: None),
    )


@pytest.mark.asyncio
async def test_process_agent_run_rejects_corrupted_runtime_scope_before_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    run_obj = _build_run()
    run_obj.runtime_scope_id = "other-root-thread"
    _patch_common(monkeypatch, run_obj)
    terminal_calls: list[dict] = []
    stream_called = False

    async def reject_binding(_run):
        raise run_worker.NonRetryableRunError("AgentRun 的 runtime scope 与 Project Workdir 绑定不一致")

    async def fake_mark_terminal(run_id: str, status: str, *args, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, "args": args, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    def fake_stream_agent_chat(**_kwargs):
        nonlocal stream_called
        stream_called = True
        return _BytesAsyncIter([])

    monkeypatch.setattr(run_worker, "_validate_run_workdir_binding", reject_binding)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, run_obj.id)

    assert stream_called is False
    assert terminal_calls[0]["status"] == "failed"
    assert terminal_calls[0]["args"][0] == "invalid_runtime_scope"


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

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
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
async def test_terminal_event_is_published_after_runtime_cleanup(monkeypatch: pytest.MonkeyPatch):
    """客户端看到 end 时，旧 runtime 必须已经释放，避免实时文件请求撞上删除。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    lifecycle: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, payload, kwargs
        if event_type == "end":
            lifecycle.append("end")

    async def fake_release_runtime(run):
        assert run is run_obj
        lifecycle.append("release")
        return True

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        return _BytesAsyncIter([b'{"status":"finished","thread_id":"thread-1","terminal_committed":true}\n'])

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fake_release_runtime)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert lifecycle[:2] == ["release", "end"]


@pytest.mark.asyncio
async def test_terminal_cleanup_failure_keeps_end_event_unpublished(monkeypatch: pytest.MonkeyPatch):
    """cleanup 失败必须保留 durable fence，不能先向客户端宣告 execution tree 已结束。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    end_event = AsyncMock()

    async def fail_cleanup(_run):
        raise RuntimeError("provisioner delete failed")

    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fail_cleanup)
    monkeypatch.setattr(run_worker, "_append_end_event", end_event)
    monkeypatch.setattr(
        run_worker,
        "stream_agent_chat",
        lambda **_kwargs: _BytesAsyncIter(
            [b'{"status":"finished","thread_id":"thread-1","terminal_committed":true}\n']
        ),
    )

    with pytest.raises(run_worker.RuntimeCleanupPendingError):
        await run_worker.process_agent_run({"job_try": 1}, "run-1")

    end_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_reconciler_reenqueues_pending_retry_without_worker_restart(
    monkeypatch: pytest.MonkeyPatch,
):
    """ARQ 尝试耗尽后，周期 cleanup 成功必须重新投递同一个 pending Run。"""
    run_obj = _build_run()
    run_obj.status = "pending"
    run_obj.runtime_cleanup_pending = True

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    class Repo:
        def __init__(self, _db):
            pass

        async def list_pending_runtime_cleanups(self):
            return [run_obj]

    cleanup = AsyncMock(return_value=True)
    dispatch = AsyncMock(return_value=run_obj.id)
    append_end = AsyncMock()
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "AgentRunRepository", Repo)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", cleanup)
    monkeypatch.setattr(run_worker, "dispatch_next_request", dispatch)
    monkeypatch.setattr(run_worker, "_append_end_event", append_end)

    cleaned = await run_worker.reconcile_pending_runtime_cleanups()

    assert cleaned == [run_obj.id]
    dispatch.assert_awaited_once_with(
        uid=run_obj.uid,
        agent_slug=run_obj.agent_slug,
        thread_id=run_obj.conversation_thread_id,
    )
    append_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_agent_run_persists_usage_from_canonical_state(
    monkeypatch: pytest.MonkeyPatch,
):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    terminal_calls: list[dict] = []

    async def fake_append_event(*args, **kwargs):
        del args, kwargs

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    async def fake_get_agent_state_view(**kwargs):
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["current_user"].uid == "user-1"
        return {
            "agent_state": {
                "token_usage": {
                    "current_run_id": "run-1",
                    "run": {
                        "schema_version": 2,
                        "models": {"provider:model": {}},
                        "total": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                    },
                }
            }
        }

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        return _BytesAsyncIter(
            [
                (
                    b'{"status":"agent_state","thread_id":"thread-1","agent_state":{"token_usage":'
                    b'{"current_run_id":"run-1","run":{"schema_version":2,"models":{"provider:model":{}},'
                    b'"total":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}}}\n'
                ),
                (
                    b'{"status":"agent_state","thread_id":"child-thread","agent_state":{"token_usage":'
                    b'{"current_run_id":"run-1","run":{"schema_version":2,"models":{"child:model":{}},'
                    b'"total":{"input_tokens":999,"output_tokens":1,"total_tokens":1000}}}}}\n'
                ),
                (
                    b'{"status":"agent_state","thread_id":"thread-1","agent_state":{"token_usage":'
                    b'{"current_run_id":"other-run","run":{"schema_version":2,"models":{"other:model":{}},'
                    b'"total":{"input_tokens":500,"output_tokens":5,"total_tokens":505}}}}}\n'
                ),
                (
                    b'{"status":"finished","request_id":"req-1","thread_id":"thread-1",'
                    b'"token_usage":{"schema_version":2,"models":{"terminal:model":{}},'
                    b'"total":{"input_tokens":700,"output_tokens":7,"total_tokens":707}}}\n'
                ),
            ]
        )

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "get_agent_state_view", fake_get_agent_state_view)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert terminal_calls[0]["status"] == "completed"
    assert terminal_calls[0]["token_usage"] == {
        "schema_version": 2,
        "models": {"provider:model": {}},
        "total": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }


@pytest.mark.asyncio
async def test_read_run_token_usage_from_state_rejects_other_run(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fake_get_agent_state_view(**kwargs):
        del kwargs
        return {
            "agent_state": {
                "token_usage": {
                    "current_run_id": "old-run",
                    "run": {"schema_version": 2, "models": {}, "total": {"total_tokens": 99}},
                }
            }
        }

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "get_agent_state_view", fake_get_agent_state_view)

    usage = await run_worker._read_run_token_usage_from_state(
        run_id="run-1",
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert usage is None


@pytest.mark.asyncio
async def test_finish_run_marks_usage_unavailable_when_state_read_fails(monkeypatch: pytest.MonkeyPatch):
    terminal_calls: list[dict] = []

    async def fake_read_usage(**kwargs):
        del kwargs
        return None

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    async def fake_append_end_event(*args, **kwargs):
        del args, kwargs

    async def fake_get_run(_run_id: str):
        return None

    monkeypatch.setattr(run_worker, "_read_run_token_usage_from_state", fake_read_usage)
    monkeypatch.setattr(run_worker, "_get_run", fake_get_run)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "_append_end_event", fake_append_end_event)

    await run_worker._finish_run(
        "run-1",
        "completed",
        thread_id="thread-1",
        chunk={"status": "finished"},
        current_user=SimpleNamespace(uid="user-1"),
        worker_id="worker-1:attempt-1",
    )

    assert terminal_calls[0]["token_usage"] == {"available": False}


@pytest.mark.asyncio
async def test_process_agent_run_publishes_interrupt_after_final_state(monkeypatch: pytest.MonkeyPatch):
    """审批中断必须在最终状态落流后结束，避免前端过早刷新历史。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    events: list[dict] = []
    terminal_statuses: list[str] = []
    lifecycle: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append({"event_type": event_type, "payload": payload})
        if event_type in {"interrupt", "end"}:
            lifecycle.append(event_type)

    async def fake_release_runtime(run):
        assert run is run_obj
        lifecycle.append("release")
        return True

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
        terminal_statuses.append(status)
        # chat_service 已在 Message/revision 的 owning transaction 内提交 interrupted。
        return run_worker.TerminalTransition(status=status, changed=False)

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        return _BytesAsyncIter(
            [
                (
                    b'{"status":"human_approval_required","thread_id":"thread-1","approval":'
                    b'{"action_requests":[{"name":"execute","args":{"command":"python app.py"}}],'
                    b'"review_configs":[{"action_name":"execute",'
                    b'"allowed_decisions":["approve","reject"]}]}}\n'
                ),
                b'{"status":"agent_state","thread_id":"thread-1","agent_state":{"artifacts":["/home/gem/user-data/outputs/app.py"]}}\n',
            ]
        )

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fake_release_runtime)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert [event["event_type"] for event in events] == ["metadata", "custom", "interrupt", "end"]
    assert terminal_statuses == ["interrupted"]
    assert lifecycle == ["release", "interrupt", "end"]


@pytest.mark.asyncio
async def test_committed_interrupt_cleanup_failure_keeps_terminal_events_unpublished(
    monkeypatch: pytest.MonkeyPatch,
):
    """Chat 已提交 interrupted 时，runtime cleanup 失败仍不能发布 interrupt/end。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    events: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, payload, kwargs
        events.append(event_type)

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
        return run_worker.TerminalTransition(status=status, changed=False)

    async def fail_cleanup(_run):
        raise RuntimeError("provisioner delete failed")

    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fail_cleanup)
    monkeypatch.setattr(
        run_worker,
        "stream_agent_chat",
        lambda **_kwargs: _BytesAsyncIter(
            [b'{"status":"interrupted","thread_id":"thread-1","message":"content guard","terminal_committed":true}\n']
        ),
    )

    with pytest.raises(run_worker.RuntimeCleanupPendingError):
        await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert events == ["metadata"]


@pytest.mark.asyncio
async def test_process_agent_run_non_retryable_error_marks_failed(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    terminal_statuses: list[str] = []
    events: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, payload, kwargs
        events.append(event_type)

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
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
async def test_preflight_failure_after_lease_closes_context_and_writes_owned_terminal(
    monkeypatch: pytest.MonkeyPatch,
):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    closed: list[str] = []
    terminal_calls: list[dict] = []

    async def fail_input_load(_message_id):
        raise RuntimeError("preflight failed")

    async def fake_close(context):
        closed.append(context.worker_id)

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    monkeypatch.setattr(run_worker, "_load_input_message", fail_input_load)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker.RunContext, "close", fake_close)

    await run_worker.process_agent_run({"worker_id": "worker-preflight", "job_try": 1}, "run-1")

    assert terminal_calls[0]["status"] == "failed"
    assert terminal_calls[0]["error_type"] == "worker_error"
    assert terminal_calls[0]["worker_id"].startswith("worker-preflight:")
    assert closed == [terminal_calls[0]["worker_id"]]


@pytest.mark.asyncio
async def test_redis_event_failure_cannot_block_owned_completed_terminal(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    terminal_calls: list[dict] = []
    closed: list[str] = []

    async def fail_event(*args, **kwargs):
        del args, kwargs
        raise ConnectionError("redis unavailable")

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    async def fake_close(context):
        closed.append(context.worker_id)

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        return _BytesAsyncIter([b'{"status":"finished","thread_id":"thread-1"}\n'])

    monkeypatch.setattr(run_worker, "append_run_event", fail_event)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "release_run_lease_for_retry", AsyncMock(side_effect=AssertionError("no retry")))
    monkeypatch.setattr(run_worker.RunContext, "close", fake_close)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"worker_id": "worker-events", "job_try": 1}, "run-1")

    assert [call["status"] for call in terminal_calls] == ["completed"]
    assert terminal_calls[0]["worker_id"].startswith("worker-events:")
    assert closed == [terminal_calls[0]["worker_id"]]


@pytest.mark.asyncio
async def test_durable_cancel_without_redis_signal_never_enters_agent_stream(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    run_obj.status = "cancel_requested"
    _patch_common(monkeypatch, run_obj)
    terminal_calls: list[dict] = []
    lifecycle: list[str] = []

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        lifecycle.append("terminal")
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        run_obj.status = status
        return run_worker.TerminalTransition(status=status, changed=True)

    def forbidden_stream(**kwargs):
        del kwargs
        raise AssertionError("durably cancelled run must not execute")

    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", forbidden_stream)

    async def fake_release_sandbox(_run):
        lifecycle.append("release")
        return True

    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fake_release_sandbox)

    await run_worker.process_agent_run({"worker_id": "worker-cancel", "job_try": 1}, "run-1")

    assert [call["status"] for call in terminal_calls] == ["cancelled"]
    assert terminal_calls[0]["worker_id"].startswith("worker-cancel:")
    assert lifecycle == ["terminal", "release"]


@pytest.mark.asyncio
async def test_infrastructure_cancel_releases_pending_and_propagates(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    released: list[tuple[str, str]] = []
    closed: list[str] = []

    async def release(run_id: str, worker_id: str) -> bool:
        released.append((run_id, worker_id))
        return True

    async def fake_close(context):
        closed.append(context.worker_id)

    monkeypatch.setattr(run_worker, "append_run_event", AsyncMock())
    monkeypatch.setattr(run_worker, "mark_run_terminal", AsyncMock(side_effect=AssertionError("not user cancel")))
    monkeypatch.setattr(run_worker, "release_run_lease_for_retry", release)
    monkeypatch.setattr(run_worker.RunContext, "close", fake_close)
    monkeypatch.setattr(
        run_worker,
        "_consume_stream_with_cancel",
        lambda stream, context: _RaisingAsyncIter(asyncio.CancelledError("worker shutdown")),
    )

    with pytest.raises(asyncio.CancelledError, match="worker shutdown"):
        await run_worker.process_agent_run({"worker_id": "worker-shutdown", "job_try": 1}, "run-1")

    assert len(released) == 1
    assert released[0][0] == "run-1"
    assert released[0][1].startswith("worker-shutdown:")
    assert closed == [released[0][1]]


@pytest.mark.asyncio
async def test_release_failure_does_not_mask_infrastructure_cancel(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    async def fail_release(_run_id: str, _worker_id: str) -> bool:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(run_worker, "append_run_event", AsyncMock())
    monkeypatch.setattr(run_worker, "release_run_lease_for_retry", fail_release)
    monkeypatch.setattr(
        run_worker,
        "_consume_stream_with_cancel",
        lambda stream, context: _RaisingAsyncIter(asyncio.CancelledError("worker shutdown")),
    )

    with pytest.raises(asyncio.CancelledError, match="worker shutdown"):
        await run_worker.process_agent_run({"worker_id": "worker-shutdown", "job_try": 1}, "run-1")


@pytest.mark.asyncio
async def test_durable_cancel_watcher_stops_execution_when_postgres_fact_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
):
    run_context = run_worker.RunContext(run_id="run-1", worker_id="worker-1:attempt-1")
    monkeypatch.setattr(run_worker, "_is_cancel_requested", AsyncMock(side_effect=RuntimeError("db unavailable")))

    await run_context._watch_durable_cancel()

    assert run_context.cancel_event.is_set()


@pytest.mark.asyncio
async def test_process_agent_run_retryable_error_retries_then_completes(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)

    terminal_statuses: list[str] = []
    events: list[dict] = []
    lifecycle: list[str] = []
    attempts = {"count": 0}

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append({"event_type": event_type, "payload": payload})

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
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

    async def fake_release_lease(*_args, **_kwargs):
        lifecycle.append("lease-and-descendants")
        return True

    async def fake_release_runtime(*_args, **_kwargs):
        lifecycle.append("runtime")
        return True

    monkeypatch.setattr(run_worker, "release_run_lease_for_retry", fake_release_lease)
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", fake_release_runtime)

    with pytest.raises(run_worker.RetryableRunError) as retry:
        await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert isinstance(retry.value, RetryJob)
    assert terminal_statuses == []
    assert lifecycle == ["lease-and-descendants", "runtime"]
    assert any(
        item["event_type"] == "error" and item["payload"]["chunk"].get("error_type") == "retryable_worker_error"
        for item in events
    )

    await run_worker.process_agent_run({"job_try": 2}, "run-1")
    assert terminal_statuses == ["completed"]


@pytest.mark.asyncio
async def test_finish_run_terminal_loser_does_not_append_end_event(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple[str, dict]] = []

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, status, kwargs
        return run_worker.TerminalTransition(status="cancelled", changed=False)

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, kwargs
        events.append((event_type, payload))

    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "append_run_event", fake_append_event)
    monkeypatch.setattr(run_worker, "_get_run", AsyncMock(return_value=None))

    transition = await run_worker._finish_run(
        "run-1",
        "completed",
        thread_id="thread-1",
        chunk={"status": "finished"},
        current_user=SimpleNamespace(uid="user-1"),
        worker_id="worker-1:attempt-1",
    )

    assert transition == run_worker.TerminalTransition(status="cancelled", changed=False)
    assert events == []


@pytest.mark.asyncio
async def test_process_subagent_run_restores_runtime_context(monkeypatch: pytest.MonkeyPatch):
    run_obj = _build_run()
    run_obj.run_type = "subagent"
    run_obj.agent_slug = "worker"
    run_obj.conversation_thread_id = "child-thread"
    run_obj.runtime_scope_id = "parent-thread"
    run_obj.created_by_run_id = "parent-run"
    run_obj.input_payload = {
        "model_spec": "provider:model",
        "runtime": {
            "parent_thread_id": "parent-thread",
        },
    }
    _patch_common(monkeypatch, run_obj)

    captured: dict[str, object] = {}
    terminal_statuses: list[str] = []

    async def fake_append_event(run_id: str, event_type: str, payload: dict, **kwargs):
        del run_id, event_type, payload, kwargs

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        del run_id, kwargs
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
    assert meta["runtime_scope_id"] == "parent-thread"
    assert meta["workdir_relative_path"] == "projects/11111111-1111-4111-8111-111111111111"
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

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None, **kwargs):
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

    async def fake_mark_terminal(run_id: str, status: str, error_type=None, error_message=None, **kwargs):
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


def test_run_owner_token_has_stable_worker_prefix_and_unique_attempt_suffix():
    ctx = {"worker_id": "worker-stable"}

    first = run_worker._run_owner_token(ctx)
    second = run_worker._run_owner_token(ctx)

    assert first.startswith("worker-stable:")
    assert second.startswith("worker-stable:")
    assert first != second


@pytest.mark.asyncio
async def test_run_context_stops_when_heartbeat_cannot_renew(monkeypatch: pytest.MonkeyPatch):
    renew = AsyncMock(return_value=False)
    monkeypatch.setattr(run_worker, "RUN_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(run_worker, "renew_run_lease", renew)
    run_ctx = run_worker.RunContext(run_id="run-1", worker_id="worker-1:attempt-1")

    await run_ctx._heartbeat_lease()

    renew.assert_awaited_once_with("run-1", "worker-1:attempt-1")
    assert run_ctx.lease_lost is True
    assert run_ctx.cancel_event.is_set()


@pytest.mark.asyncio
async def test_worker_startup_ensures_builtin_mcp_servers(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_initialize():
        calls.append("initialize")

    async def fake_require_current_schema(*, include_knowledge: bool):
        assert include_knowledge is True
        calls.append("require_current_schema")

    async def fake_ensure_builtin_mcp_servers_in_db():
        calls.append("ensure_builtin_mcp_servers_in_db")

    @asynccontextmanager
    async def fake_session_ctx():
        yield SimpleNamespace(commit=AsyncMock())

    async def fake_init_builtin_skills(session):
        del session
        calls.append("init_builtin_skills")

    async def fake_ensure_options_in_db(session):
        del session
        calls.append("ensure_options_in_db")

    async def fake_invalidate_option_cache(key):
        del key
        calls.append("invalidate_option_cache")

    async def fake_recover_pending_dispatches():
        calls.append("recover_pending_dispatches")

    async def fake_publish_reconciliation_health():
        calls.append("publish_reconciliation_health")

    async def fake_publish_task_reconciliation_health():
        calls.append("publish_task_reconciliation_health")

    async def fake_reconcile_expired_run_leases():
        calls.append("reconcile_expired_run_leases")
        return []

    async def fake_reconcile_pending_runtime_cleanups():
        calls.append("reconcile_pending_runtime_cleanups")
        return []

    async def fake_reconciliation_loop():
        calls.append("reconciliation_loop")

    async def fake_reconcile_and_publish_tasks():
        calls.append("reconcile_and_publish_tasks")
        return []

    async def fake_task_reconciliation_loop():
        calls.append("task_reconciliation_loop")

    monkeypatch.setattr(run_worker.pg_manager, "initialize", fake_initialize)
    monkeypatch.setattr(run_worker.pg_manager, "require_current_schema", fake_require_current_schema)
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(run_worker, "ensure_builtin_mcp_servers_in_db", fake_ensure_builtin_mcp_servers_in_db)
    monkeypatch.setattr(run_worker, "init_builtin_skills", fake_init_builtin_skills)
    monkeypatch.setattr(config_options, "ensure_options_in_db", fake_ensure_options_in_db)
    monkeypatch.setattr(config_options, "invalidate_option_cache", fake_invalidate_option_cache)
    monkeypatch.setattr(run_worker, "recover_pending_dispatches", fake_recover_pending_dispatches)
    monkeypatch.setattr(run_worker, "reconcile_expired_run_leases", fake_reconcile_expired_run_leases)
    monkeypatch.setattr(
        run_worker,
        "reconcile_pending_runtime_cleanups",
        fake_reconcile_pending_runtime_cleanups,
    )
    monkeypatch.setattr(run_worker, "_publish_reconciliation_health", fake_publish_reconciliation_health)
    monkeypatch.setattr(run_worker, "_publish_task_reconciliation_health", fake_publish_task_reconciliation_health)
    monkeypatch.setattr(run_worker, "_reconcile_agent_run_leases_forever", fake_reconciliation_loop)
    monkeypatch.setattr(run_worker, "reconcile_and_publish_tasks", fake_reconcile_and_publish_tasks)
    monkeypatch.setattr(run_worker, "_reconcile_durable_tasks_forever", fake_task_reconciliation_loop)
    options_module = importlib.import_module("yuxi.config.options")
    monkeypatch.setattr(options_module, "ensure_options_in_db", fake_ensure_options_in_db)

    ctx = {}
    await run_worker._worker_startup(ctx)
    await ctx[run_worker._RECONCILIATION_TASK_KEY]
    await ctx[run_worker._TASK_RECONCILIATION_TASK_KEY]

    assert calls == [
        "initialize",
        "require_current_schema",
        "ensure_options_in_db",
        "invalidate_option_cache",
        "ensure_builtin_mcp_servers_in_db",
        "init_builtin_skills",
        "reconcile_expired_run_leases",
        "reconcile_pending_runtime_cleanups",
        "recover_pending_dispatches",
        "reconcile_and_publish_tasks",
        "publish_task_reconciliation_health",
        "publish_reconciliation_health",
        "reconciliation_loop",
        "task_reconciliation_loop",
    ]
    assert ctx["worker_id"] == run_worker.WORKER_ID


async def test_durable_task_publication_failure_does_not_refresh_health(monkeypatch):
    sleep_calls = 0
    health_calls = 0

    async def controlled_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    async def fail_reconciliation():
        raise ConnectionError("arq publication failed")

    async def publish_health():
        nonlocal health_calls
        health_calls += 1

    monkeypatch.setattr(run_worker.asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(run_worker, "reconcile_and_publish_tasks", fail_reconciliation)
    monkeypatch.setattr(run_worker, "_publish_task_reconciliation_health", publish_health)

    with pytest.raises(asyncio.CancelledError):
        await run_worker._reconcile_durable_tasks_forever()

    assert health_calls == 0


def test_worker_settings_publish_short_ttl_versioned_health_contract():
    assert run_worker.WorkerSettings.max_jobs == 10
    assert run_worker.WorkerSettings.health_check_key == "yuxi:worker:health:agent-run-v1"
    assert 0 < run_worker.WorkerSettings.health_check_interval <= 10


def test_worker_settings_reject_invalid_redis_dsn_instead_of_using_arq_default():
    env = os.environ.copy()
    env["REDIS_URL"] = "http://configured-redis.invalid:6379/0"

    completed = subprocess.run(
        [sys.executable, "-c", "import yuxi.services.run_worker"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid DSN scheme" in completed.stderr


@pytest.mark.asyncio
async def test_reconciliation_failure_does_not_refresh_success_lease(monkeypatch: pytest.MonkeyPatch):
    calls = {"reconcile": 0, "publish": 0}

    async def no_wait(_seconds: float):
        return None

    async def fail_then_cancel():
        calls["reconcile"] += 1
        if calls["reconcile"] == 1:
            raise RuntimeError("database schema mismatch")
        raise asyncio.CancelledError

    async def publish():
        calls["publish"] += 1

    monkeypatch.setattr(run_worker.asyncio, "sleep", no_wait)
    monkeypatch.setattr(run_worker, "reconcile_expired_run_leases", fail_then_cancel)
    monkeypatch.setattr(run_worker, "_publish_reconciliation_health", publish)

    with pytest.raises(asyncio.CancelledError):
        await run_worker._reconcile_agent_run_leases_forever()

    assert calls == {"reconcile": 2, "publish": 0}


@pytest.mark.asyncio
async def test_worker_startup_fails_when_system_options_cannot_initialize(monkeypatch: pytest.MonkeyPatch):

    monkeypatch.setattr(run_worker.pg_manager, "initialize", lambda: None)
    monkeypatch.setattr(run_worker.pg_manager, "require_current_schema", AsyncMock())

    @asynccontextmanager
    async def fake_session_ctx():
        yield object()

    async def fail_initialize(_session):
        raise RuntimeError("config load failed")

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", fake_session_ctx)
    monkeypatch.setattr(config_options, "ensure_options_in_db", fail_initialize)

    with pytest.raises(RuntimeError, match="config load failed"):
        await run_worker._worker_startup({})


@pytest.mark.asyncio
async def test_worker_shutdown_closes_queue_clients_before_postgres(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    never_finishes = asyncio.Event()
    reconciliation_task = asyncio.create_task(never_finishes.wait())

    async def fake_close_queue_clients():
        calls.append("redis")

    async def fake_close_postgres():
        calls.append("postgres")

    monkeypatch.setattr("yuxi.services.run_queue_service.close_queue_clients", fake_close_queue_clients)
    monkeypatch.setattr(run_worker.pg_manager, "close", fake_close_postgres)

    await run_worker._worker_shutdown({run_worker._RECONCILIATION_TASK_KEY: reconciliation_task})

    assert calls == ["redis", "postgres"]
    assert reconciliation_task.cancelled()


@pytest.mark.asyncio
async def test_manifest_persist_failure_fails_run_before_execution(monkeypatch: pytest.MonkeyPatch):
    """manifest 固化失败时 Run 显式失败，且不得进入执行流。"""
    run_obj = _build_run()
    _patch_common(monkeypatch, run_obj)
    terminal_calls: list[dict] = []
    stream_called = asyncio.Event()

    async def fake_persist_manifest(**kwargs):
        del kwargs
        raise RuntimeError("manifest db unavailable")

    async def fake_mark_terminal(run_id: str, status: str, **kwargs):
        terminal_calls.append({"run_id": run_id, "status": status, **kwargs})
        return run_worker.TerminalTransition(status=status, changed=True)

    def fake_stream_agent_chat(**kwargs):
        del kwargs
        stream_called.set()
        return _BytesAsyncIter([])

    monkeypatch.setattr(run_worker, "persist_run_manifest", fake_persist_manifest)
    monkeypatch.setattr(run_worker, "mark_run_terminal", fake_mark_terminal)
    monkeypatch.setattr(run_worker, "stream_agent_chat", fake_stream_agent_chat)

    await run_worker.process_agent_run({"job_try": 1}, "run-1")

    assert not stream_called.is_set()
    assert len(terminal_calls) == 1
    assert terminal_calls[0]["status"] == "failed"
    assert terminal_calls[0]["error_type"] == "manifest_persist_failed"
    assert "执行未开始" in terminal_calls[0]["error_message"]


def test_retry_requires_new_manifest_fingerprint_to_match_write_once_fact():
    persisted = SimpleNamespace(manifest_fingerprint="a" * 64)

    run_worker._require_persisted_manifest_match(persisted, recorded=False, fingerprint="a" * 64)
    with pytest.raises(RuntimeError, match="运行资产已在重试前变化"):
        run_worker._require_persisted_manifest_match(persisted, recorded=False, fingerprint="b" * 64)
