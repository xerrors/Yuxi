from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain.messages import AIMessageChunk, HumanMessage

from yuxi.services import chat_service as svc


class _FakeConvRepo:
    def __init__(self, _db):
        self.saved_messages: list[dict] = []
        self.bound_agent_configs: list[tuple[str, int]] = []
        self.conversations: dict[str, SimpleNamespace] = {}

    async def add_message_by_thread_id(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
    ):
        self.saved_messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "message_type": message_type,
                "extra_metadata": extra_metadata,
                "image_content": image_content,
            }
        )
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self.conversations.get(thread_id)

    async def create_conversation(self, *, user_id: str, agent_id: str, thread_id: str):
        conversation = SimpleNamespace(
            user_id=user_id,
            agent_id=agent_id,
            thread_id=thread_id,
            extra_metadata={},
        )
        self.conversations[thread_id] = conversation
        return conversation

    async def bind_agent_config(self, thread_id: str, agent_config_id: int):
        conversation = self.conversations.setdefault(
            thread_id,
            SimpleNamespace(user_id="user-1", agent_id="test-agent", thread_id=thread_id, extra_metadata={}),
        )
        conversation.extra_metadata["agent_config_id"] = agent_config_id
        self.bound_agent_configs.append((thread_id, agent_config_id))


@pytest.mark.asyncio
async def test_stream_agent_chat_passes_langfuse_callbacks_and_persists_trace_info(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    class FakeAgent:
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

    async def fake_get_agent_config_by_id(db, user, agent_config_id):
        return SimpleNamespace(agent_id="test-agent", config_json={"context": {"temperature": 0.1}})

    async def fake_save_messages_from_langgraph_state(*, agent_instance, thread_id, conv_repo, config_dict, trace_info):
        calls["saved_state"] = {
            "thread_id": thread_id,
            "config_dict": config_dict,
            "trace_info": trace_info,
        }

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
        if False:
            yield None
        return

    class FakeAgentConfigRepo:
        def __init__(self, db): pass
        async def get_by_id(self, config_id):
            return SimpleNamespace(id=config_id)
    monkeypatch.setattr(svc, "AgentConfigRepository", FakeAgentConfigRepo)
    
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda agent_id: FakeAgent())
    monkeypatch.setattr(svc, "get_agent_config_by_id", fake_get_agent_config_by_id)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    
    import contextlib
    @contextlib.asynccontextmanager
    async def fake_get_async_session_context():
        yield object()
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", fake_get_async_session_context)
    
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": kwargs["current_user"].id, "langfuse_session_id": kwargs["thread_id"]},
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
        agent_config_id=123,
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id="user-1", user_id="login-user-1", department_id="dept-1"),
        db=object(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert calls["stream_input_context"] == {
        "temperature": 0.1,
        "user_id": "user-1",
        "work_id": "login-user-1",
        "thread_id": "thread-1",
        "department_id": "dept-1",
        "system_prompt": "工号: login-user-1",
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
async def test_stream_agent_chat_emits_realtime_agent_state_from_values(monkeypatch: pytest.MonkeyPatch):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"todos": [{"content": "done", "status": "completed"}]})

    class FakeAgent:
        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "pending"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "messages", (AIMessageChunk(content="hello"), {"node": "llm"})

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self):
            return FakeGraph()

    async def fake_get_agent_config_by_id(db, user, agent_config_id):
        return SimpleNamespace(agent_id="test-agent", config_json={"context": {}})

    async def fake_save_messages_from_langgraph_state(*, agent_instance, thread_id, conv_repo, config_dict, trace_info):
        return None

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
        if False:
            yield None
        return

    class FakeAgentConfigRepo:
        def __init__(self, db): pass
        async def get_by_id(self, config_id):
            return SimpleNamespace(id=config_id)
    monkeypatch.setattr(svc, "AgentConfigRepository", FakeAgentConfigRepo)

    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda agent_id: FakeAgent())
    monkeypatch.setattr(svc, "get_agent_config_by_id", fake_get_agent_config_by_id)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    
    import contextlib
    @contextlib.asynccontextmanager
    async def fake_get_async_session_context():
        yield object()
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", fake_get_async_session_context)

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
        agent_config_id=123,
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id="user-1", user_id="login-user-1", department_id="dept-1"),
        db=object(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    agent_state_chunks = [chunk for chunk in chunks if chunk.get("status") == "agent_state"]
    assert len(agent_state_chunks) == 3
    assert agent_state_chunks[0]["agent_state"]["todos"][0]["status"] == "pending"
    assert agent_state_chunks[1]["agent_state"]["todos"][0]["status"] == "in_progress"
    assert agent_state_chunks[2]["agent_state"]["todos"][0]["status"] == "completed"
