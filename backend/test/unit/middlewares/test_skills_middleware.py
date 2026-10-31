from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

import yuxi.modules.agents.runtime.middlewares.skills as skills_middleware
from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware
from yuxi.modules.extensions.tools.runtime import resolve_skill_gated_tools
from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools

_KB_TOOL_NAMES = {
    "list_kbs",
    "query_kb",
    "find_kb_document",
    "open_kb_document",
}


def _system_message_text(message: SystemMessage) -> str:
    return "\n".join(block.get("text", "") for block in message.content_blocks if isinstance(block, dict))


def _runtime_skill(
    slug: str,
    *,
    name: str | None = None,
    description: str = "",
    tools: list[str] | None = None,
    mcps: list[str] | None = None,
) -> dict:
    return {
        "name": name or slug,
        "description": description,
        "path": f"/home/user/skills/{slug}/SKILL.md",
        "tools": tools or [],
        "mcps": mcps or [],
        "skills": [],
    }


def _as_model_request(request):
    """将测试上下文装配为支持完整提示与工具替换的正式请求。"""
    return ModelRequest(
        model=SimpleNamespace(),
        messages=[],
        system_message=getattr(request, "system_message", None),
        tools=getattr(request, "tools", []),
        state=request.state,
        runtime=request.runtime,
    )


async def _authorized_model_call(monkeypatch, middleware, request, handler, *, mcp_policy=None):
    """按生产顺序组合 Skills 与真实授权中间件，只替换持久权限读取。"""
    from yuxi.modules.agents.runtime.middlewares import authorization

    context = request.runtime.context
    context.tools = getattr(context, "tools", [])
    context.mcps = getattr(context, "mcps", [])
    context.middleware_tool_names = {"read_file"} | getattr(context, "middleware_tool_names", set())
    context.registered_builtin_tool_names = {
        tool.name for tool in request.tools if tool.name not in context.mcp_tool_servers and tool.name != "read_file"
    }
    monkeypatch.setattr(authorization, "refresh_execution_authorization", AsyncMock(return_value=mcp_policy or {}))

    async def authorize(current):
        """最终工具集交给生产授权实现。"""
        return await authorization.RuntimeAuthorizationMiddleware().awrap_model_call(current, handler)

    return await middleware.awrap_model_call(_as_model_request(request), authorize)


