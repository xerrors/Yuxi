from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.messages import AIMessage, HumanMessage

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
async def test_agent_chat_uses_invoke_messages_and_persists_langgraph_state(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    class FakeGraph:
        async def aget_state(self, config):
            calls["state_config"] = config
            return SimpleNamespace(values={"messages": [AIMessage(content="Hi from graph")], "todos": ["todo-1"]})

    class FakeAgent:
        async def invoke_messages(self, messages, input_context=None, **kwargs):
            calls["invoke_messages"] = messages
            calls["invoke_input_context"] = input_context
            calls["invoke_kwargs"] = kwargs
            return {"messages": [messages[0], AIMessage(content="Hi from invoke")]}

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages should not be used by sync chat")

        async def get_graph(self):
            return FakeGraph()

    async def fake_get_agent_config_by_id(db, user, agent_config_id):
        assert user.id == "user-1"
        assert agent_config_id == 123
        return SimpleNamespace(agent_id="test-agent", config_json={"context": {"temperature": 0.1}})

    async def fake_save_messages_from_langgraph_state(*, agent_instance, thread_id, conv_repo, config_dict, trace_info):
        calls["saved_state"] = {
            "agent_instance": agent_instance,
            "thread_id": thread_id,
            "conv_repo": conv_repo,
            "config_dict": config_dict,
            "trace_info": trace_info,
        }

    async def fake_guard_check(_content):
        return False

    def fake_build_langfuse_run_context(**kwargs):
        calls["langfuse_kwargs"] = kwargs
        return SimpleNamespace(
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": kwargs["current_user"].id, "langfuse_session_id": kwargs["thread_id"]},
            tags=["yuxi", "chat"],
            trace_id="trace-seeded",
        )

    def fake_get_trace_info(_run_context):
        return {"langfuse_trace_id": "trace-runtime", "langfuse_session_id": "thread-1"}

    monkeypatch.setattr(svc, "_build_langfuse_run_context", fake_build_langfuse_run_context)
    monkeypatch.setattr(svc, "get_trace_info", fake_get_trace_info)
    monkeypatch.setattr(svc, "flush_langfuse", lambda: calls.setdefault("flushed", True))

    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda agent_id: FakeAgent())
    monkeypatch.setattr(svc, "get_agent_config_by_id", fake_get_agent_config_by_id)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)

    result = await svc.agent_chat(
        query="hello",
        agent_config_id=123,
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id="user-1", department_id="dept-1"),
        db=object(),
    )

    assert result["status"] == "finished"
    assert result["response"] == "Hi from invoke"
    assert result["thread_id"] == "thread-1"
    assert result["request_id"] == "req-1"
    assert result["agent_state"] == {"todos": ["todo-1"], "files": {}, "uploads": [], "artifacts": []}

    invoke_messages = calls["invoke_messages"]
    assert isinstance(invoke_messages, list)
    assert len(invoke_messages) == 1
    assert isinstance(invoke_messages[0], HumanMessage)
    assert invoke_messages[0].content == "hello"
    assert calls["invoke_input_context"] == {"temperature": 0.1, "user_id": "user-1", "thread_id": "thread-1"}
    assert calls["invoke_kwargs"] == {
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1", "langfuse_session_id": "thread-1"},
        "tags": ["yuxi", "chat"],
    }
    assert calls["saved_state"]["thread_id"] == "thread-1"
    assert calls["saved_state"]["config_dict"] == {"configurable": {"thread_id": "thread-1", "user_id": "user-1"}}
    assert calls["saved_state"]["trace_info"] == {
        "langfuse_trace_id": "trace-runtime",
        "langfuse_session_id": "thread-1",
    }
    assert calls["saved_state"]["conv_repo"].bound_agent_configs == [("thread-1", 123)]
    assert calls["flushed"] is True


@pytest.mark.asyncio
async def test_agent_chat_sync_returns_finished_even_when_state_has_interrupt(monkeypatch: pytest.MonkeyPatch):
    class FakeGraph:
        async def aget_state(self, config):
            return SimpleNamespace(
                values={
                    "messages": [AIMessage(content="Need input later")],
                    "__interrupt__": [{"questions": [{"question": "继续吗？"}]}],
                }
            )

    class FakeAgent:
        async def invoke_messages(self, messages, input_context=None, **kwargs):
            return {"messages": [messages[0], AIMessage(content="Need input later")]}

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages should not be used by sync chat")

        async def get_graph(self):
            return FakeGraph()

    async def fake_get_agent_config_by_id(db, user, agent_config_id):
        return SimpleNamespace(agent_id="test-agent", config_json={"context": {}})

    async def fake_save_messages_from_langgraph_state(*, agent_instance, thread_id, conv_repo, config_dict, trace_info):
        return None

    async def fake_guard_check(_content):
        return False

    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None),
    )
    monkeypatch.setattr(svc, "get_trace_info", lambda _run_context: {})
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)

    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda agent_id: FakeAgent())
    monkeypatch.setattr(svc, "get_agent_config_by_id", fake_get_agent_config_by_id)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)

    result = await svc.agent_chat(
        query="hello",
        agent_config_id=456,
        thread_id="thread-2",
        meta={"request_id": "req-2"},
        image_content=None,
        current_user=SimpleNamespace(id="user-1", department_id="dept-1"),
        db=object(),
    )

    assert result["status"] == "finished"
    assert result["response"] == "Need input later"
    assert result["thread_id"] == "thread-2"
    assert result["request_id"] == "req-2"


def test_extract_agent_state_keeps_uploads_and_files():
    state = svc.extract_agent_state(
        {
            "todos": [{"content": "read attachment", "status": "completed"}],
            "files": {"/home/gem/user-data/uploads/attachments/demo.md": {"content": ["line1"]}},
            "uploads": [{"path": "/home/gem/user-data/uploads/attachments/demo.md"}],
            "artifacts": ["/home/gem/user-data/outputs/result.txt"],
        }
    )

    assert state == {
        "todos": [{"content": "read attachment", "status": "completed"}],
        "files": {"/home/gem/user-data/uploads/attachments/demo.md": {"content": ["line1"]}},
        "uploads": [{"path": "/home/gem/user-data/uploads/attachments/demo.md"}],
        "artifacts": ["/home/gem/user-data/outputs/result.txt"],
    }
