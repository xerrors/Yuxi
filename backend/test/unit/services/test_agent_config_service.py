from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.agents.runtime.context import BaseContext
import yuxi.modules.agents.services.configuration as agent_config_service

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_prepare_agent_config_write_resolves_only_submitted_resource_fields(monkeypatch):
    """保存补丁为每个提交资源返回对应的真实权限集合。"""
    resource_fields = {"tools", "knowledges", "mcps", "skills"}
    resolver = AsyncMock(
        return_value={
            field_name: [{"key": f"visible-{field_name}"}, {"key": f"also-visible-{field_name}"}] for field_name in resource_fields
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
        "preload_skills": {"visible-skills", "also-visible-skills"},
    }
    resolver.assert_awaited_once_with(resource_fields, db=db, user=user, agent_id=None)


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
async def test_prepare_agent_config_write_does_not_load_resources_for_strategy_switch(monkeypatch, subagents, selection):
    """替代配置入口明确拒绝未声明字段。"""
    monkeypatch.setattr(
        agent_config_service,
        "resolve_agent_resource_options",
        AsyncMock(side_effect=AssertionError("resource options should not be loaded")),
    )

    with pytest.raises(ValueError, match="不可写或未声明"):
        await agent_config_service.prepare_agent_config_write(
            {"context": {"skills": selection, "mcps": selection, "preload_skills": selection, "subagents": subagents}},
            context_schema=None,
            db=object(),
            user=SimpleNamespace(role="user"),
        )


@dataclass(kw_only=True)
class ConfigContext(BaseContext):
    """覆盖保存资源字段的最小测试 Schema。"""

    title: str = ""


@pytest.mark.parametrize("selection", ["all", ["missing-deployed-plugin"], 1])
@pytest.mark.asyncio
async def test_plugin_config_rejects_invalid_selection_at_write_boundary(selection):
    """插件配置只能引用当前部署中明确选择的插件。"""
    with pytest.raises(ValueError):
        await agent_config_service.prepare_agent_config_write(
            {"context": {"plugins": selection}},
            context_schema=ConfigContext,
            db=object(),
            user=SimpleNamespace(role="user"),
        )


def test_plugin_config_default_and_schema_are_explicit():
    """新增部署插件不会自动进入既有智能体。"""
    context = ConfigContext()
    assert context.plugins == []
    item = ConfigContext.get_configurable_items()["plugins"]
    assert item["supports_all"] is False
    assert item["default"] == []
    merged = agent_config_service.merge_agent_config_json(
        {},
        {"context": {"plugins": ["knowledge-base"]}},
        resource_access={"plugins": {"knowledge-base"}},
        context_schema=ConfigContext,
    )
    assert merged["context"]["plugins"] == ["knowledge-base"]


@pytest.mark.parametrize("field", ["skills", "preload_skills"])
def test_plugin_entry_cannot_be_selected_as_independent_skill(field):
    """插件入口必须通过插件配置获得，普通 Skill 路径不能绕过。"""
    with pytest.raises(ValueError, match="插件入口"):
        agent_config_service.merge_agent_config_json(
            {},
            {"context": {field: ["knowledge-base"]}},
            resource_access={field: {"knowledge-base"}},
            context_schema=ConfigContext,
        )


@pytest.mark.asyncio
async def test_skill_options_exclude_plugin_entries(monkeypatch):
    """入口仍可管理，但不能由普通 Skill 选择器独立启用。"""
    from yuxi.modules.agents.runtime.context import resolve_agent_resource_options
    from yuxi.modules.extensions.skills.repository import SkillRepository

    monkeypatch.setattr(
        SkillRepository,
        "list_enabled_readable",
        AsyncMock(
            return_value=[
                SimpleNamespace(slug="knowledge-base", name="知识库", description="入口"),
                SimpleNamespace(slug="ordinary-guide", name="普通指南", description="内容"),
            ]
        ),
    )
    monkeypatch.setattr(
        SkillRepository,
        "get_by_slug",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                bound_agent_id=None,
                source_type="builtin",
                created_by=None,
                share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
            )
        ),
    )
    options = await resolve_agent_resource_options(
        {"skills", "plugins"}, db=object(), user=SimpleNamespace(uid="reader", role="user", department_id=None)
    )
    assert [item["key"] for item in options["skills"]] == ["ordinary-guide"]
    assert "knowledge-base" in {item["key"] for item in options["plugins"]}