@pytest.mark.asyncio
async def test_skills_prompt_uses_effective_skills_at_request_level():
    context = SimpleNamespace(
        mcp_tools={},
        system_prompt="context base",
        skills=["configured-only"],
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["alpha"],
            "runtime_skills": {
                "alpha": _runtime_skill("alpha", name="Alpha", description="alpha desc"),
                "configured-only": _runtime_skill("configured-only", name="Configured Only", description="should not appear"),
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
async def test_preloaded_skill_injects_full_instructions_once_and_hides_lazy_read_hint():
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "effective_skills": ["alpha", "beta"],
            "preloaded_skills": ["alpha"],
            "preloaded_skill_contents": {"alpha": "# Alpha full instructions\nUSE_ALPHA_TOOL"},
            "runtime_skills": {
                "alpha": _runtime_skill("alpha", name="Alpha", description="alpha desc"),
                "beta": _runtime_skill("beta", name="Beta", description="beta desc"),
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

    captured = []

    async def handler(request):
        captured.append(_system_message_text(request.system_message))
        return "ok"

    middleware = SkillsMiddleware()
    original_request = FakeRequest()
    await middleware.awrap_model_call(original_request, handler)
    await middleware.awrap_model_call(original_request, handler)

    assert len(captured) == 2
    assert all(text.count("USE_ALPHA_TOOL") == 1 for text in captured)
    assert all("Read `/home/user/skills/alpha/SKILL.md`" not in text for text in captured)
    assert all("Read `/home/user/skills/beta/SKILL.md`" in text for text in captured)


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_dependencies_only_for_readable_activated_skills(monkeypatch):
    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    mcps=[],
                    mcp_tool_servers={},
                    mcp_tools={},
                    skill_runtime_snapshot={
                        "capability_limited": False,
                        "context_skills": [],
                        "context_preload_skills": [],
                        "skill_metadata": {},
                        "preloaded_skills": [],
                        "preloaded_skill_contents": {},
                        "effective_skills": ["alpha"],
                        "runtime_skills": {
                            "alpha": _runtime_skill("alpha", tools=["tool-a"]),
                            "beta": _runtime_skill("beta", tools=["tool-b"]),
                        },
                    },
                )
            )
            self.state = {"activated_skills": ["alpha", "beta"]}
            self.tools = tools if tools is not None else [SimpleNamespace(name="tool-a"), SimpleNamespace(name="tool-b")]

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = [tool.name for tool in request.tools]
        return "ok"

    result = await _authorized_model_call(monkeypatch, SkillsMiddleware(), FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == ["tool-a"]


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_knowledge_base_skill_tools(monkeypatch):
    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    mcps=[],
                    mcp_tool_servers={},
                    mcp_tools={},
                    skill_runtime_snapshot={
                        "capability_limited": False,
                        "context_skills": [],
                        "context_preload_skills": [],
                        "skill_metadata": {},
                        "preloaded_skills": [],
                        "preloaded_skill_contents": {},
                        "effective_skills": ["knowledge-base"],
                        "runtime_skills": {
                            "knowledge-base": _runtime_skill(
                                "knowledge-base",
                                tools=sorted(_KB_TOOL_NAMES),
                            )
                        },
                    },
                )
            )
            self.state = {"activated_skills": ["knowledge-base"]}
            self.tools = tools if tools is not None else [SimpleNamespace(name=name) for name in sorted(_KB_TOOL_NAMES)]

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = {tool.name for tool in request.tools}
        return "ok"

    result = await _authorized_model_call(monkeypatch, SkillsMiddleware(), FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == {
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
    }


@pytest.mark.asyncio
async def test_resolve_skill_gated_tools_registers_local_skill_tools(monkeypatch):
    """门控工具必须能从可见 Skill 的依赖解析出真实工具实例，并随基础工具一起进入
    create_agent 工具列表（即注册进 ToolNode），否则激活后仍报 not a valid tool。"""
    from yuxi.modules.extensions.tools import registry as tool_registry

    demo = SimpleNamespace(name="demo_local_tool", description="fixture")
    monkeypatch.setattr(tool_registry, "_all_tool_instances", [*tool_registry.get_all_tool_instances(), demo])
    context = SimpleNamespace(
        tools=None,
        mcps=None,
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["knowledge-base"],
            "runtime_skills": {"knowledge-base": _runtime_skill("knowledge-base", tools=["demo_local_tool"])},
        },
    )

    gated_tools = resolve_skill_gated_tools(context)
    assert [tool.name for tool in gated_tools] == ["demo_local_tool"]

    runtime_tools = await resolve_configured_runtime_tools(context)
    assert "demo_local_tool" in {tool.name for tool in runtime_tools}


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", [None, "test-run"])
async def test_tool_resolver_only_registers_explicit_selection(run_id):
    """通用解析器不隐式追加基础工具，只注册显式选择且去重。"""
    context = SimpleNamespace(
        run_id=run_id,
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": [],
            "runtime_skills": {},
        },
    )
    tools = await resolve_configured_runtime_tools(context)
    assert tools == []
    context.tools = ["mark_for_improvement", "ask_user_question", "present_artifacts", "ask_user_question"]
    tools = await resolve_configured_runtime_tools(context)
    assert [tool.name for tool in tools] == ["mark_for_improvement", "ask_user_question", "present_artifacts"]


