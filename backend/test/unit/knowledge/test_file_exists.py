from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, *_args):
        if exc_type is None:
            await self.db.commit()
        else:
            await self.db.rollback()
        return False


@pytest_asyncio.fixture
async def knowledge_session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(KnowledgeBase.__table__.create)
        await conn.run_sync(KnowledgeFile.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        monkeypatch.setattr(
            repository_module.pg_manager,
            "get_async_session_context",
            lambda: _AsyncSessionContext(session),
        )
        yield session

    await engine.dispose()


async def test_exists_by_filename_matches_active_file_exactly(knowledge_session):
    existing_name = (
        "google_drive/shared_drives/engineering/serving-runtime/dsid_e4ff04ebc2a14c1982abc4987753790c__playbook.txt"
    )
    knowledge_session.add_all(
        [
            KnowledgeBase(kb_id="kb_1", name="KB 1", description="", kb_type="milvus"),
            KnowledgeBase(kb_id="kb_2", name="KB 2", description="", kb_type="milvus"),
            KnowledgeFile(
                file_id="file_active",
                kb_id="kb_1",
                filename=existing_name,
                status="uploaded",
                is_folder=False,
            ),
            KnowledgeFile(
                file_id="file_other_kb",
                kb_id="kb_2",
                filename=existing_name,
                status="uploaded",
                is_folder=False,
            ),
            KnowledgeFile(
                file_id="file_failed",
                kb_id="kb_1",
                filename="failed.txt",
                status="failed",
                is_folder=False,
            ),
            KnowledgeFile(
                file_id="folder_same_name",
                kb_id="kb_1",
                filename="folder",
                status="done",
                is_folder=True,
            ),
            KnowledgeFile(
                file_id="legacy_file",
                kb_id="kb_1",
                filename="legacy.txt",
                status="indexed",
                is_folder=None,
            ),
        ]
    )
    await knowledge_session.commit()

    repo = KnowledgeFileRepository()

    assert await repo.exists_by_filename(kb_id="kb_1", filename=existing_name) is True
    assert await repo.exists_by_filename(kb_id="kb_1", filename=existing_name.upper()) is False
    assert await repo.exists_by_filename(kb_id="kb_1", filename="failed.txt") is False
    assert await repo.exists_by_filename(kb_id="kb_1", filename="folder") is False
    assert await repo.exists_by_filename(kb_id="kb_1", filename="legacy.txt") is True
    assert await repo.exists_by_filename(kb_id="missing", filename=existing_name) is False
