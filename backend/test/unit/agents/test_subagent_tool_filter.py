from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.buildin.subagent import graph as subagent_graph


class _Request:
    def __init__(self, tools):
        self.tools = tools

    def override(self, **kwargs):
        return _Request(kwargs.get("tools", self.tools))


def test_filter_disabled_tools_keeps_allowed_tools_order():
    tools = [
        SimpleNamespace(name="search"),
        SimpleNamespace(name="present_artifacts"),
        {"name": "ask_user_question"},
        SimpleNamespace(name="install_skill"),
        SimpleNamespace(name="calculator"),
    ]

    filtered = subagent_graph._filter_disabled_tools(tools)

    assert [subagent_graph._tool_name(tool) for tool in filtered] == ["search", "calculator"]


def test_ask_for_main_agent_interrupts_with_structured_question(monkeypatch):
    captured = []
    monkeypatch.setattr(
        subagent_graph,
        "interrupt",
        lambda payload: captured.append(payload) or "使用自然月",
    )

    result = subagent_graph.ask_for_main_agent.func(question=" 应该使用哪个统计周期？ ")

    assert captured == [{"source": "ask_for_main_agent", "question": "应该使用哪个统计周期？"}]
    assert result == {"question": "应该使用哪个统计周期？", "answer": "使用自然月"}


def test_ask_for_main_agent_rejects_empty_question():
    with pytest.raises(ValueError, match="question 不能为空"):
        subagent_graph.ask_for_main_agent.func(question="  ")


def test_subagent_tool_filter_middleware_filters_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = middleware.wrap_model_call(
        _Request(
            [
                SimpleNamespace(name="present_artifacts"),
                SimpleNamespace(name="allowed_tool"),
            ]
        ),
        handler,
    )

    assert result == "ok"
    assert [tool.name for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_subagent_tool_filter_middleware_filters_async_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    async def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = await middleware.awrap_model_call(
        _Request(
            [
                {"name": "ask_user_question"},
                SimpleNamespace(name="allowed_tool"),
            ]
        ),
        handler,
    )

    assert result == "ok"
    assert [subagent_graph._tool_name(tool) for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_subagent_get_info_hides_disabled_tool_options(monkeypatch):
    async def get_info(_self, **_kwargs):
        return {
            "metadata": {},
            "configurable_items": {
                "tools": {
                    "options": [
                        {"key": "present_artifacts", "name": "展示交付物"},
                        {"key": "allowed_tool", "name": "Allowed"},
                        {"key": "ask_user_question", "name": "向用户提问"},
                        {"key": "install_skill", "name": "安装技能"},
                    ]
                }
            },
        }

    monkeypatch.setattr(subagent_graph.BaseAgent, "get_info", get_info)

    info = await subagent_graph.SubAgentBackend().get_info()

    assert [option["key"] for option in info["configurable_items"]["tools"]["options"]] == ["allowed_tool"]


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_parent_questions", [False, True])
async def test_subagent_graph_exposes_parent_question_tool_only_for_sync_runs(
    monkeypatch,
    allow_parent_questions,
):
    context = SimpleNamespace(model="provider:model", allow_parent_questions=allow_parent_questions)
    captured = {}

    async def fake_prepare(value, **_kwargs):
        return value

    async def fake_resolve_tools(_context):
        return [SimpleNamespace(name="search")]

    async def fake_middlewares(_context):
        return []

    async def fake_checkpointer(_self):
        return object()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return "graph"

    monkeypatch.setattr(subagent_graph, "prepare_agent_runtime_context", fake_prepare)
    monkeypatch.setattr(subagent_graph, "resolve_configured_runtime_tools", fake_resolve_tools)
    monkeypatch.setattr(subagent_graph, "resolve_chat_model_spec", lambda model: model)
    monkeypatch.setattr(subagent_graph, "load_chat_model", lambda fully_specified_name: fully_specified_name)
    monkeypatch.setattr(subagent_graph, "build_prompt_with_context", lambda _context: "base prompt")
    monkeypatch.setattr(subagent_graph, "_build_middlewares", fake_middlewares)
    monkeypatch.setattr(subagent_graph.SubAgentBackend, "_get_checkpointer", fake_checkpointer)
    monkeypatch.setattr(subagent_graph, "create_agent", fake_create_agent)

    result = await subagent_graph.SubAgentBackend().get_graph(context=context)

    assert result == "graph"
    tool_names = [subagent_graph._tool_name(item) for item in captured["tools"]]
    assert ("ask_for_main_agent" in tool_names) is allow_parent_questions
    assert ("## 向父智能体请求信息" in captured["system_prompt"]) is allow_parent_questions
