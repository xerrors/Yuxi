from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

import yuxi.agents.middlewares.skills as skills_middleware
from yuxi.agents.middlewares.skills import SkillsMiddleware, resolve_runtime_skills_for_context


def _system_message_text(message: SystemMessage) -> str:
    return "\n".join(block.get("text", "") for block in message.content_blocks if isinstance(block, dict))


@pytest.mark.asyncio
async def test_resolve_runtime_skills_derives_prompt_and_readable_closure(monkeypatch):
    async def fake_list_skills_from_db(db=None, user=None):
        del db, user
        return [
            SimpleNamespace(
                slug="alpha",
                name="Alpha",
                description="alpha desc",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
            ),
        ]

    monkeypatch.setattr(skills_middleware, "_list_skills_from_db", fake_list_skills_from_db)

    context = SimpleNamespace(skills=["alpha", "missing"])

    scope = await resolve_runtime_skills_for_context(context)

    assert scope["context_skills"] == ["alpha"]
    assert scope["prompt_skills"] == ["alpha", "beta"]
    assert scope["readable_skills"] == ["alpha", "beta"]
    assert set(scope["runtime_skill_metadata"]) == {"alpha", "beta"}
    assert scope["runtime_skill_dependency_map"]["alpha"]["skills"] == ["beta"]


@pytest.mark.asyncio
async def test_skills_prompt_uses_prepared_prompt_skills_at_request_level():
    context = SimpleNamespace(
        system_prompt="context base",
        skills=["configured-only"],
        _prompt_skills=["alpha"],
        _runtime_skill_metadata={
            "alpha": {
                "name": "Alpha",
                "description": "alpha desc",
                "path": "/home/gem/skills/alpha/SKILL.md",
            },
            "configured-only": {
                "name": "Configured Only",
                "description": "should not appear",
                "path": "/home/gem/skills/configured-only/SKILL.md",
            },
        },
    )

    class FakeRequest:
        def __init__(self, *, system_message=None, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools or []
            self.system_message = system_message or SystemMessage(content="base")

        def override(self, **kwargs):
            return FakeRequest(
                system_message=kwargs.get("system_message", self.system_message),
                tools=kwargs.get("tools", self.tools),
            )

    captured = {}

    async def handler(request):
        captured["system_message"] = request.system_message
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(FakeRequest(), handler)
    prompt_text = _system_message_text(captured["system_message"])

    assert result == "ok"
    assert "base" in prompt_text
    assert "Alpha" in prompt_text
    assert "Configured Only" not in prompt_text
    assert context.system_prompt == "context base"
    assert not hasattr(context, "_skills_prompt_injected")
    assert not hasattr(context, "_visible_skills")


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_dependencies_only_for_readable_activated_skills(monkeypatch):
    monkeypatch.setattr(
        skills_middleware,
        "get_all_tool_instances",
        lambda: [SimpleNamespace(name="tool-a"), SimpleNamespace(name="tool-b")],
    )

    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _readable_skills=["alpha"],
                    _runtime_skill_dependency_map={
                        "alpha": {"tools": ["tool-a"], "mcps": [], "skills": []},
                        "beta": {"tools": ["tool-b"], "mcps": [], "skills": []},
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": ["alpha", "beta"]}
            self.tools = tools or []

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = [tool.name for tool in request.tools]
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == ["tool-a"]


def test_read_file_activates_only_readable_skill() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(_readable_skills=["alpha"])),
        tool_call={"name": "read_file", "args": {"file_path": "/home/gem/skills/alpha/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert isinstance(updated, Command)
    assert updated.update["activated_skills"] == ["alpha"]


def test_read_file_denies_skill_outside_readable_scope() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(_readable_skills=["alpha"])),
        tool_call={"name": "read_file", "args": {"file_path": "/home/gem/skills/beta/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert updated is result
