"""配置选项与依赖编辑采用显示覆盖，但提交标识保持原值。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yuxi.agents.context import resolve_agent_resource_options
from yuxi.agents.skills import service as skill_service
from yuxi.agents.mcp import service as mcp_service
from yuxi.agents.toolkits import service as tool_service
from yuxi.repositories import tool_display_repository
from yuxi.services.resource_display_service import MCP_NAMES, SKILL_NAMES


@pytest.mark.asyncio
async def test_runtime_options_apply_names_after_visibility_filter(monkeypatch):
    """内置显示覆盖不添加隐藏资源，不改变自建名称或调用身份。"""
    metadata = [{"slug": "stable-tool", "name": "Original tool", "description": "tool desc"}]
    servers = [
        SimpleNamespace(slug="builtin-server", name="Original server", description="server desc"),
        SimpleNamespace(slug="custom-server", name="Custom server", description="custom desc"),
        SimpleNamespace(slug="disabled-server", name="Disabled", description="hidden"),
    ]
    skills = [
        SimpleNamespace(slug="builtin-skill", name="Original skill", description="skill desc", source_type="builtin"),
        SimpleNamespace(slug="custom-skill", name="Personal name", description="custom desc", source_type="upload"),
    ]
    names = {
        tool_display_repository.KEY: {"stable-tool": "中文工具", "missing-tool": "不得添加"},
        MCP_NAMES: {"builtin-server": "中文服务", "custom-server": "不得覆盖", "disabled-server": "不得添加"},
        SKILL_NAMES: {"builtin-skill": "中文技能", "custom-skill": "不得覆盖", "missing-skill": "不得添加"},
    }
    monkeypatch.setattr(
        tool_display_repository, "read_names", AsyncMock(side_effect=lambda key=tool_display_repository.KEY: names[key])
    )
    monkeypatch.setattr(tool_service, "get_tool_metadata", lambda category=None: metadata)
    monkeypatch.setattr(mcp_service, "get_all_mcp_servers", AsyncMock(return_value=servers))
    monkeypatch.setattr(
        mcp_service, "get_enabled_mcp_server_slugs", AsyncMock(return_value=["builtin-server", "custom-server"])
    )
    monkeypatch.setattr(mcp_service, "is_builtin_mcp_server", lambda server: server.slug == "builtin-server")
    monkeypatch.setattr(skill_service, "list_accessible_skills", AsyncMock(return_value=skills))
    result = await resolve_agent_resource_options({"tools", "mcps", "skills"}, db=object(), user=object())
    assert [(row["key"], row["name"]) for row in result["tools"]] == [("stable-tool", "中文工具")]
    assert [(row["key"], row["name"]) for row in result["mcps"]] == [
        ("builtin-server", "中文服务"),
        ("custom-server", "Custom server"),
    ]
    assert [(row["key"], row["name"]) for row in result["skills"]] == [
        ("builtin-skill", "中文技能"),
        ("custom-skill", "Personal name"),
    ]
    assert metadata[0]["name"] == "Original tool"
    assert servers[0].name == "Original server"
    assert skills[0].name == "Original skill"


@pytest.mark.asyncio
async def test_skill_dependency_tool_names_preserve_dependency_ids(monkeypatch):
    """依赖选项使用中文工具名，MCP和技能的已有字符串契约保持稳定。"""
    monkeypatch.setattr(tool_service, "get_tool_metadata", lambda: [{"slug": "stable-tool", "name": "Original"}])
    monkeypatch.setattr(tool_display_repository, "read_names", AsyncMock(return_value={"stable-tool": "中文工具"}))
    monkeypatch.setattr(skill_service, "list_skill_slugs", AsyncMock(return_value=["self", "dependency"]))
    monkeypatch.setattr(skill_service, "get_enabled_mcp_server_slugs", AsyncMock(return_value=["server-id"]))
    result = await skill_service.get_skill_dependency_options(object(), object(), slug="self")
    assert result == {
        "tools": [{"slug": "stable-tool", "name": "中文工具"}],
        "mcps": ["server-id"],
        "skills": ["dependency"],
    }
