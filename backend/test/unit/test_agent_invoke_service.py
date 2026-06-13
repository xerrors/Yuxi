from __future__ import annotations

import os
import tempfile

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault(
    "SAVE_DIR", os.path.join(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir()), "yuxi-test-saves")
)

from yuxi.services import agent_invoke_service as service

pytestmark = pytest.mark.unit


class FakeUser:
    def __init__(self, uid: str = "user-1"):
        self.uid = uid
        self.role = "user"


class FakeAgentItem:
    def __init__(self, backend_id: str = "ChatbotAgent", config_json: dict | None = None):
        self.backend_id = backend_id
        self.config_json = config_json or {"context": {"knowledges": ["kb-1"]}}


class FakeBackend:
    context_schema = object

    def __init__(self, invoke_return):
        self._invoke_return = invoke_return
        self.invoke_calls: list[dict] = []

    async def invoke_messages(self, messages, input_context=None, **kwargs):
        self.invoke_calls.append({"messages": messages, "input_context": input_context})
        return self._invoke_return


def _patch_common(monkeypatch, *, agent_item, backend):
    class FakeAgentRepository:
        def __init__(self, db):
            self.db = db

        async def get_visible_by_slug(self, *, slug, user):
            return agent_item

    monkeypatch.setattr(service, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(service.agent_manager, "get_agent", lambda backend_id: backend)

    async def fake_normalize(context, *, db, user, context_schema=None):
        return dict(context or {})

    async def fake_build_input_context(agent_config, *, thread_id, uid, **kwargs):
        return {**(agent_config or {}), "thread_id": thread_id, "uid": uid}

    monkeypatch.setattr(service, "normalize_agent_context_config", fake_normalize)
    monkeypatch.setattr(service, "build_agent_input_context", fake_build_input_context)


@pytest.mark.asyncio
async def test_invoke_agent_once_extracts_answer_and_tool_trace(monkeypatch):
    messages = [
        HumanMessage(content="哪个产品支持离线？"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "search_kb", "args": {"query": "离线"}}]),
        ToolMessage(content="chunk: 产品 A 支持离线", tool_call_id="call-1"),
        AIMessage(content="产品 A 支持离线使用。"),
    ]
    backend = FakeBackend({"messages": messages})
    agent_item = FakeAgentItem()
    _patch_common(monkeypatch, agent_item=agent_item, backend=backend)

    result = await service.invoke_agent_once(
        db=None,
        user=FakeUser("user-1"),
        agent_id="my-agent",
        query="哪个产品支持离线？",
    )

    assert result.answer == "产品 A 支持离线使用。"
    assert len(result.messages) == 4
    assert result.tool_calls == [
        {"id": "call-1", "name": "search_kb", "args": {"query": "离线"}, "output": "chunk: 产品 A 支持离线"}
    ]


@pytest.mark.asyncio
async def test_invoke_agent_once_uses_temp_thread_and_caller_uid(monkeypatch):
    backend = FakeBackend({"messages": [AIMessage(content="ok")]})
    _patch_common(monkeypatch, agent_item=FakeAgentItem(), backend=backend)

    await service.invoke_agent_once(
        db=None,
        user=FakeUser("user-42"),
        agent_id="my-agent",
        query="hi",
        model_spec="openai/gpt-4o",
    )

    [call] = backend.invoke_calls
    assert call["input_context"]["uid"] == "user-42"
    assert call["input_context"]["thread_id"].startswith("eval-")
    assert call["input_context"]["model"] == "openai/gpt-4o"
    assert isinstance(call["messages"][0], HumanMessage)


@pytest.mark.asyncio
async def test_invoke_agent_once_raises_when_agent_missing(monkeypatch):
    _patch_common(monkeypatch, agent_item=None, backend=FakeBackend({"messages": []}))

    with pytest.raises(ValueError, match="智能体不存在"):
        await service.invoke_agent_once(db=None, user=FakeUser(), agent_id="ghost", query="hi")


@pytest.mark.asyncio
async def test_invoke_agent_once_returns_empty_answer_without_ai_message(monkeypatch):
    backend = FakeBackend({"messages": [HumanMessage(content="hi")]})
    _patch_common(monkeypatch, agent_item=FakeAgentItem(), backend=backend)

    result = await service.invoke_agent_once(db=None, user=FakeUser(), agent_id="my-agent", query="hi")

    assert result.answer == ""
    assert result.tool_calls == []
