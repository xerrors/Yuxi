"""真实 PostgreSQL 下的检索来源和分片数回归。"""

import os
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.repositories import knowledge_file_repository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件仅使用独立 PostgreSQL Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """局部 fixture 负责清理测试数据。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本文件不创建沙盒。"""
    yield


@pytest.fixture
async def chunk_source_store(monkeypatch):
    """用真实数据库会话执行 repository 查询。"""
    schema = f"pytest_chunk_sources_{uuid.uuid4().hex}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        """提交或回滚隔离数据库事务。"""
        async with sessions.begin() as session:
            yield session

    monkeypatch.setattr(knowledge_file_repository.pg_manager, "get_async_session_context", session_context)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeBase.__table__.create)
            await connection.run_sync(KnowledgeFile.__table__.create)
        async with sessions.begin() as session:
            session.add_all(
                [
                    KnowledgeBase(kb_id="target", name="target", kb_type="milvus"),
                    KnowledgeBase(kb_id="other", name="other", kb_type="milvus"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    KnowledgeFile(file_id="live", kb_id="target", filename="live.md", chunk_count=4),
                    KnowledgeFile(file_id="foreign", kb_id="other", filename="foreign.md", chunk_count=9),
                ]
            )
        yield sessions
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("count", [None, 0, 1, 4])
async def test_chunk_sources_preserve_count_and_kb_scope(chunk_source_store, count):
    """分片数来自实际行，外库文件和 PG 中不存在的文件不能进入结果。"""
    async with chunk_source_store.begin() as session:
        await session.execute(
            text("UPDATE knowledge_files SET chunk_count = :count WHERE file_id = 'live'"), {"count": count}
        )
    repository = KnowledgeFileRepository()
    assert await repository.get_chunk_sources_by_file_ids(kb_id="target", file_ids=[]) == {}
    assert await repository.get_chunk_sources_by_file_ids(kb_id="target", file_ids=["live", "foreign", "deleted"]) == {
        "live": {"source": "live.md", "chunk_count": count or 0}
    }

    chunks = [
        {"metadata": {"file_id": file_id, "chunk_index": 0}, "content": file_id, "score": 0.9}
        for file_id in ["live", "foreign", "deleted"]
    ]
    result = await MilvusKB.__new__(MilvusKB)._hydrate_chunk_sources("target", chunks)
    assert result == [
        {
            "metadata": {"file_id": "live", "chunk_index": 0, "source": "live.md", "chunk_count": count or 0},
            "content": "live",
            "score": 0.9,
        }
    ]