@pytest.mark.asyncio
async def test_unactivated_skill_cannot_hide_base_tools(monkeypatch):
    """Skill 声明基础工具依赖时，模型仍能看到基础工具，普通依赖保持隐藏。"""
    context = SimpleNamespace(
        tools=[],
        middleware_tool_names={"ask_user_question", "mark_for_improvement"},
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["alpha"],
            "runtime_skills": {"alpha": _runtime_skill("alpha", tools=["ask_user_question", "mark_for_improvement", "query_kb"])},
        },
    )
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={},
        tools=[SimpleNamespace(name=name) for name in ["ask_user_question", "mark_for_improvement", "query_kb"]],
    )
    request.override = lambda **updates: SimpleNamespace(**{**vars(request), **updates})

    async def handler(current):
        """读取实际交给模型的工具集合。"""
        return [tool.name for tool in current.tools]

    visible = await _authorized_model_call(monkeypatch, SkillsMiddleware(), request, handler)
    assert visible == ["ask_user_question", "mark_for_improvement"]


@pytest.mark.asyncio
async def test_improvement_tool_without_run_rejects_untraceable_write():
    """工具可被注册，但缺少真实执行归属时明确拒绝保存。"""
    from langchain_core.tools import ToolException
    from yuxi.modules.extensions.tools.builtin.mark_for_improvement import mark_for_improvement

    runtime = SimpleNamespace(context=SimpleNamespace(run_id=None), tool_call_id="call-test")
    with pytest.raises(ToolException, match="未绑定真实 Run"):
        await mark_for_improvement.coroutine(reason="可复用经验", runtime=runtime)


@pytest.mark.asyncio
async def test_preloaded_skill_rejects_duplicate_mcp_tool_names(monkeypatch):
    @tool("chart_tool")
    async def chart_tool(value: int) -> str:
        """渲染测试图表。"""

        return f"rendered:{value}"

    @tool("chart_tool")
    async def conflicting_chart_tool(value: int) -> str:
        """同名但来自另一服务的测试工具。"""

        return f"wrong-service:{value}"

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        result = {name: [chart_tool] if name == "charts" else [conflicting_chart_tool] for name in server_names}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skill_contents": {"report": "# Report instructions"},
            "effective_skills": ["report"],
            "preloaded_skills": ["report"],
            "runtime_skills": {"report": _runtime_skill("report", mcps=["charts", "conflicting-charts"])},
        },
    )

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools

        def override(self, *, tools):
            request = FakeRequest(tools)
            request.runtime = self.runtime
            return request

    middleware = SkillsMiddleware()
    with pytest.raises(RuntimeError, match="Skill MCP 工具名冲突"):
        await middleware.awrap_model_call(_as_model_request(FakeRequest([])), AsyncMock())


@pytest.mark.asyncio
async def test_activated_mcp_rejects_name_of_inactive_registered_skill_tool(monkeypatch):
    """未激活本地工具虽对模型隐藏，仍占用 ToolNode 的工具名。"""

    @tool("chart_tool")
    async def chart_tool(value: int) -> str:
        """模拟已激活 Skill 的 MCP 工具。"""

        return f"mcp:{value}"

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        assert server_names == ["charts"]
        result = {name: [chart_tool] for name in server_names}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["report", "other"],
            "runtime_skills": {
                "report": _runtime_skill("report", mcps=["charts"]),
                "other": _runtime_skill("other", tools=["chart_tool"]),
            },
        },
    )

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {"activated_skills": ["report"]}
            self.tools = tools

        def override(self, *, tools):
            return FakeRequest(tools)

    registered_local_tool = SimpleNamespace(name="chart_tool")
    with pytest.raises(RuntimeError, match="Skill MCP 工具名冲突：chart_tool"):
        await SkillsMiddleware().awrap_model_call(_as_model_request(FakeRequest([registered_local_tool])), AsyncMock())


