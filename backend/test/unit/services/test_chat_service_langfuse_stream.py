from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain.messages import AIMessageChunk, HumanMessage

from yuxi.services import chat_service as svc


async def _fake_normalize_agent_context_config(context, **_kwargs):
    return dict(context or {})


class _FakeConvRepo:
    def __init__(self, _db):
        self.saved_messages: list[dict] = []
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1,
                uid="user-1",
                agent_id="test-agent",
                thread_id=thread_id,
                status="active",
                extra_metadata={},
            ),
        )

    async def add_message_by_thread_id(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
    ):
        self.saved_messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "message_type": message_type,
                "extra_metadata": extra_metadata,
                "image_content": image_content,
                "run_id": run_id,
                "request_id": request_id,
            }
        )
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self._conversation(thread_id)

    async def create_conversation(self, *, uid: str, agent_id: str, thread_id: str, metadata: dict | None = None):
        conversation = SimpleNamespace(
            id=1,
            uid=uid,
            agent_id=agent_id,
            thread_id=thread_id,
            status="active",
            extra_metadata=metadata or {},
        )
        self.conversations[thread_id] = conversation
        return conversation

    async def get_attachments_by_request_id(self, conversation_id: int, request_id: str):
        return []

    async def bind_attachments_to_request(self, conversation_id: int, request_id: str, file_ids: list[str]):
        return []


@pytest.mark.asyncio
async def test_stream_agent_chat_passes_langfuse_callbacks_and_persists_trace_info(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    class FakeAgent:
        context_schema = None

        async def stream_messages(self, messages, input_context=None, **kwargs):
            calls["stream_messages"] = messages
            calls["stream_input_context"] = input_context
            calls["stream_kwargs"] = kwargs
            yield AIMessageChunk(content="hello"), {"node": "llm"}

        async def get_graph(self):
            class FakeGraph:
                async def aget_state(self, config):
                    return SimpleNamespace(values={"messages": [], "files": {}, "artifacts": []})

            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {"temperature": 0.1}

    async def fake_save_messages_from_langgraph_state(
        *, agent_instance, thread_id, conv_repo, config_dict, trace_info, run_id=None, request_id=None
    ):
        calls["saved_state"] = {
            "thread_id": thread_id,
            "config_dict": config_dict,
            "trace_info": trace_info,
            "run_id": run_id,
            "request_id": request_id,
        }

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
        if False:
            yield None
        return

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": kwargs["current_user"].uid, "langfuse_session_id": kwargs["thread_id"]},
            tags=["yuxi", "chat"],
            trace_id="trace-seeded",
        ),
    )
    monkeypatch.setattr(
        svc,
        "get_trace_info",
        lambda _run_context: {
            "langfuse_trace_id": "trace-runtime",
            "langfuse_session_id": "thread-1",
        },
    )
    monkeypatch.setattr(svc, "flush_langfuse", lambda: calls.setdefault("flushed", True))

    chunks = []
    async for chunk in svc.stream_agent_chat(
        query="hello",
        agent_id="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=object(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert calls["stream_input_context"] == {
        "temperature": 0.1,
        "uid": "user-1",
        "thread_id": "thread-1",
        "run_id": None,
        "request_id": "req-1",
    }
    assert calls["stream_kwargs"] == {
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1", "langfuse_session_id": "thread-1"},
        "tags": ["yuxi", "chat"],
    }
    assert calls["saved_state"]["trace_info"] == {
        "langfuse_trace_id": "trace-runtime",
        "langfuse_session_id": "thread-1",
    }
    assert chunks[-1]["status"] == "finished"
    assert calls["flushed"] is True
    assert isinstance(calls["stream_messages"][0], HumanMessage)


