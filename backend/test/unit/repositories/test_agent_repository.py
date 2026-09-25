from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.presets.subagents.general_purpose import PRESET as GENERAL_PURPOSE
from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEFAULT_AGENT_DESCRIPTION,
    DEFAULT_SHARE_CONFIG,
    SUB_AGENT_BACKEND_ID,
    merge_agent_config_json,
    user_can_access_agent,
    user_can_manage_agent,
)
from yuxi.storage.postgres.models_business import Agent, User


class FakeDb:
    def __init__(self):
        self.added = None
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, item):
        self.added = item


_MANAGER_USER_SCOPE = {
    "version": 2,
    "read_scope": {"access_level": "user", "user_uids": ["manager"]},
    "manage_scope": {"access_level": "user", "user_uids": ["manager"]},
}


def _agent_for_update(*, slug="shared-bot", name="Shared Bot", created_by="owner", share_config=_MANAGER_USER_SCOPE):
    return SimpleNamespace(
        slug=slug,
        backend_id="ChatbotAgent",
        share_config=share_config,
        created_by=created_by,
        updated_by=None,
        updated_at=None,
        name=name,
        description="",
        icon=None,
        pics=[],
        config_json={},
    )


def test_merge_agent_config_json_preserves_omitted_context_fields_and_hidden_skills():
    """省略字段和不可见 Skill 引用均保持原值。"""
    existing = {
        "context": {
            "model": "provider:model-a",
            "skills": [f"skill-{index}" for index in range(10)],
        },
        "metadata": {"source": "owner"},
    }

    merged = merge_agent_config_json(
        existing,
        {
            "context": {
                "temperature": 0.2,
                "skills": [f"skill-{index}" for index in range(5)],
            }
        },
        resource_access={
            "skills": {
                *(f"skill-{index}" for index in range(5)),
                "visible-extra-a",
                "visible-extra-b",
                "visible-extra-c",
            }
        },
    )

    assert merged == {
        "context": {
            "model": "provider:model-a",
            "temperature": 0.2,
            "skills": [f"skill-{index}" for index in range(10)],
        },
        "metadata": {"source": "owner"},
    }
    assert existing["context"]["skills"] == [f"skill-{index}" for index in range(10)]


def test_merge_agent_config_json_applies_visible_edits_and_preserves_hidden_references():
    """可见增删保留交错引用顺序，新选择追加到末尾。"""
    merged = merge_agent_config_json(
        {"context": {"skills": ["visible-a", "hidden-a", "visible-b", "hidden-b"]}},
        {"context": {"skills": ["visible-b", "visible-c", "hidden-a"]}},
        resource_access={"skills": {"visible-a", "visible-b", "visible-c"}},
    )

    assert merged["context"]["skills"] == ["hidden-a", "visible-b", "hidden-b", "visible-c"]


@pytest.mark.parametrize("strategy", [None, []])
def test_merge_agent_config_json_replaces_resource_list_for_explicit_strategy_switch(strategy):
    """显式空列表或 null 整体切换资源策略。"""
    merged = merge_agent_config_json(
        {"context": {"skills": ["visible", "hidden"], "subagents": ["visible-subagent", "hidden-subagent"]}},
        {"context": {"skills": strategy, "subagents": strategy}},
        resource_access={"skills": {"visible"}, "subagents": {"visible-subagent"}},
    )

    assert merged["context"]["skills"] == strategy
    assert merged["context"]["subagents"] == strategy


def test_merge_agent_config_json_rejects_new_unauthorized_resource_reference():
    """新增无权引用必须拒绝，不能借既有隐藏引用绕过。"""
    with pytest.raises(ValueError, match="无权新增.*skills.*hidden-new"):
        merge_agent_config_json(
            {"context": {"skills": ["visible", "hidden-existing"]}},
            {"context": {"skills": ["visible", "hidden-existing", "hidden-new"]}},
            resource_access={"skills": {"visible"}},
        )


def test_merge_agent_config_json_requires_authorization_result_for_resource_write():
    """缺少权限解析结果的直接资源写入必须拒绝。"""
    with pytest.raises(ValueError, match="skills 未经过权限校验"):
        merge_agent_config_json(
            {"context": {"skills": []}},
            {"context": {"skills": ["new-skill"]}},
            resource_access={},
        )


def test_merge_agent_config_json_validates_preload_skills_independently():
    """预加载选择独立保留不可见引用，不修改 Skill 允许列表。"""
    merged = merge_agent_config_json(
        {
            "context": {
                "skills": ["visible", "hidden"],
                "preload_skills": ["visible", "hidden"],
            }
        },
        {"context": {"preload_skills": ["visible"]}},
        resource_access={"preload_skills": {"visible"}},
    )

    assert merged["context"]["skills"] == ["visible", "hidden"]
    assert merged["context"]["preload_skills"] == ["visible", "hidden"]


