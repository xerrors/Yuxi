from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.context import BaseContext, ResourceSelection
from yuxi.services import agent_config_service

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_prepare_agent_config_write_resolves_only_submitted_resource_fields(monkeypatch):
    """保存补丁为每个提交资源返回对应的真实权限集合。"""
    resource_fields = {"tools", "knowledges", "mcps", "skills", "subagents"}
    resolver = AsyncMock(
        return_value={
            field_name: [{"key": f"visible-{field_name}"}, {"key": f"also-visible-{field_name}"}]
            for field_name in resource_fields
        }
    )

    monkeypatch.setattr(agent_config_service, "resolve_agent_resource_options", resolver)
    db = object()
    user = SimpleNamespace(role="user")

    config, resource_access = await agent_config_service.prepare_agent_config_write(
        {
            "context": {
                "title": "renamed",
                "tools": ["visible-tools"],
                "knowledges": ["visible-knowledges"],
                "mcps": ["visible-mcps"],
                "skills": ["visible-skills"],
                "subagents": ["visible-subagents"],
                "preload_skills": ["visible-skills"],
            }
        },
        context_schema=ConfigContext,
        db=db,
        user=user,
    )

    assert config["context"]["title"] == "renamed"
    assert resource_access == {
        "tools": {"visible-tools", "also-visible-tools"},
        "knowledges": {"visible-knowledges", "also-visible-knowledges"},
        "mcps": {"visible-mcps", "also-visible-mcps"},
        "skills": {"visible-skills", "also-visible-skills"},
        "subagents": {"visible-subagents", "also-visible-subagents"},
        "preload_skills": {"visible-skills", "also-visible-skills"},
    }
    resolver.assert_awaited_once_with(resource_fields, db=db, user=user)


@pytest.mark.asyncio
async def test_prepare_agent_config_write_does_not_load_resources_for_unrelated_patch(monkeypatch):
    """无关配置修改不依赖资源列表的可用性。"""
    monkeypatch.setattr(
        agent_config_service,
        "resolve_agent_resource_options",
        AsyncMock(side_effect=AssertionError("resource options should not be loaded")),
    )

    config, resource_access = await agent_config_service.prepare_agent_config_write(
        {"context": {"title": "renamed"}},
        context_schema=ConfigContext,
        db=object(),
        user=SimpleNamespace(role="user"),
    )

    assert config == {"context": {"title": "renamed"}}
    assert resource_access == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("subagents", [[], ["undeclared-subagent"]])
@pytest.mark.parametrize("selection", [[], "all"])
async def test_prepare_agent_config_write_does_not_load_resources_for_strategy_switch(
    monkeypatch, subagents, selection
):
    """显式策略切换保留原值，未声明的字段在资源解析前被移除。"""
    monkeypatch.setattr(
        agent_config_service,
        "resolve_agent_resource_options",
        AsyncMock(side_effect=AssertionError("resource options should not be loaded")),
    )

    config, resource_access = await agent_config_service.prepare_agent_config_write(
        {"context": {"skills": selection, "mcps": selection, "preload_skills": selection, "subagents": subagents}},
        context_schema=None,
        db=object(),
        user=SimpleNamespace(role="user"),
    )

    assert config == {"context": {"skills": selection, "mcps": selection, "preload_skills": selection}}
    assert resource_access == {}


@dataclass(kw_only=True)
class ConfigContext(BaseContext):
    """覆盖保存资源字段的最小测试 Schema。"""

    subagents: ResourceSelection = "all"
    title: str = ""
