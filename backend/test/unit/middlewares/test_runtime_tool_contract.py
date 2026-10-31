"""工具装配缺失和未知来源不能通过当前授权边界。"""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.runtime.middlewares.authorization import tool_allowed


@pytest.fixture
def context():
    """分别声明已注册本地、框架和 MCP 工具。"""
    return SimpleNamespace(
        tools=["local"],
        mcps=["server"],
        registered_builtin_tool_names={"local", "gated"},
        middleware_tool_names={"read_file"},
        mcp_tool_servers={"remote": "server"},
        skill_runtime_snapshot={"effective_skills": [], "preloaded_skills": [], "runtime_skills": {}},
    )


@pytest.fixture
def mcp_policy():
    """本次 service 查询结果只在当前调用内传递。"""
    return {"server": set()}


def test_only_registered_and_currently_authorized_tools_are_accepted(context, mcp_policy):
    """合法框架工具继续可用，未配置和未注册工具拒绝。"""
    assert tool_allowed(context, "local", {}, mcp_policy)
    assert tool_allowed(context, "read_file", {}, mcp_policy)
    assert tool_allowed(context, "remote", {}, mcp_policy)
    assert not tool_allowed(context, "gated", {}, mcp_policy)
    assert not tool_allowed(context, "unknown", {}, mcp_policy)
    mcp_policy["server"].add("remote")
    assert not tool_allowed(context, "remote", {}, mcp_policy)


@pytest.mark.parametrize("name", ["registered_builtin_tool_names", "mcp_tool_servers", "skill_runtime_snapshot"])
def test_missing_or_unprepared_tool_assembly_cannot_authorize(context, mcp_policy, name):
    """替代 Context 的缺失或未初始化状态均不能放行。"""
    setattr(context, name, None)
    assert not tool_allowed(context, "read_file", {}, mcp_policy)
    delattr(context, name)
    assert not tool_allowed(context, "local", {}, mcp_policy)


def test_runtime_fields_are_declared_and_excluded_from_user_configuration():
    """运行身份与授权派生内容不能由持久配置伪造。"""
    names = {
        "skill_runtime_snapshot",
        "mcp_tool_servers",
        "mcp_tools",
        "registered_builtin_tool_names",
        "middleware_tool_names",
    }
    schema = {item.name: item for item in fields(BaseContext)}
    assert names <= schema.keys()
    assert all(schema[name].metadata["configurable"] is False for name in names)
    assert not names & BaseContext.get_configurable_items().keys()
    context = BaseContext()
    context.update_config({name: {"forged": "value"} for name in names})
    assert context.skill_runtime_snapshot is None
    assert context.mcp_tool_servers is None
    assert context.registered_builtin_tool_names is None
    assert context.middleware_tool_names == set()


@pytest.mark.asyncio
async def test_dynamic_mcp_loading_requires_prior_tool_source_assembly():
    """Skill 动态建连不能在缺失来源记录时开始。"""
    from unittest.mock import AsyncMock

    from langchain.agents.middleware.types import ModelRequest

    from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware

    context = BaseContext()
    context.skill_runtime_snapshot = {
        "effective_skills": ["report"],
        "preloaded_skills": ["report"],
        "preloaded_skill_contents": {"report": "# Report instructions"},
        "runtime_skills": {"report": {"tools": [], "mcps": ["server"]}},
    }
    request = ModelRequest(model=SimpleNamespace(), messages=[], tools=[], runtime=SimpleNamespace(context=context), state={})
    with pytest.raises(RuntimeError, match="来源尚未装配"):
        await SkillsMiddleware().awrap_model_call(request, AsyncMock())


def test_missing_mcp_connection_blocks_skill_dependencies_but_preserves_explicit_tools(context, mcp_policy):
    """模型之外直接调用也不能绕过不可用 Skill，显式工具仍独立可用。"""
    context.skill_runtime_snapshot = {
        "effective_skills": ["report"],
        "preloaded_skills": ["report"],
        "runtime_skills": {"report": {"tools": ["gated"], "mcps": ["server"]}},
    }
    mcp_policy = {}
    assert not tool_allowed(context, "gated", {}, mcp_policy)
    assert tool_allowed(context, "local", {}, mcp_policy)
    mcp_policy = {"server": set()}
    assert tool_allowed(context, "gated", {}, mcp_policy)


def test_nested_skill_cannot_expose_local_tools_when_dependency_mcp_is_unconnected(context, mcp_policy):
    """子 Skill 缺少授权时，父 Skill 的本地工具也不能执行。"""
    context.skill_runtime_snapshot = {
        "effective_skills": ["parent", "child"],
        "preloaded_skills": ["parent", "child"],
        "runtime_skills": {
            "parent": {"tools": ["gated"], "mcps": [], "skills": ["child"]},
            "child": {"tools": [], "mcps": ["server"], "skills": []},
        },
    }
    mcp_policy = {}
    assert not tool_allowed(context, "gated", {}, mcp_policy)
    assert tool_allowed(context, "local", {}, mcp_policy)
    mcp_policy = {"server": set()}
    assert tool_allowed(context, "gated", {}, mcp_policy)
