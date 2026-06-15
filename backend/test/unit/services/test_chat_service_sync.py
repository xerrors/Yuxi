from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.messages import AIMessage, HumanMessage

from yuxi.agents import context as agent_context
from yuxi.services import chat_service as svc


def _empty_agents_prompt(_thread_id: str, _uid: str) -> str:
    return ""


async def _fake_normalize_agent_context_config(context, **_kwargs):
    return dict(context or {})


class _FakeConvRepo:
    def __init__(self, _db):
        self.saved_messages: list[dict] = []
        self.tool_calls: list[dict] = []
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
        return self._conversation(thread_id)

    async def get_messages_by_thread_id(self, _thread_id: str):
        return []

    async def add_tool_call(
        self,
        *,
        message_id: int,
        tool_name: str,
        tool_input: dict | None = None,
        status: str = "pending",
        langgraph_tool_call_id: str | None = None,
    ):
        self.tool_calls.append(
            {
                "message_id": message_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "status": status,
                "langgraph_tool_call_id": langgraph_tool_call_id,
            }
        )
        return SimpleNamespace(id=len(self.tool_calls))

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
async def test_save_messages_from_langgraph_state_handles_dict_tool_call_blocks() -> None:
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        {
                            "id": "ai-tool-call",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_call",
                                    "id": "call-task-1",
                                    "name": "task",
                                    "args": {"description": "write file", "subagent_type": "worker"},
                                }
                            ],
                        }
                    ]
                }
            )

    class FakeAgent:
        async def get_graph(self):
            return FakeGraph()

    conv_repo = _FakeConvRepo(None)

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        trace_info=None,
    )

    assert conv_repo.saved_messages[0]["content"] == ""
    assert conv_repo.saved_messages[0]["extra_metadata"]["content"][0]["id"] == "call-task-1"
    assert conv_repo.tool_calls == [
        {
            "message_id": 1,
            "tool_name": "task",
            "tool_input": {"description": "write file", "subagent_type": "worker"},
            "status": "pending",
            "langgraph_tool_call_id": "call-task-1",
        }
    ]


@pytest.mark.asyncio
async def test_agent_chat_uses_invoke_messages_and_persists_langgraph_state(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    class FakeGraph:
        async def aget_state(self, config):
            calls["state_config"] = config
            return SimpleNamespace(values={"messages": [AIMessage(content="Hi from graph")], "todos": ["todo-1"]})

    class FakeAgent:
        context_schema = None

        async def invoke_messages(self, messages, input_context=None, **kwargs):
            calls["invoke_messages"] = messages
            calls["invoke_input_context"] = input_context
            calls["invoke_kwargs"] = kwargs
            return {"messages": [messages[0], AIMessage(content="Hi from invoke")]}

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages should not be used by sync chat")

        async def get_graph(self):
            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {"temperature": 0.1}

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
            metadata={"langfuse_user_id": kwargs["current_user"].uid, "langfuse_session_id": kwargs["thread_id"]},
            tags=["yuxi", "chat"],
            trace_id="trace-seeded",
        )

    def fake_get_trace_info(_run_context):
        return {"langfuse_trace_id": "trace-runtime", "langfuse_session_id": "thread-1"}

    monkeypatch.setattr(svc, "_build_langfuse_run_context", fake_build_langfuse_run_context)
    monkeypatch.setattr(svc, "get_trace_info", fake_get_trace_info)
    monkeypatch.setattr(svc, "flush_langfuse", lambda: calls.setdefault("flushed", True))
    monkeypatch.setattr(agent_context, "_load_workspace_agents_prompt", _empty_agents_prompt)

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)

    result = await svc.agent_chat(
        query="hello",
        agent_id="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        image_content=None,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=object(),
    )

    assert result["status"] == "finished"
    assert result["response"] == "Hi from invoke"
    assert result["thread_id"] == "thread-1"
    assert result["request_id"] == "req-1"
    assert result["agent_state"] == {
        "todos": ["todo-1"],
        "files": {},
        "artifacts": [],
        "subagent_runs": [],
        "token_usage": None,
    }

    invoke_messages = calls["invoke_messages"]
    assert isinstance(invoke_messages, list)
    assert len(invoke_messages) == 1
    assert isinstance(invoke_messages[0], HumanMessage)
    assert invoke_messages[0].content == "hello"
    assert calls["invoke_input_context"] == {
        "temperature": 0.1,
        "uid": "user-1",
        "thread_id": "thread-1",
        "run_id": None,
        "request_id": "req-1",
    }
    assert calls["invoke_kwargs"] == {
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1", "langfuse_session_id": "thread-1"},
        "tags": ["yuxi", "chat"],
    }
    assert calls["saved_state"]["thread_id"] == "thread-1"
    assert calls["saved_state"]["config_dict"] == {"configurable": {"thread_id": "thread-1", "uid": "user-1"}}
    assert calls["saved_state"]["trace_info"] == {
        "langfuse_trace_id": "trace-runtime",
        "langfuse_session_id": "thread-1",
    }
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
        context_schema = None

        async def invoke_messages(self, messages, input_context=None, **kwargs):
            return {"messages": [messages[0], AIMessage(content="Need input later")]}

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages should not be used by sync chat")

        async def get_graph(self):
            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {}

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
    monkeypatch.setattr(agent_context, "_load_workspace_agents_prompt", _empty_agents_prompt)

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)

    result = await svc.agent_chat(
        query="hello",
        agent_id="test-agent",
        thread_id="thread-2",
        meta={"request_id": "req-2"},
        image_content=None,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=object(),
    )

    assert result["status"] == "finished"
    assert result["response"] == "Need input later"
    assert result["thread_id"] == "thread-2"
    assert result["request_id"] == "req-2"