@pytest.mark.asyncio
async def test_preloaded_skill_exposes_mcp_tool_on_first_model_call(monkeypatch):
    @tool("chart_tool")
    async def chart_tool(value: int) -> str:
        """渲染测试图表。"""

        return f"rendered:{value}"

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        assert server_names == ["charts"]
        assert uid == "employee-runtime"
        result = {name: [chart_tool] for name in server_names}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        uid="employee-runtime",
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skill_contents": {"report": "# Report instructions"},
            "effective_skills": ["report"],
            "preloaded_skills": ["report"],
            "runtime_skills": {"report": _runtime_skill("report", mcps=["charts"])},
        },
    )

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools

        def override(self, *, tools):
            request = FakeRequest(tools)
            request.runtime = self.runtime
            return request

    captured = []

    async def handler(request):
        captured.append([item.name for item in request.tools])
        return "ok"

    assert await SkillsMiddleware().awrap_model_call(_as_model_request(FakeRequest([])), handler) == "ok"
    assert captured == [["chart_tool"]]


@pytest.mark.asyncio
async def test_activated_skill_loads_mcp_without_agent_selection(monkeypatch):
    """普通 Skill 激活后才加载依赖的 MCP，Agent 无需直接选择。"""

    @tool("chart_tool")
    async def chart_tool(value: int) -> str:
        """渲染测试图表。"""

        return f"rendered:{value}"

    calls = []

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        calls.extend(server_names)
        result = {name: [chart_tool] for name in server_names}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["report"],
            "runtime_skills": {"report": _runtime_skill("report", mcps=["charts"])},
        },
    )

    class FakeRequest:
        def __init__(self, state, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = state
            self.tools = tools or []

        def override(self, *, tools):
            return FakeRequest(self.state, tools)

    captured = []

    async def handler(request):
        captured.append([item.name for item in request.tools])
        return "ok"

    middleware = SkillsMiddleware()
    await middleware.awrap_model_call(_as_model_request(FakeRequest({})), handler)
    assert calls == []
    await middleware.awrap_model_call(_as_model_request(FakeRequest({"activated_skills": ["report"]})), handler)

    assert calls == ["charts"]
    assert captured == [[], ["chart_tool"]]

    tool_request = ToolCallRequest(
        tool_call={"name": "chart_tool", "args": {"value": 3}, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=SimpleNamespace(context=context),
    )

    async def execute_bound_tool(request):
        assert request.tool is chart_tool
        return await request.tool.ainvoke(request.tool_call["args"])

    assert await middleware.awrap_tool_call(tool_request, execute_bound_tool) == "rendered:3"


@pytest.mark.asyncio
async def test_skill_reusing_explicit_mcp_server_does_not_duplicate_registered_tool(monkeypatch):
    @tool("chart_tool")
    async def chart_tool(value: int) -> str:
        """渲染测试图表。"""

        return f"rendered:{value}"

    calls = []

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        calls.extend(server_names)
        result = {name: [chart_tool] for name in server_names}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        tools=[],
        mcps=["charts"],
        mcp_tool_servers={"chart_tool": "charts"},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skill_contents": {"report": "# Report instructions"},
            "effective_skills": ["report"],
            "preloaded_skills": ["report"],
            "runtime_skills": {"report": _runtime_skill("report", mcps=["charts"])},
        },
    )

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools

        def override(self, *, tools):
            request = FakeRequest(tools)
            request.runtime = self.runtime
            return request

    captured = []

    async def handler(request):
        captured.append([item.name for item in request.tools])
        return "ok"

    middleware = SkillsMiddleware()
    assert await middleware.awrap_model_call(_as_model_request(FakeRequest([chart_tool])), handler) == "ok"
    assert captured == [["chart_tool"]]
    assert calls == ["charts"]


