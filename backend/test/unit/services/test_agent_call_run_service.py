from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from yuxi.services import agent_invocation_service as svc
from yuxi.services.input_message_service import build_chat_input_message


class _NoExistingRunRepo:
    def __init__(self, db):
        self.db = db

    async def get_run_by_request_id(self, request_id: str):
        del request_id
        return None


@pytest.mark.asyncio
async def test_create_agent_invocation_run_creates_invocation_metadata(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    class AgentRepo:
        def __init__(self, db):
            self.db = db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            assert kind == "main"
            assert user is current_user
            return SimpleNamespace(slug=slug)

    class ConvRepo:
        def __init__(self, db):
            self.db = db

        async def get_conversation_by_thread_id(self, thread_id: str):
            calls["thread_lookup"] = thread_id
            return None

        async def add_conversation(self, *, uid: str, agent_id: str, title: str, thread_id: str, metadata: dict):
            calls["conversation"] = {
                "uid": uid,
                "agent_id": agent_id,
                "title": title,
                "thread_id": thread_id,
                "metadata": metadata,
            }
            return SimpleNamespace(id=1, thread_id=thread_id)

    async def fake_create_agent_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": kwargs["thread_id"],
            "status": "pending",
            "request_id": kwargs["meta"]["request_id"],
        }

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _NoExistingRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_agent_run_view", fake_create_agent_run_view)

    result = await svc.create_agent_invocation_run_view(
        agent_slug="translator",
        input_message=build_chat_input_message("Hello World"),
        invocation_metadata={"source": "agent_call", "agent_invocation_meta": {"trace_id": "trace-1"}},
        requested_thread_id="thread-1",
        request_id="req-1",
        model_spec="provider:model",
        current_user=current_user,
        db=object(),
        conversation_title="Agent Call Run",
    )

    assert calls["conversation"]["metadata"] == {
        "source": "agent_call",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert calls["run_kwargs"]["input_message"].content == "Hello World"
    assert calls["run_kwargs"]["model_spec"] == "provider:model"
    assert calls["run_kwargs"]["meta"] == {
        "request_id": "req-1",
        "source": "agent_call",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert result["run_id"] == "run-1"
    assert result["thread_id"] == "thread-1"
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_create_agent_call_run_does_not_commit_conversation_before_run_creation(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    class Db:
        async def commit(self):
            calls["committed"] = True

    class AgentRepo:
        def __init__(self, db):
            self.db = db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user, kind
            return SimpleNamespace(slug=slug)

    class ConvRepo:
        def __init__(self, db):
            self.db = db

        async def get_conversation_by_thread_id(self, thread_id: str):
            calls["thread_lookup"] = thread_id
            return None

        async def add_conversation(self, *, uid: str, agent_id: str, title: str, thread_id: str, metadata: dict):
            calls["conversation"] = {
                "uid": uid,
                "agent_id": agent_id,
                "title": title,
                "thread_id": thread_id,
                "metadata": metadata,
            }
            return SimpleNamespace(id=1, thread_id=thread_id)

        async def create_conversation(self, **_kwargs):
            raise AssertionError("agent-call conversation must not be committed before run creation")

    async def fake_create_agent_run_view(**_kwargs):
        raise HTTPException(status_code=422, detail="未找到可用聊天模型: 'missing:model'")

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _NoExistingRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_agent_run_view", fake_create_agent_run_view)

    with pytest.raises(HTTPException) as exc:
        await svc.create_agent_invocation_run_view(
            agent_slug="translator",
            input_message=build_chat_input_message("Hello"),
            invocation_metadata={"source": "agent_call"},
            requested_thread_id="",
            request_id="req-1",
            model_spec="missing:model",
            current_user=current_user,
            db=Db(),
            conversation_title="Agent Call Run",
        )

    assert exc.value.status_code == 422
    assert "conversation" in calls
    assert calls.get("committed") is None


@pytest.mark.asyncio
async def test_create_agent_call_run_parses_openai_content_and_returns_async_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    async def fake_create_agent_invocation_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": kwargs["requested_thread_id"],
            "status": "pending",
            "request_id": kwargs["request_id"],
        }

    async def fail_await_agent_run_result(**_kwargs):
        raise AssertionError("async_mode must not wait for run result")

    monkeypatch.setattr(svc, "create_agent_invocation_run_view", fake_create_agent_invocation_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fail_await_agent_run_result)

    result = await svc.create_agent_call_run_view(
        agent_slug=" translator ",
        messages=[
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ],
        agent_call_meta={"trace_id": "trace-1"},
        requested_thread_id=" thread-1 ",
        request_id=" req-1 ",
        model_spec="provider:model",
        async_mode=True,
        stream=False,
        current_user=current_user,
        db=object(),
    )

    assert result["run_id"] == "run-1"
    assert result["choices"][0]["finish_reason"] is None
    assert calls["run_kwargs"]["agent_slug"] == "translator"
    assert calls["run_kwargs"]["input_message"].content == "hello"
    assert calls["run_kwargs"]["input_message"].message_type == "text"
    assert calls["run_kwargs"]["requested_thread_id"] == "thread-1"
    assert calls["run_kwargs"]["request_id"] == "req-1"
    assert calls["run_kwargs"]["invocation_metadata"] == {
        "source": "agent_call",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }


@pytest.mark.asyncio
async def test_create_agent_call_run_waits_and_wraps_final_result(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    async def fake_create_agent_invocation_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": kwargs["request_id"],
        }

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        calls["await_kwargs"] = {"run_id": run_id, "current_uid": current_uid}
        return {
            "status": "completed",
            "output": "你好",
            "agent_slug": "translator",
            "thread_id": "thread-1",
            "agent_run_id": run_id,
            "request_id": "req-1",
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        }

    monkeypatch.setattr(svc, "create_agent_invocation_run_view", fake_create_agent_invocation_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fake_await_agent_run_result)

    result = await svc.create_agent_call_run_view(
        agent_slug="translator",
        messages=[{"role": "user", "content": "Hello"}],
        agent_call_meta={},
        requested_thread_id=None,
        request_id="req-1",
        model_spec=None,
        async_mode=False,
        stream=False,
        current_user=current_user,
        db=object(),
    )

    assert result["run_id"] == "run-1"
    assert result["output"] == "你好"
    assert result["choices"][0]["messages"] == [{"role": "assistant", "content": "你好"}]
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert calls["await_kwargs"] == {"run_id": "run-1", "current_uid": "user-1"}


@pytest.mark.asyncio
async def test_create_agent_eval_run_leaves_thread_resolution_to_invocation_helper(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    async def fake_create_agent_invocation_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": "existing-or-new-thread",
            "status": "pending",
            "request_id": kwargs["request_id"],
        }

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        calls["await_kwargs"] = {"run_id": run_id, "current_uid": current_uid}
        return {"status": "completed", "agent_run_id": run_id, "request_id": "eval-req", "output": "ok"}

    monkeypatch.setattr(svc, "create_agent_invocation_run_view", fake_create_agent_invocation_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fake_await_agent_run_result)

    result = await svc.create_agent_eval_run_view(
        query="question",
        agent_slug="default-chatbot",
        evaluation={"dataset_name": "dataset"},
        meta={"request_id": "eval-req"},
        image_content=None,
        model_spec=None,
        current_user=current_user,
        db=object(),
    )

    assert result["status"] == "completed"
    assert calls["run_kwargs"]["requested_thread_id"] == ""
    assert calls["run_kwargs"]["request_id"] == "eval-req"
    assert calls["await_kwargs"] == {"run_id": "run-1", "current_uid": "user-1"}


@pytest.mark.asyncio
async def test_create_agent_eval_run_adds_trajectory_summary_when_requested(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    async def fake_create_agent_invocation_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": kwargs["request_id"],
        }

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        return {
            "status": "completed",
            "agent_run_id": run_id,
            "request_id": "eval-req",
            "output": "ok",
            "langfuse_trace_id": "trace-1",
        }

    async def fake_load_trajectory_summary(run_id: str):
        calls["trajectory_run_id"] = run_id
        return {"tool_call_count": 1, "tools": [{"name": "web_search", "call_count": 1, "error_count": 0}]}

    monkeypatch.setattr(svc, "create_agent_invocation_run_view", fake_create_agent_invocation_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fake_await_agent_run_result)
    monkeypatch.setattr(svc, "_load_trajectory_summary", fake_load_trajectory_summary)

    result = await svc.create_agent_eval_run_view(
        query="question",
        agent_slug="default-chatbot",
        evaluation={},
        meta={"request_id": "eval-req"},
        image_content=None,
        model_spec=None,
        current_user=current_user,
        db=object(),
        include_trajectory_summary=True,
    )

    assert calls["trajectory_run_id"] == "run-1"
    assert result["trajectory_summary"] == {
        "tool_call_count": 1,
        "tools": [{"name": "web_search", "call_count": 1, "error_count": 0}],
        "langfuse_trace_id": "trace-1",
    }


@pytest.mark.asyncio
async def test_create_agent_eval_run_ignores_trajectory_summary_load_errors(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user")

    async def fake_create_agent_invocation_run_view(**kwargs):
        return {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": kwargs["request_id"],
        }

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        del current_uid
        return {"status": "completed", "agent_run_id": run_id, "request_id": "eval-req", "output": "ok"}

    async def fake_load_trajectory_summary(run_id: str):
        calls["trajectory_run_id"] = run_id
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(svc, "create_agent_invocation_run_view", fake_create_agent_invocation_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fake_await_agent_run_result)
    monkeypatch.setattr(svc, "_load_trajectory_summary", fake_load_trajectory_summary)

    result = await svc.create_agent_eval_run_view(
        query="question",
        agent_slug="default-chatbot",
        evaluation={},
        meta={"request_id": "eval-req"},
        image_content=None,
        model_spec=None,
        current_user=current_user,
        db=object(),
        include_trajectory_summary=True,
    )

    assert calls["trajectory_run_id"] == "run-1"
    assert result == {"status": "completed", "agent_run_id": "run-1", "request_id": "eval-req", "output": "ok"}


def test_build_trajectory_summary_counts_tools_interrupts_and_errors():
    summary = svc._build_trajectory_summary(
        [
            {
                "seq": "1-0",
                "event_type": "messages",
                "payload": {
                    "payload": {
                        "items": [
                            {
                                "stream_event": {
                                    "type": "tool_call",
                                    "tool_call_id": "call-search",
                                    "name": "web_search",
                                }
                            }
                        ]
                    }
                },
            },
            {
                "seq": "2-0",
                "event_type": "error",
                "payload": {
                    "payload": {
                        "chunk": {
                            "event": {
                                "data": {
                                    "event": "tool-finished",
                                    "tool_call_id": "call-search",
                                    "tool_name": "web_search",
                                    "error": "timeout",
                                }
                            }
                        }
                    }
                },
            },
            {"seq": "3-0", "event_type": "interrupt", "payload": {"payload": {"reason": "human_approval"}}},
        ]
    )

    assert summary == {
        "schema_version": 1,
        "source": "run_events",
        "event_count": 3,
        "events_truncated": False,
        "event_range": {"first_seq": "1-0", "last_seq": "3-0"},
        "tool_call_count": 1,
        "tool_error_count": 1,
        "interrupt_count": 1,
        "tools": [{"name": "web_search", "call_count": 1, "error_count": 1}],
    }


def test_build_trajectory_summary_counts_human_interrupt_once_with_end_event():
    interrupt_chunk = {
        "status": "human_approval_required",
        "message": "approve?",
    }

    summary = svc._build_trajectory_summary(
        [
            {
                "seq": "1-0",
                "event_type": "interrupt",
                "payload": {"payload": {"reason": "human_approval", "chunk": interrupt_chunk}},
            },
            {
                "seq": "2-0",
                "event_type": "end",
                "payload": {"payload": {"status": "interrupted", "chunk": interrupt_chunk}},
            },
        ]
    )

    assert summary["interrupt_count"] == 1


def test_build_trajectory_summary_matches_no_id_tool_finish_to_start():
    summary = svc._build_trajectory_summary(
        [
            {
                "seq": None,
                "event_type": "messages",
                "payload": {
                    "payload": {
                        "items": [
                            {
                                "stream_event": {
                                    "type": "tool_call",
                                    "name": "read_file",
                                }
                            }
                        ]
                    }
                },
            },
            {
                "seq": None,
                "event_type": "error",
                "payload": {
                    "payload": {
                        "chunk": {
                            "event": {
                                "data": {
                                    "event": "tool-finished",
                                    "tool_name": "read_file",
                                    "error": "file not found",
                                }
                            }
                        }
                    }
                },
            },
        ]
    )

    assert summary["event_range"] == {"first_seq": None, "last_seq": None}
    assert summary["tool_call_count"] == 1
    assert summary["tool_error_count"] == 1
    assert summary["tools"] == [{"name": "read_file", "call_count": 1, "error_count": 1}]


@pytest.mark.asyncio
async def test_load_trajectory_summary_reads_run_events_with_limit(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_list_run_stream_events(run_id: str, *, after_seq: str, limit: int):
        calls["run_id"] = run_id
        calls["after_seq"] = after_seq
        calls["limit"] = limit
        return []

    monkeypatch.setattr(svc, "list_run_stream_events", fake_list_run_stream_events)

    summary = await svc._load_trajectory_summary("run-1")

    assert calls == {
        "run_id": "run-1",
        "after_seq": "0-0",
        "limit": svc.TRAJECTORY_SUMMARY_EVENT_LIMIT,
    }
    assert summary["event_count"] == 0
    assert summary["tools"] == []
