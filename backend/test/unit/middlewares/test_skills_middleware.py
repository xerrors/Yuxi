from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware
from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools


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
    context.middleware_tool_names = {"read_file"}
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
            "context_skills": ["alpha", "beta"],
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
            "context_skills": ["alpha", "beta"],
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
@pytest.mark.parametrize("run_id", [None, "test-run"])
async def test_base_tools_are_registered_once_without_configuration_or_skill(run_id):
    """基础工具来自同一注册循环，空配置可用，重复选择不会重复注册。"""
    context = SimpleNamespace(
        run_id=run_id,
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": ["alpha", "beta"],
            "context_preload_skills": [],
            "skill_metadata": {},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "effective_skills": [],
            "runtime_skills": {},
        },
    )
    tools = await resolve_configured_runtime_tools(context)
    assert [tool.name for tool in tools] == ["ask_user_question", "mark_for_improvement"]
    context.tools = ["mark_for_improvement", "ask_user_question", "present_artifacts"]
    tools = await resolve_configured_runtime_tools(context)
    assert [tool.name for tool in tools] == ["ask_user_question", "mark_for_improvement", "present_artifacts"]


@pytest.mark.asyncio
async def test_skill_disclosure_does_not_grant_tools(monkeypatch):
    """Skill 内容披露不改变已注册工具授权，基础工具仍可用。"""
    context = SimpleNamespace(
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        mcp_tools={},
        skill_runtime_snapshot={
            "capability_limited": False,
            "context_skills": ["alpha", "beta"],
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
@pytest.mark.parametrize(
    "activated,direct,revoked,visible",
    [(False, False, False, False), (True, False, False, True), (False, True, False, True), (True, False, True, False)],
)
async def test_plugin_companion_summary_requires_activation_unless_directly_selected(activated, direct, revoked, visible):
    """配套说明随插件披露，独立选择的普通内容继续可见。"""
    context = SimpleNamespace(
        plugins=["abridge"],
        skill_runtime_snapshot={
            "context_skills": ["chat-widgets"] if direct else [],
            "effective_skills": ["abridge", "chat-widgets"],
            "plugin_entry_contents": {} if revoked else {"abridge": "entry"},
            "preloaded_skills": [],
            "preloaded_skill_contents": {},
            "skill_metadata": {},
            "runtime_skills": {slug: _runtime_skill(slug) for slug in ["abridge", "chat-widgets"]},
        },
    )
    request = ModelRequest(
        model=SimpleNamespace(),
        messages=[],
        tools=[],
        system_message=SystemMessage(content="base"),
        state={"activated_plugins": ["abridge"] if activated else []},
        runtime=SimpleNamespace(context=context),
    )

    async def handler(current):
        return _system_message_text(current.system_message)

    prompt = await SkillsMiddleware().awrap_model_call(request, handler)
    assert ("chat-widgets/SKILL.md" in prompt) == visible
    assert "abridge/SKILL.md" not in prompt
