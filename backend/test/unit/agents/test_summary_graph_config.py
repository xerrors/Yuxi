from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.buildin.chatbot import graph as chatbot_graph
from yuxi.agents.buildin.subagent import graph as subagent_graph
from yuxi.agents.middlewares import summary as summary_module


def _context(summary_threshold: int = 123) -> SimpleNamespace:
    return SimpleNamespace(
        model="test-provider:test-model",
        summary_threshold=summary_threshold,
        summary_keep_messages=7,
        summary_prompt="SUMMARY {messages}",
        summary_tool_result_token_limit=300,
        tool_token_limit=3,
        model_retry_times=1,
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
        workdir_path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
    )


def _patch_common_graph_deps(monkeypatch: pytest.MonkeyPatch, graph_module, captured: dict) -> None:
    monkeypatch.setattr(graph_module, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())

    def create_summary_middleware_from_context(context, *, backend):
        captured["summary_context"] = context
        captured["summary_backend"] = backend
        return object()

    monkeypatch.setattr(
        graph_module,
        "create_summary_middleware_from_context",
        create_summary_middleware_from_context,
    )


@pytest.mark.parametrize(
    ("graph_module", "threshold", "build_args", "patch_subagent_task"),
    [
        (chatbot_graph, 123, (object(),), True),
        (subagent_graph, 64, (object(), "default"), False),
    ],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_uses_shared_summary_middleware_factory(
    monkeypatch: pytest.MonkeyPatch, graph_module, threshold: int, build_args, patch_subagent_task: bool
) -> None:
    captured: dict = {}
    _patch_common_graph_deps(monkeypatch, graph_module, captured)

    async def no_subagent_middleware(_context):
        return None

    if patch_subagent_task:
        monkeypatch.setattr(graph_module, "create_subagent_task_middleware", no_subagent_middleware)

    middlewares = await graph_module._build_middlewares(_context(summary_threshold=threshold), *build_args)

    assert captured["summary_context"].summary_threshold == threshold
    assert captured["summary_backend"] is build_args[0]
    middleware_names = [type(middleware).__name__ for middleware in middlewares]
    assert middleware_names.index("NetworkRetryMiddleware") < middleware_names.index(
        "ImageInputCompatibilityMiddleware"
    )


@pytest.mark.unit
def test_shared_summary_factory_uses_one_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def load_model(fully_specified_name, *, session_id):
        """记录摘要模型实际接收的会话 ID。"""
        captured["session_id"] = session_id
        return object()

    monkeypatch.setattr(summary_module, "load_chat_model", load_model)

    def create_summary_middleware(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(summary_module, "create_summary_middleware", create_summary_middleware)
    context = _context(summary_threshold=96)
    context.thread_id = "summary-thread"
    backend = object()

    summary_module.create_summary_middleware_from_context(context, backend=backend)

    assert captured["backend"] is backend
    assert captured["session_id"] == "summary-thread"
    assert captured["trigger"] == ("tokens", 96 * 1024)
    assert captured["trim_tokens_to_summarize"] == 96 * 1024
    assert "l1_l2_trigger_ratio" not in captured


@pytest.mark.parametrize(
    "graph_module,agent_class",
    [
        (chatbot_graph, chatbot_graph.ChatbotAgent),
        (subagent_graph, subagent_graph.SubAgentBackend),
    ],
)
@pytest.mark.asyncio
async def test_graph_passes_conversation_session_to_model(monkeypatch, graph_module, agent_class):
    """主 Agent 与子 Agent 构图都使用实际线程的模型会话。"""
    context = _context()
    context.thread_id = "graph-thread"
    monkeypatch.setattr(graph_module, "prepare_agent_runtime_context", AsyncMock(return_value=context))
    monkeypatch.setattr(graph_module, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(graph_module, "build_prompt_with_context", lambda _context: "test")
    monkeypatch.setattr(graph_module, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_class, "_get_checkpointer", AsyncMock(return_value=None))
    captured = {}

    def load_model(fully_specified_name, *, session_id):
        """用装配参数作为模型占位，核对传给图的对象。"""
        captured.update(spec=fully_specified_name, session_id=session_id)
        return captured

    monkeypatch.setattr(graph_module, "load_chat_model", load_model)
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: kwargs)
    graph = await agent_class().get_graph(context=context)
    assert graph["model"] == {"spec": context.model, "session_id": context.thread_id}
