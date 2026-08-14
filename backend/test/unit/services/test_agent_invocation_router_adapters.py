from __future__ import annotations

from types import SimpleNamespace
import importlib

import pytest
from fastapi import HTTPException

call_router = importlib.import_module("server.routers.agent_invocation_call_router")
eval_router = importlib.import_module("server.routers.agent_invocation_eval_router")


@pytest.mark.asyncio
async def test_agent_call_adapter_submits_shared_run_command(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_submit_run_command(*, command, current_user, db):
        calls.update(command=command, current_user=current_user, db=db)
        return {
            "request_id": command.request_id,
            "status": "dispatched",
            "queue_policy": command.queue_policy,
            "queue_position": 0,
            "message_id": 1,
            "run_id": "run-1",
            "thread_id": command.thread_id,
        }

    monkeypatch.setattr(call_router, "submit_run_command", fake_submit_run_command)
    monkeypatch.setattr(
        call_router,
        "await_agent_run_result",
        lambda **_: pytest.fail("async Agent Call must not wait"),
    )
    user = SimpleNamespace(uid="user-1")
    result = await call_router.create_agent_call_run(
        call_router.AgentCallRunCreate(
            agent_slug=" translator ",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            agent_call_meta={"trace_id": "trace-1"},
            request_id=" req-1 ",
            async_mode=True,
        ),
        current_user=user,
        db=object(),
    )

    command = calls["command"]
    assert command.origin.source == "agent_call"
    assert command.origin.channel == "api"
    assert command.origin.external_id == "req-1"
    assert command.origin.metadata == {"agent_invocation_meta": {"trace_id": "trace-1"}}
    assert command.input_message.content == "hello"
    assert result["run_id"] == "run-1"
    assert result["choices"][0]["finish_reason"] is None


@pytest.mark.asyncio
async def test_agent_call_adapter_waits_and_wraps_result(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_submit_run_command(*, command, **_kwargs):
        calls["command"] = command
        return {"run_id": "run-1", "thread_id": "thread-1", "status": "dispatched", "request_id": "req-1"}

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        calls["await"] = (run_id, current_uid)
        return {
            "status": "completed",
            "output": "你好",
            "agent_slug": "translator",
            "thread_id": "thread-1",
            "agent_run_id": run_id,
            "request_id": "req-1",
            "token_usage": {
                "schema_version": 2,
                "complete": True,
                "models": {"provider:model": {}},
                "total": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        }

    monkeypatch.setattr(call_router, "submit_run_command", fake_submit_run_command)
    monkeypatch.setattr(call_router, "await_agent_run_result", fake_await_agent_run_result)
    result = await call_router.create_agent_call_run(
        call_router.AgentCallRunCreate(
            agent_slug="translator",
            messages=[{"role": "user", "content": "Hello"}],
            request_id="req-1",
        ),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["output"] == "你好"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert calls["await"] == ("run-1", "user-1")


@pytest.mark.parametrize(
    "token_usage",
    [
        {"available": False},
        {
            "complete": False,
            "model_call_count": 2,
            "usage_reported_call_count": 1,
            "total": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
    ],
)
def test_agent_call_response_leaves_unavailable_or_partial_usage_as_none(token_usage):
    result = call_router._build_agent_call_response(
        {
            "status": "completed",
            "agent_run_id": "run-1",
            "token_usage": token_usage,
        }
    )

    assert result["usage"] is None


@pytest.mark.asyncio
async def test_agent_call_adapter_rejects_invalid_sync_policy():
    with pytest.raises(HTTPException) as exc:
        await call_router.create_agent_call_run(
            call_router.AgentCallRunCreate(
                agent_slug="translator",
                messages=[{"role": "user", "content": "Hello"}],
                queue_policy="enqueue",
            ),
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_eval_adapter_submits_evaluation_origin_and_waits(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_submit_run_command(*, command, **_kwargs):
        calls["command"] = command
        return {"run_id": "run-1", "thread_id": "thread-1", "status": "dispatched", "request_id": "eval-1"}

    async def fake_await_agent_run_result(**kwargs):
        calls["await"] = kwargs
        return {"status": "completed", "agent_run_id": "run-1", "request_id": "eval-1", "output": "ok"}

    monkeypatch.setattr(eval_router, "submit_run_command", fake_submit_run_command)
    monkeypatch.setattr(eval_router, "await_agent_run_result", fake_await_agent_run_result)
    result = await eval_router.create_agent_eval_run(
        eval_router.AgentEvalRunCreate(
            query="question",
            agent_slug="default-chatbot",
            evaluation={"dataset_name": "dataset", "ignored": "nope"},
            meta={"request_id": "eval-1"},
        ),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    command = calls["command"]
    assert command.origin.source == "agent_evaluation"
    assert command.origin.channel == "api"
    assert command.origin.external_id == "eval-1"
    assert command.origin.metadata == {"agent_invocation_meta": {"evaluation": {"dataset_name": "dataset"}}}
    assert result["output"] == "ok"


def test_trajectory_summary_counts_tool_error_and_interrupt():
    summary = eval_router._build_trajectory_summary(
        [
            {
                "seq": "1-0",
                "event_type": "messages",
                "payload": {"payload": {"items": [{"stream_event": {"type": "tool_call", "name": "search"}}]}},
            },
            {
                "seq": "2-0",
                "event_type": "error",
                "payload": {
                    "payload": {
                        "chunk": {
                            "event": {"data": {"event": "tool-finished", "tool_name": "search", "error": "timeout"}}
                        }
                    }
                },
            },
            {"seq": "3-0", "event_type": "interrupt", "payload": {"payload": {}}},
        ]
    )
    assert summary["tool_call_count"] == 1
    assert summary["tool_error_count"] == 1
    assert summary["interrupt_count"] == 1
    assert summary["tools"] == [{"name": "search", "call_count": 1, "error_count": 1}]
