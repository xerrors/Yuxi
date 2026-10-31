from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime import graph as runtime_graph
from yuxi.modules.agents.runtime.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode

PROJECT_ROOT = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111"


def _requires_approval(middleware, tool_name: str, file_path: object = None) -> bool:
    config = middleware.interrupt_on[tool_name]
    predicate = config.get("when")
    if predicate is None:
        return True
    request = SimpleNamespace(tool_call={"name": tool_name, "args": {"file_path": file_path}})
    return predicate(request)


def test_default_mode_auto_approves_writes_inside_current_project():
    middleware = create_tool_approval_middleware("default", current_project_path=PROJECT_ROOT)

    assert _requires_approval(middleware, "write_file", f"{PROJECT_ROOT}/outputs/report.md") is False
    assert _requires_approval(middleware, "edit_file", f"{PROJECT_ROOT}/notes.md") is False
    assert all(config["allowed_decisions"] == ["approve", "reject"] for config in middleware.interrupt_on.values())


def test_default_mode_keeps_project_external_writes_and_execute_behind_approval():
    middleware = create_tool_approval_middleware("default", current_project_path=PROJECT_ROOT)

    assert _requires_approval(middleware, "write_file", "/home/user/workspace/projects/other/report.md") is True
    assert _requires_approval(middleware, "edit_file", f"{PROJECT_ROOT}-other/report.md") is True
    assert _requires_approval(middleware, "execute") is True


@pytest.mark.parametrize(
    "file_path",
    [
        None,
        "relative.txt",
        f"{PROJECT_ROOT}/../other/report.md",
        f"{PROJECT_ROOT}\\report.md",
        "https://example.com/report.md",
    ],
)
def test_default_mode_fail_closes_invalid_write_paths(file_path):
    middleware = create_tool_approval_middleware("default", current_project_path=PROJECT_ROOT)

    assert _requires_approval(middleware, "write_file", file_path) is True


def test_default_mode_without_current_project_keeps_writes_behind_approval():
    middleware = create_tool_approval_middleware("default")

    assert _requires_approval(middleware, "write_file", f"{PROJECT_ROOT}/report.md") is True


@pytest.mark.asyncio
async def test_chatbot_graph_assembles_approval_with_current_project(monkeypatch):
    monkeypatch.setattr(runtime_graph, "create_summary_middleware_from_context", lambda _context, **kwargs: object())
    monkeypatch.setattr(runtime_graph, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(runtime_graph, "get_langgraph_checkpointer", lambda _: None)
    context = SimpleNamespace(
        model="test-provider:test-model",
        tool_approval_mode="default",
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
    )

    from yuxi.modules.agents.runtime.state import BaseState

    graph = await runtime_graph.build_agent_graph(context, prompt_builder=lambda _: "", state_schema=BaseState, checkpoint_only=True)
    middlewares = graph["middleware"]
    approval = next(middleware for middleware in middlewares if hasattr(middleware, "interrupt_on"))

    assert _requires_approval(approval, "write_file", f"{PROJECT_ROOT}/report.md") is False
    assert _requires_approval(approval, "write_file", "/home/user/workspace/projects/other/report.md") is True
    assert _requires_approval(approval, "execute") is True


def test_always_trust_mode_does_not_build_approval_middleware():
    assert create_tool_approval_middleware("always_trust") is None


def test_unknown_tool_approval_mode_is_rejected():
    with pytest.raises(ValueError, match="不支持的 tool_approval_mode"):
        normalize_tool_approval_mode("unknown")