@pytest.mark.asyncio
async def test_explicit_mcp_rejects_skill_local_tool_name_collision(monkeypatch):
    from yuxi.modules.extensions.tools import registry as tool_registry

    demo = SimpleNamespace(name="demo_local_tool", description="fixture")
    monkeypatch.setattr(tool_registry, "_all_tool_instances", [*tool_registry.get_all_tool_instances(), demo])

    @tool("demo_local_tool")
    async def conflicting_demo_tool() -> str:
        """模拟与 Skill 本地依赖同名的显式 MCP 工具。"""

        return "wrong-source"

    async def fake_resolve_runtime_mcp_tools(
        server_names, *, uid=None, run_tools=None, unavailable=None, context=None
    ):
        assert server_names == ["configured"]
        result = {"configured": [conflicting_demo_tool]}
        if run_tools is not None:
            run_tools.update(result)
        return result

    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.resolve_runtime_mcp_tools", fake_resolve_runtime_mcp_tools)
    context = SimpleNamespace(
        tools=[],
        mcps=["configured"],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["knowledge-base"],
            "runtime_skills": {"knowledge-base": _runtime_skill("knowledge-base", tools=["demo_local_tool"])},
        },
    )

    with pytest.raises(RuntimeError, match="Skill 本地工具 'demo_local_tool'"):
        await resolve_configured_runtime_tools(context)


def _make_gated_request(activated, *, preloaded=None):
    base = SimpleNamespace(name="read_file")
    gated = [SimpleNamespace(name="list_kbs"), SimpleNamespace(name="query_kb")]

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    mcps=[],
                    mcp_tool_servers={},
                    mcp_tools={},
                    skill_runtime_snapshot={
                        "capability_limited": False,
                        "context_skills": [],
                        "context_preload_skills": [],
                        "skill_metadata": {},
                        "preloaded_skills": [],
                        "preloaded_skill_contents": {},
                        "effective_skills": ["knowledge-base"],
                        "runtime_skills": {"knowledge-base": _runtime_skill("knowledge-base", tools=["list_kbs", "query_kb"])},
                    },
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
async def test_awrap_model_call_hides_gated_tools_until_activated(monkeypatch):
    """未激活 Skill 时门控工具对模型不可见（懒加载），激活后才放出。"""
    request = _make_gated_request(activated=[])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await _authorized_model_call(monkeypatch, SkillsMiddleware(), request, handler)

    assert captured["tools"] == {"read_file"}


@pytest.mark.asyncio
async def test_awrap_model_call_keeps_gated_tools_when_activated(monkeypatch):
    request = _make_gated_request(activated=["knowledge-base"])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await _authorized_model_call(monkeypatch, SkillsMiddleware(), request, handler)

    assert captured["tools"] == {"read_file", "list_kbs", "query_kb"}


def test_read_file_activates_only_readable_skill() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                skill_runtime_snapshot={
                    "capability_limited": False,
                    "context_skills": [],
                    "context_preload_skills": [],
                    "skill_metadata": {},
                    "preloaded_skills": [],
                    "preloaded_skill_contents": {},
                    "effective_skills": ["alpha"],
                }
            )
        ),
        tool_call={"name": "read_file", "args": {"file_path": "/home/user/skills/alpha/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert isinstance(updated, Command)
    assert updated.update["activated_skills"] == ["alpha"]


@pytest.mark.parametrize("wrapped_in_command", [False, True])
def test_failed_read_file_does_not_activate_skill(wrapped_in_command) -> None:
    """读取说明失败时不得开放 Skill 声明的 MCP 依赖。"""
    middleware = SkillsMiddleware()
    error = ToolMessage(content="read failed", tool_call_id="tool-1", name="read_file", status="error")
    result = Command(update={"messages": [error]}) if wrapped_in_command else error
    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                skill_runtime_snapshot={
                    "capability_limited": False,
                    "context_skills": [],
                    "context_preload_skills": [],
                    "skill_metadata": {},
                    "preloaded_skills": [],
                    "preloaded_skill_contents": {},
                    "effective_skills": ["alpha"],
                }
            )
        ),
        tool_call={"name": "read_file", "args": {"file_path": "/home/user/skills/alpha/SKILL.md"}},
    )

    assert middleware._process_tool_call_result(result, request) is result