@pytest.mark.asyncio
async def test_ensure_default_agent_creates_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_default_agent()

    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.config_json == {"context": {}}
    assert db.added is agent
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_default_agent_backfills_missing_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        share_config=DEFAULT_SHARE_CONFIG.copy(),
        is_default=True,
        description=None,
        updated_by=None,
        updated_at=None,
    )

    async def get_by_slug(_slug):
        return agent

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    result = await repo.ensure_default_agent(created_by="admin")

    assert result is agent
    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.updated_by == "admin"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_preset_creates_empty_config_subagent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_preset(GENERAL_PURPOSE, created_by="system")

    assert agent.slug == GENERAL_PURPOSE.slug
    assert agent.name == GENERAL_PURPOSE.name
    assert agent.description == GENERAL_PURPOSE.description
    assert agent.backend_id == SUB_AGENT_BACKEND_ID
    assert agent.is_subagent is True
    assert agent.is_default is False
    assert agent.config_json == {"context": {}}
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.created_by == "system"
    assert db.added is agent
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_preset_is_idempotent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    existing = SimpleNamespace(slug=GENERAL_PURPOSE.slug, config_json={"context": {"model": "custom:model"}})

    async def get_by_slug(_slug):
        return existing

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_preset(GENERAL_PURPOSE)

    assert agent is existing
    assert db.added is None
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_defaults_to_creator_read_scope_without_manage_scope(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def fake_unique_slug(_slug, _name):
        return "personal-bot"

    monkeypatch.setattr(repo, "_unique_slug", fake_unique_slug)

    agent = await repo.create(
        name="Personal Bot",
        backend_id="ChatbotAgent",
        slug="personal-bot",
        created_by="user",
    )

    assert agent.share_config == {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["user"]},
        "manage_scope": None,
    }
    assert db.added is agent


@pytest.mark.asyncio
async def test_create_agent_allows_same_explicit_share_scope_for_normal_user(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def fake_unique_slug(_slug, _name):
        return "personal-bot"

    monkeypatch.setattr(repo, "_unique_slug", fake_unique_slug)
    agent = await repo.create(
        name="Personal Bot",
        backend_id="ChatbotAgent",
        slug="personal-bot",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        },
        created_by="user",
    )

    assert agent.share_config == DEFAULT_SHARE_CONFIG


def test_user_shared_agent_is_manageable_for_normal_user():
    user = User(username="user", uid="user", password_hash="x", role="user", department_id=1)
    agent = Agent(
        slug="shared-bot",
        name="Shared Bot",
        backend_id="ChatbotAgent",
        created_by="other",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["user"]},
            "manage_scope": {"access_level": "user", "department_ids": [], "user_uids": ["user"]},
        },
    )

    assert user_can_access_agent(user, agent) is True
    assert user_can_manage_agent(user, agent) is True


@pytest.mark.asyncio
async def test_delegated_manager_update_preserves_shared_agent_acl():
    db = FakeDb()
    repo = AgentRepository(db)
    agent = _agent_for_update()
    await repo.update(
        agent,
        share_config=_MANAGER_USER_SCOPE,
        updated_by="manager",
    )

    assert agent.share_config["read_scope"]["user_uids"] == ["manager"]
    assert agent.share_config["manage_scope"]["user_uids"] == ["manager"]


@pytest.mark.asyncio
async def test_delegated_manager_can_update_agent_acl_with_standard_validation():
    db = FakeDb()
    repo = AgentRepository(db)
    agent = _agent_for_update()
    await repo.update(
        agent,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        },
        updated_by="manager",
    )

    assert agent.share_config == DEFAULT_SHARE_CONFIG
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_user_can_update_read_scope_without_granting_manage_scope():
    db = FakeDb()
    repo = AgentRepository(db)
    agent = _agent_for_update(slug="personal-bot", name="Personal Bot", created_by="manager")
    await repo.update(
        agent,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["manager", "reader"]},
            "manage_scope": None,
        },
        updated_by="manager",
    )

    assert agent.share_config == {
        "version": 2,
        "read_scope": {
            "access_level": "user",
            "department_ids": [],
            "user_uids": ["manager", "reader"],
        },
        "manage_scope": None,
    }


@pytest.mark.asyncio
async def test_normal_user_can_update_agent_with_equivalent_v2_share_config():
    db = FakeDb()
    repo = AgentRepository(db)
    agent = _agent_for_update(name="Legacy Bot", created_by="manager")
    await repo.update(
        agent,
        name="Renamed Bot",
        share_config=_MANAGER_USER_SCOPE,
        updated_by="manager",
    )

    assert agent.name == "Renamed Bot"
    assert agent.share_config["read_scope"] == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["manager"],
    }
    assert agent.share_config["manage_scope"] == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["manager"],
    }
