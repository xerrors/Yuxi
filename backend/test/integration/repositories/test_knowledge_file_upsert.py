"""真实 PostgreSQL 中的文件 ID 碰撞与同库写入回归。"""

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本测试只使用独立 Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """资源由局部 fixture 清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本测试不创建沙盒。"""
    yield


@pytest.fixture
async def file_store(monkeypatch):
    """使用独立表和真实事务，避免触及运行实例的文件记录。"""
    schema = f"pytest_file_upsert_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    manager._initialized = True
    monkeypatch.setattr(repository_module, "pg_manager", manager)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeBase.__table__.create)
            await connection.run_sync(KnowledgeFile.__table__.create)
        async with manager.get_async_session_context() as session:
            session.add_all(
                [
                    KnowledgeBase(kb_id="owner-kb", name="Owner", kb_type="milvus"),
                    KnowledgeBase(kb_id="editor-kb", name="Editor", kb_type="milvus"),
                ]
            )
        yield manager
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def _read_file_row(manager: PostgresManager, file_id: str) -> dict:
    """通过新事务回读整行，包含更新时刻等隐式字段。"""
    async with manager.get_async_session_context() as session:
        result = await session.execute(select(KnowledgeFile.__table__).where(KnowledgeFile.file_id == file_id))
        return dict(result.mappings().one())


async def test_upsert_rejects_cross_kb_collision_and_preserves_foreign_row(file_store):
    """跨库碰撞必须报错，其他库的整行保持原值。"""
    repository = KnowledgeFileRepository()
    file_id = "file_deadbe"
    await repository.upsert(
        file_id,
        {
            "kb_id": "owner-kb",
            "filename": "owner.md",
            "path": "owner/original.md",
            "status": "indexed",
            "content_hash": "original-hash",
            "processing_params": {"keep": True},
            "created_by": "owner",
        },
    )
    before = await _read_file_row(file_store, file_id)

    with pytest.raises(ValueError, match="another knowledge base"):
        await repository.upsert(
            file_id,
            {
                "kb_id": "editor-kb",
                "filename": "replacement.md",
                "path": "editor/replacement.md",
                "status": "uploaded",
                "content_hash": "replacement-hash",
                "processing_params": {"replace": True},
                "created_by": "editor",
            },
        )

    assert await _read_file_row(file_store, file_id) == before


async def test_upsert_updates_existing_file_in_same_kb(file_store):
    """同库文件继续允许更新，创建信息和主键保持原值。"""
    repository = KnowledgeFileRepository()
    file_id = "file_deadbe"
    await repository.upsert(file_id, {"kb_id": "editor-kb", "filename": "original.md", "created_by": "creator"})
    before = await _read_file_row(file_store, file_id)

    await repository.upsert(
        file_id,
        {"kb_id": "editor-kb", "filename": "updated.md", "status": "parsed", "updated_by": "editor"},
    )

    after = await _read_file_row(file_store, file_id)
    assert after["id"] == before["id"]
    assert after["kb_id"] == "editor-kb"
    assert after["filename"] == "updated.md"
    assert after["status"] == "parsed"
    assert after["created_by"] == "creator"
    assert after["created_at"] == before["created_at"]
    assert after["updated_by"] == "editor"
