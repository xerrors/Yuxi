from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

import yuxi.agents.middlewares.skills as skills_middleware
from yuxi.agents.middlewares.skills import (
    SkillsMiddleware,
    resolve_runtime_skills_for_context,
    resolve_skill_gated_tools,
)
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

_KB_TOOL_NAMES = {
    "list_kbs",
    "query_kb",
    "find_kb_document",
    "open_kb_document",
    "get_mindmap",
}


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
                source_scope="shared",
                source_dir="/tmp/shared/alpha",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                source_scope="personal",
                source_dir="/tmp/personal/beta",
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
    assert scope["runtime_skill_metadata"]["alpha"]["path"] == "/home/gem/skills/alpha/SKILL.md"
    assert (
        scope["runtime_skill_metadata"]["beta"]["path"] == "/home/gem/user-data/workspace/agents/skills/beta/SKILL.md"
    )
    assert scope["runtime_skill_sources"] == {"alpha": "/tmp/shared/alpha"}
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


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_knowledge_base_skill_tools():
    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _readable_skills=["knowledge-base"],
                    _runtime_skill_dependency_map={
                        "knowledge-base": {
                            "tools": [
                                "list_kbs",
                                "query_kb",
                                "find_kb_document",
                                "open_kb_document",
                                "get_mindmap",
                            ],
                            "mcps": [],
                            "skills": [],
                        }
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": ["knowledge-base"]}
            self.tools = tools or []

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = {tool.name for tool in request.tools}
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == {
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
        "get_mindmap",
    }


@pytest.mark.asyncio
async def test_resolve_skill_gated_tools_registers_kb_tools():
    """门控工具必须能从可见 Skill 的依赖解析出真实工具实例，并随基础工具一起进入
    create_agent 工具列表（即注册进 ToolNode），否则激活后仍报 not a valid tool。"""
    context = SimpleNamespace(
        tools=None,
        mcps=None,
        _readable_skills=["knowledge-base"],
        _runtime_skill_dependency_map={"knowledge-base": {"tools": sorted(_KB_TOOL_NAMES), "mcps": [], "skills": []}},
    )

    gated_tools = resolve_skill_gated_tools(context)
    assert {tool.name for tool in gated_tools} == _KB_TOOL_NAMES

    runtime_tools = await resolve_configured_runtime_tools(context)
    assert _KB_TOOL_NAMES <= {tool.name for tool in runtime_tools}


def _make_gated_request(activated):
    base = SimpleNamespace(name="read_file")
    gated = [SimpleNamespace(name="list_kbs"), SimpleNamespace(name="query_kb")]

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _readable_skills=["knowledge-base"],
                    _runtime_skill_dependency_map={
                        "knowledge-base": {"tools": ["list_kbs", "query_kb"], "mcps": [], "skills": []}
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": activated}
            self.tools = tools

        def override(self, *, tools):
            new_request = FakeRequest(tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    # ToolNode 默认绑定 = 基础工具 + 门控工具
    return FakeRequest([base, *gated])


@pytest.mark.asyncio
async def test_awrap_model_call_hides_gated_tools_until_activated():
    """未激活 Skill 时门控工具对模型不可见（懒加载），激活后才放出。"""
    request = _make_gated_request(activated=[])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await SkillsMiddleware().awrap_model_call(request, handler)

    assert captured["tools"] == {"read_file"}


@pytest.mark.asyncio
async def test_awrap_model_call_keeps_gated_tools_when_activated():
    request = _make_gated_request(activated=["knowledge-base"])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await SkillsMiddleware().awrap_model_call(request, handler)

    assert captured["tools"] == {"read_file", "list_kbs", "query_kb"}


@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gem/skills/alpha/SKILL.md",
        "/home/gem/user-data/workspace/agents/skills/alpha/SKILL.md",
    ],
)
def test_read_file_activates_only_readable_skill(file_path: str) -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(_readable_skills=["alpha"])),
        tool_call={"name": "read_file", "args": {"file_path": file_path}},
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
