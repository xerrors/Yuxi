from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from yuxi.repositories.agent_config_repository import AgentConfigRepository, _merge_skill_slugs


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self, config):
        self.config = config
        self.statements = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeScalarResult(self.config)


def test_merge_skill_slugs_preserves_order_and_dedupes():
    assert _merge_skill_slugs(["old", "new", "old", "", None], ["new", "third"]) == [
        "old",
        "new",
        "third",
    ]


@pytest.mark.asyncio
async def test_get_by_id_for_update_uses_row_lock():
    config = SimpleNamespace(config_json={}, updated_at=None)
    db = FakeDb(config)
    repo = AgentConfigRepository(db)

    result = await repo._get_by_id_for_update(1)

    assert result is config
    compiled_sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled_sql


@pytest.mark.asyncio
async def test_add_skills_to_config_json_writes_context_skills_without_duplicates():
    config = SimpleNamespace(config_json={"context": {"skills": ["existing", "new"]}}, updated_at=None)
    db = FakeDb(config)
    repo = AgentConfigRepository(db)

    result = await repo.add_skills_to_config_json(agent_config_id=1, new_slugs=["new", "extra"])

    assert result is True
    assert config.config_json == {"context": {"skills": ["existing", "new", "extra"]}}
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(config)


@pytest.mark.asyncio
async def test_add_skills_to_config_json_creates_missing_context():
    config = SimpleNamespace(config_json={}, updated_at=None)
    db = FakeDb(config)
    repo = AgentConfigRepository(db)

    result = await repo.add_skills_to_config_json(agent_config_id=1, new_slugs=["demo-skill"])

    assert result is True
    assert config.config_json == {"context": {"skills": ["demo-skill"]}}


@pytest.mark.asyncio
async def test_add_skills_to_config_json_returns_false_when_missing_config():
    db = FakeDb(None)
    repo = AgentConfigRepository(db)

    result = await repo.add_skills_to_config_json(agent_config_id=404, new_slugs=["demo-skill"])

    assert result is False
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()