@pytest.mark.asyncio
async def test_stream_agent_chat_maps_raw_protocol_events_to_yuxi_stream_events(monkeypatch: pytest.MonkeyPatch):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [], "files": {}, "artifacts": []})

    class FakeAgent:
        context_schema = None

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            del messages, input_context, kwargs
            metadata = {"run_id": "run-1"}
            yield "messages", ({"event": "message-start", "id": "msg-1", "role": "ai"}, metadata)
            yield "messages", ({"event": "content-block-start", "index": 0, "content": {"type": "text"}}, metadata)
            yield (
                "messages",
                (
                    {"event": "content-block-delta", "index": 0, "delta": {"type": "text-delta", "text": "hello"}},
                    metadata,
                ),
            )
            yield (
                "messages",
                (
                    {
                        "event": "content-block-delta",
                        "index": 1,
                        "delta": {
                            "type": "block-delta",
                            "fields": {
                                "type": "tool_call_chunk",
                                "id": "call-1",
                                "name": "task",
                                "args": '{"description":"do',
                                "index": 0,
                            },
                        },
                    },
                    metadata,
                ),
            )
            yield (
                "messages",
                (
                    {
                        "event": "content-block-finish",
                        "index": 1,
                        "content": {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "task",
                            "args": {"description": "do work", "subagent_type": "worker"},
                        },
                    },
                    metadata,
                ),
            )
            yield "messages", ({"event": "message-finish", "usage": {}}, metadata)

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self):
            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {}

    async def fake_save_messages_from_langgraph_state(
        *, agent_instance, thread_id, conv_repo, config_dict, trace_info, run_id=None, request_id=None
    ):
        del agent_instance, thread_id, conv_repo, config_dict, trace_info, run_id, request_id
        return None

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
        if False:
            yield None
        return

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None),
    )
    monkeypatch.setattr(svc, "get_trace_info", lambda _run_context: {})
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)

    chunks = []
    async for chunk in svc.stream_agent_chat(
        query="hello",
        agent_id="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=object(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    loading_chunks = [chunk for chunk in chunks if chunk.get("status") == "loading"]
    assert [chunk["stream_event"]["type"] for chunk in loading_chunks] == ["message_delta", "tool_call"]
    assert loading_chunks[0]["response"] == "hello"
    assert loading_chunks[0]["stream_event"] == {
        "type": "message_delta",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "namespace": [],
        "content": "hello",
    }
    assert loading_chunks[1]["response"] == ""
    assert loading_chunks[1]["stream_event"] == {
        "type": "tool_call",
        "message_id": "msg-1",
        "tool_call_id": "call-1",
        "name": "task",
        "args": {"description": "do work", "subagent_type": "worker"},
        "index": 1,
        "thread_id": "thread-1",
        "namespace": [],
    }
    assert all("msg" not in chunk for chunk in loading_chunks)


@pytest.mark.asyncio
async def test_stream_agent_chat_emits_realtime_agent_state_from_values(monkeypatch: pytest.MonkeyPatch):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"todos": [{"content": "done", "status": "completed"}]})

    class FakeAgent:
        context_schema = None

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "pending"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "messages", (AIMessageChunk(content="hello"), {"node": "llm"})

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self):
            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {}

    async def fake_save_messages_from_langgraph_state(
        *, agent_instance, thread_id, conv_repo, config_dict, trace_info, run_id=None, request_id=None
    ):
        del agent_instance, thread_id, conv_repo, config_dict, trace_info, run_id, request_id
        return None

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
        if False:
            yield None
        return

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None),
    )
    monkeypatch.setattr(svc, "get_trace_info", lambda _run_context: {})
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)

    chunks = []
    async for chunk in svc.stream_agent_chat(
        query="hello",
        agent_id="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=object(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    agent_state_chunks = [chunk for chunk in chunks if chunk.get("status") == "agent_state"]
    assert len(agent_state_chunks) == 3
    assert agent_state_chunks[0]["agent_state"]["todos"][0]["status"] == "pending"
    assert agent_state_chunks[1]["agent_state"]["todos"][0]["status"] == "in_progress"
    assert agent_state_chunks[2]["agent_state"]["todos"][0]["status"] == "completed"
