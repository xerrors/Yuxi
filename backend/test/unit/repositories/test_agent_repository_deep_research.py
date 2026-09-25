from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.presets import discover_agent_presets
from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEFAULT_AGENT_BACKEND_ID,
    SUB_AGENT_BACKEND_ID,
)


class CollectingDb:
    def __init__(self):
        self.added: list = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, item):
        self.added.append(item)


@pytest.mark.asyncio
async def test_discovered_presets_creates_orchestrator_and_subagents(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    for preset in discover_agent_presets():
        if preset.slug != "default-chatbot":
            await repo.ensure_preset(preset)

    created = {agent.slug: agent for agent in db.added}
    assert set(created) == {
        "general-purpose",
        "web-search",
        "deep-research",
        "research-explorer",
        "fact-verifier",
    }

    explorer = created["research-explorer"]
    verifier = created["fact-verifier"]
    assert explorer.backend_id == SUB_AGENT_BACKEND_ID and explorer.is_subagent is True
    assert verifier.backend_id == SUB_AGENT_BACKEND_ID and verifier.is_subagent is True

    orchestrator = created["deep-research"]
    assert orchestrator.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert orchestrator.is_subagent is False
    assert orchestrator.is_default is False
    context = orchestrator.config_json["context"]
    assert context["subagents"] == ["research-explorer", "fact-verifier"]
    assert context["skills"] == ["deep-research"]
    assert context["system_prompt"].strip()


@pytest.mark.asyncio
async def test_discovered_presets_is_idempotent(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(slug):
        return SimpleNamespace(slug=slug)

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    for preset in discover_agent_presets():
        if preset.slug != "default-chatbot":
            await repo.ensure_preset(preset)

    assert db.added == []
    db.commit.assert_not_awaited()
