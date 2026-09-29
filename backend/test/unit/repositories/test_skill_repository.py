from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.repositories.skill_repository import SkillRepository


@pytest.mark.asyncio
async def test_skill_repository_flushes_without_committing() -> None:
    db = Mock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()

    item = await SkillRepository(db).create(
        slug="demo",
        name="Demo",
        description="demo",
        source_type="upload",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        dir_path="shared/demo",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by="user-1",
    )

    db.add.assert_called_once_with(item)
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(item)
    db.commit.assert_not_awaited()