@pytest.mark.parametrize("limit", [0, -1])
def test_zero_line_read_does_not_activate_skill(limit) -> None:
    """零行读取返回成功提示时仍未读取 Skill 说明。"""
    middleware = SkillsMiddleware()
    result = ToolMessage(content="No lines requested", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                skill_runtime_snapshot={
                    "capability_limited": False,
                    "context_skills": [],
                    "context_preload_skills": [],
                    "skill_metadata": {},
                    "preloaded_skills": [],
                    "preloaded_skill_contents": {},
                    "effective_skills": ["alpha"],
                }
            )
        ),
        tool_call={"name": "read_file", "args": {"file_path": "/home/user/skills/alpha/SKILL.md", "limit": limit}},
    )

    assert middleware._process_tool_call_result(result, request) is result


def test_personal_workspace_path_activates_skill() -> None:
    middleware = SkillsMiddleware()

    slug = middleware._extract_skill_slug_from_skill_md_path("/home/user/workspace/agents/skills/alpha/SKILL.md")

    assert slug == "alpha"


def test_read_file_denies_skill_outside_readable_scope() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                skill_runtime_snapshot={
                    "capability_limited": False,
                    "context_skills": [],
                    "context_preload_skills": [],
                    "skill_metadata": {},
                    "preloaded_skills": [],
                    "preloaded_skill_contents": {},
                    "effective_skills": ["alpha"],
                }
            )
        ),
        tool_call={"name": "read_file", "args": {"file_path": "/home/user/skills/beta/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert updated is result


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["configured", "skill"])
async def test_mcp_failure_reaches_runtime_instead_of_removing_tools(monkeypatch, source):
    """显式 MCP 与 Skill MCP 加载失败都保留运行失败，不变成无工具成功。"""
    failure = RuntimeError("MCP 服务 demo 工具加载失败")
    loader = AsyncMock(side_effect=failure)
    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.resolve_runtime_mcp_tools", loader)
    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", loader)
    context = SimpleNamespace(tools=[], mcps=["demo"] if source == "configured" else [], mcp_tool_servers={}, mcp_tools={})
    with pytest.raises(RuntimeError, match="MCP 服务 demo") as caught:
        if source == "configured":
            await resolve_configured_runtime_tools(context)
        else:
            context.skill_runtime_snapshot = {
                "effective_skills": ["report"],
                "preloaded_skills": ["report"],
                "preloaded_skill_contents": {"report": "# Report instructions"},
                "runtime_skills": {"report": _runtime_skill("report", mcps=["demo"])},
            }
            request = SimpleNamespace(runtime=SimpleNamespace(context=context), state={})
            await SkillsMiddleware().awrap_model_call(_as_model_request(request), AsyncMock())
    assert caught.value is failure
    assert context.mcp_tool_servers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_skill_does_not_reintroduce_unconfigured_web_tools(configured):
    """激活搜索 Skill 只能暴露本轮已经装配的网页能力。"""
    from yuxi.modules.extensions.tools.builtin.web_search import web_search, web_fetch

    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": ["research"],
            "runtime_skills": {"research": _runtime_skill("research", tools=["web_search", "web_fetch"])},
        },
    )

    class Request:
        """保留真实工具实例与 Skill 上下文的模型请求。"""

        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {"activated_skills": ["research"]}
            self.tools = tools

        def override(self, *, tools):
            """模拟模型请求仅替换可见工具。"""
            return Request(tools)

    async def inspect(request):
        """核对最终交给模型的工具集合。"""
        names = {item.name for item in request.tools}
        assert names == ({"web_search", "web_fetch"} if configured else set())
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(
        _as_model_request(Request([web_search, web_fetch] if configured else [])),
        inspect,
    )
    assert result == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("via_skill", [False, True, "nested", "dynamic_nested"])