@pytest.mark.asyncio
async def test_build_agent_input_context_merges_workspace_agents_prompt(monkeypatch: pytest.MonkeyPatch):
    def fake_agents_prompt(_thread_id: str, _uid: str) -> str:
        return "回答前先读取 AGENTS.md"

    monkeypatch.setattr(agent_context, "_load_workspace_agents_prompt", fake_agents_prompt)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词", "temperature": 0.1},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == "原始系统提示词\n\n用户工作区 agents/AGENTS.md 内容：\n回答前先读取 AGENTS.md"
    assert context["temperature"] == 0.1
    assert context["thread_id"] == "thread-1"
    assert context["uid"] == "user-1"


@pytest.mark.asyncio
async def test_get_agent_state_view_allows_recorded_child_thread(monkeypatch: pytest.MonkeyPatch):
    child_thread_id = "opaque-child-thread"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            if thread_id == "parent-thread":
                return SimpleNamespace(id=11, uid="user-1", agent_id="main-agent", status="active")
            return None

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            if slug == "worker":
                return SimpleNamespace(backend_id="SubAgentBackend")
            return None

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="sub-run-1",
                thread_id=child_thread_id,
                checkpoint_thread_id=child_thread_id,
                agent_id="worker",
                uid="user-1",
                status="completed",
                parent_agent_run_id="parent-run-1",
                conversation_id=11,
                input_payload={
                    "tool_call_id": "tool-1",
                    "subagent_type": "worker",
                    "subagent_name": "Worker",
                    "child_thread_id": child_thread_id,
                    "description": "do work",
                },
                error_message=None,
                to_dict=lambda: {"created_at": "2026-05-31T01:00:00Z", "finished_at": "2026-05-31T01:00:03Z"},
            )

        async def get_run_for_user(self, run_id: str, uid: str):
            assert run_id == "parent-run-1"
            assert uid == "user-1"
            return SimpleNamespace(id="parent-run-1", thread_id="parent-thread", conversation_id=11)

    class Graph:
        async def aget_state(self, config):
            assert config["configurable"]["thread_id"] == child_thread_id
            return SimpleNamespace(
                values={
                    "messages": [HumanMessage(content="do work"), AIMessage(content="done")],
                    "artifacts": ["out.txt"],
                }
            )

    class Agent:
        async def get_graph(self):
            return Graph()

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: Agent())

    result = await svc.get_agent_state_view(
        thread_id=child_thread_id,
        current_uid="user-1",
        db=object(),
        include_messages=True,
    )

    assert result["parent_thread_id"] == "parent-thread"
    assert result["subagent_run"]["id"] == "tool-1"
    assert result["subagent_run"]["run_id"] == "sub-run-1"
    assert result["subagent_run"]["child_thread_id"] == child_thread_id
    assert result["agent_state"]["artifacts"] == ["out.txt"]
    assert [message["type"] for message in result["messages"]] == ["human", "ai"]


@pytest.mark.asyncio
async def test_build_agent_input_context_keeps_prompt_when_workspace_agents_prompt_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_context, "_load_workspace_agents_prompt", _empty_agents_prompt)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词"},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == "原始系统提示词"
