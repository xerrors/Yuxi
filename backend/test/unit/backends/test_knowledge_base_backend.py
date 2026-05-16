from __future__ import annotations

from types import SimpleNamespace

import pytest
from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_for_context_filters_by_enabled_names(monkeypatch) -> None:
    async def _fake_get_databases_by_raw_id(user_id: int) -> dict:
        assert user_id == 7
        return {"databases": [{"db_id": "db-1", "name": "Alpha"}, {"db_id": "db-2", "name": "Beta"}]}

    monkeypatch.setattr(
        "yuxi.agents.backends.knowledge_base_backend.knowledge_base.get_databases_by_raw_id",
        _fake_get_databases_by_raw_id,
    )

    context = SimpleNamespace(user_id="7", knowledges=["Beta"])
    visible = await resolve_visible_knowledge_bases_for_context(context)

    assert visible == [{"db_id": "db-2", "name": "Beta"}]
    assert getattr(context, "_visible_knowledge_bases") == visible


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_for_context_defaults_to_all_accessible(monkeypatch) -> None:
    databases = [{"db_id": "db-1", "name": "Alpha"}, {"db_id": "db-2", "name": "Beta"}]

    async def _fake_get_databases_by_raw_id(user_id: int) -> dict:
        assert user_id == 7
        return {"databases": databases}

    monkeypatch.setattr(
        "yuxi.agents.backends.knowledge_base_backend.knowledge_base.get_databases_by_raw_id",
        _fake_get_databases_by_raw_id,
    )

    context = SimpleNamespace(user_id="7", knowledges=None)
    visible = await resolve_visible_knowledge_bases_for_context(context)

    assert visible == databases
    assert getattr(context, "_visible_knowledge_bases") == databases


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_for_context_handles_missing_user() -> None:
    context = SimpleNamespace(user_id="", knowledges=None)

    visible = await resolve_visible_knowledge_bases_for_context(context)

    assert visible == []
    assert getattr(context, "_visible_knowledge_bases") == []


@pytest.mark.asyncio
async def test_resolve_visible_knowledge_bases_for_context_handles_invalid_user() -> None:
    context = SimpleNamespace(user_id="not-a-number", knowledges=None)

    visible = await resolve_visible_knowledge_bases_for_context(context)

    assert visible == []
    assert getattr(context, "_visible_knowledge_bases") == []
