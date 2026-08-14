from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEFAULT_AGENT_DESCRIPTION,
    DEFAULT_SHARE_CONFIG,
    GENERAL_PURPOSE_AGENT_DESCRIPTION,
    GENERAL_PURPOSE_AGENT_NAME,
    GENERAL_PURPOSE_AGENT_SLUG,
    SUB_AGENT_BACKEND_ID,
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
async def test_ensure_general_purpose_subagent_creates_empty_config_subagent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_general_purpose_subagent(created_by="system")

    assert agent.slug == GENERAL_PURPOSE_AGENT_SLUG
    assert agent.name == GENERAL_PURPOSE_AGENT_NAME
    assert agent.description == GENERAL_PURPOSE_AGENT_DESCRIPTION
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
async def test_ensure_general_purpose_subagent_is_idempotent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    existing = SimpleNamespace(slug=GENERAL_PURPOSE_AGENT_SLUG, config_json={"context": {"model": "custom:model"}})

    async def get_by_slug(_slug):
        return existing

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_general_purpose_subagent()

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