async def test_unconnected_mcp_preserves_chat_and_hides_skill_dependencies(monkeypatch, via_skill):
    """缺少个人授权时普通模型仍执行，Skill 本地依赖也不能伪装可用。"""
    resolver = AsyncMock(return_value={})
    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.resolve_runtime_mcp_tools", resolver)
    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", resolver)
    context = SimpleNamespace(
        uid="employee",
        run_id="run",
        thread_id="thread",
        _authorization_turn_id="turn",
        tools=[],
        mcps=[] if via_skill else ["attendance"],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": [],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": ["report"] if via_skill else [],
            "preloaded_skill_contents": {"report": "# Report instructions"},
            "effective_skills": ["report"],
            "runtime_skills": {"report": _runtime_skill("report", tools=["list_kbs"], mcps=["attendance"])},
        },
    )
    if via_skill in {"nested", "dynamic_nested"}:
        snapshot = context.skill_runtime_snapshot
        snapshot["effective_skills"].append("parent")
        snapshot["preloaded_skills"] = ["parent", "report"]
        snapshot["preloaded_skill_contents"]["parent"] = "# Parent instructions"
        snapshot["runtime_skills"]["report"]["tools"] = []
        snapshot["runtime_skills"]["parent"] = _runtime_skill("parent", tools=["list_kbs"])
        snapshot["runtime_skills"]["parent"]["skills"] = ["report"]
    if via_skill == "dynamic_nested":
        context.skill_runtime_snapshot["preloaded_skills"] = []
    if not via_skill:
        await resolve_configured_runtime_tools(context)

    class Request:
        """观察实际送入模型的工具与系统提示。"""

        def __init__(self, *, tools=None, system_message=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {"activated_skills": ["parent"]} if via_skill == "dynamic_nested" else {}
            self.tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="list_kbs")] if tools is None else tools
            self.system_message = system_message or SystemMessage(content="base")

        def override(self, **kwargs):
            return Request(tools=kwargs.get("tools", self.tools), system_message=kwargs.get("system_message", self.system_message))

    observed = []

    async def handler(request):
        """普通模型请求真实到达下游，提示和工具可见性均有可观察结果。"""
        observed.append(request)
        return "normal chat"

    middleware = SkillsMiddleware()
    for _ in range(2):
        assert await _authorized_model_call(monkeypatch, middleware, Request(), handler) == "normal chat"
    assert _system_message_text(observed[0].system_message).startswith("base")
    assert "report" in _system_message_text(observed[0].system_message)
    assert {tool.name for tool in observed[0].tools} == {"read_file"}


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_dynamic_parent_uses_service_result_for_nested_mcp_dependency(monkeypatch, connected):
    """子 Skill 的 MCP 可用时父工具开放，子 Skill 自身工具仍等待激活。"""
    resolver = AsyncMock(return_value={})
    mcp_policy = {"attendance": set()} if connected else {}
    monkeypatch.setattr(skills_middleware, "resolve_runtime_mcp_tools", resolver)
    parent = _runtime_skill("parent", tools=["list_kbs"])
    parent["skills"] = ["child"]
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        registered_builtin_tool_names={"list_kbs"},
        skill_runtime_snapshot={
            "preloaded_skills": [],
            "effective_skills": ["parent", "child"],
            "runtime_skills": {"parent": parent, "child": _runtime_skill("child", mcps=["attendance"])},
        },
    )

    class Request:
        """观察实际模型获得的依赖工具。"""

        def __init__(self, tools):
            self.runtime = SimpleNamespace(context=context)
            self.state = {"activated_skills": ["parent"]}
            self.tools = tools

        def override(self, *, tools):
            """保留本轮上下文与激活状态。"""
            return Request(tools)

    async def handler(request):
        """检查可用服务影响父工具，但不隐式激活子 Skill。"""
        from yuxi.modules.agents.runtime.middlewares.authorization import tool_allowed

        assert tool_allowed(context, "list_kbs", request.state, mcp_policy) is connected
        return {tool.name for tool in request.tools}

    names = await _authorized_model_call(
        monkeypatch,
        SkillsMiddleware(),
        Request([SimpleNamespace(name="list_kbs")]),
        handler,
        mcp_policy=mcp_policy,
    )
    assert names == ({"list_kbs"} if connected else set())
    resolver.assert_not_called()
    assert context.mcp_tool_servers == {}
