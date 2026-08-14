from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.buildin.chatbot import graph as chatbot_graph
from yuxi.agents.buildin.subagent import graph as subagent_graph


def _context(summary_threshold: int = 123) -> SimpleNamespace:
    return SimpleNamespace(
        model="test-provider:test-model",
        summary_threshold=summary_threshold,
        summary_keep_messages=7,
        summary_prompt="SUMMARY {messages}",
        summary_tool_result_token_limit=300,
        summary_l2_trigger_ratio=0.75,
        tool_token_limit=3,
        model_retry_times=1,
    )


def _patch_common_graph_deps(monkeypatch: pytest.MonkeyPatch, graph_module, captured: dict) -> None:
    monkeypatch.setattr(graph_module, "load_chat_model", lambda fully_specified_name: object())
    monkeypatch.setattr(graph_module, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())

    def create_summary_middleware(**kwargs):
        captured["summary_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(graph_module, "create_summary_middleware", create_summary_middleware)


@pytest.mark.parametrize(
    ("graph_module", "threshold", "build_args", "patch_subagent_task"),
    [
        (chatbot_graph, 123, (), True),
        (subagent_graph, 64, ("default",), False),
    ],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_trim_limit_matches_summary_threshold(
    monkeypatch: pytest.MonkeyPatch, graph_module, threshold: int, build_args, patch_subagent_task: bool
) -> None:
    captured: dict = {}
    _patch_common_graph_deps(monkeypatch, graph_module, captured)

    async def no_subagent_middleware(_context):
        return None

    if patch_subagent_task:
        monkeypatch.setattr(graph_module, "create_subagent_task_middleware", no_subagent_middleware)

    middlewares = await graph_module._build_middlewares(_context(summary_threshold=threshold), *build_args)

    assert captured["summary_kwargs"]["trigger"] == ("tokens", threshold * 1024)
    assert captured["summary_kwargs"]["trim_tokens_to_summarize"] == threshold * 1024
    assert captured["summary_kwargs"]["l1_l2_trigger_ratio"] == 0.75
    middleware_names = [type(middleware).__name__ for middleware in middlewares]
    assert middleware_names.index("ModelRetryMiddleware") < middleware_names.index("ImageInputCompatibilityMiddleware")
