"""真实 PostgreSQL 和 Redis 下的统计投影回归。"""

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.api.routers.knowledge.management as knowledge_router
import yuxi.modules.knowledge.repositories.bases as knowledge_base_repository
import yuxi.modules.knowledge.repositories.files as knowledge_file_repository
from yuxi.infrastructure.redis import close_async_redis_client, get_async_redis_client
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeChunk, KnowledgeFile
from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services import access as knowledge_access
from yuxi.modules.knowledge.services import bases as knowledge_bases
from yuxi.modules.knowledge.services import documents as knowledge_documents
from yuxi.modules.knowledge.services import statistics

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def stats_store(monkeypatch):
    """创建隔离表和唯一缓存键。"""
    schema = f"pytest_stats_{uuid.uuid4().hex}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        """提交或回滚一个独立事务。"""
        async with sessions.begin() as session:
            yield session

    monkeypatch.setattr(knowledge_base_repository.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(knowledge_file_repository.pg_manager, "get_async_session_context", session_context)
    redis = await get_async_redis_client()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeBase.__table__.create)
            await connection.run_sync(KnowledgeFile.__table__.create)
            await connection.run_sync(KnowledgeChunk.__table__.create)
        async with sessions.begin() as session:
            session.add(KnowledgeBase(kb_id=schema, name="stats regression", kb_type="milvus", additional_params={"keep": True}))
            session.add(
                KnowledgeFile(
                    file_id=schema,
                    kb_id=schema,
                    filename="test.md",
                    is_folder=False,
                    status="parsed",
                    chunk_count=0,
                    token_count=0,
                )
            )
        yield schema, sessions, redis
    finally:
        await redis.delete(f"yuxi:kb_file_stats:{schema}", f"yuxi:knowledge_base:{schema}:lock")
        await close_async_redis_client()
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("fails", [False, True])
async def test_operation_refresh_ignores_cached_stats(stats_store, tmp_path, fails):
    """成功与异常收尾都必须写回真实结果，统计读取返回同一最新事实。"""
    kb_id, sessions, redis = stats_store
    cached = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    assert cached["pending_index_count"] == 1
    # 延长本测试缓存存活期，消除慢 CI 下自然过期导致的假通过。
    await redis.expire(f"yuxi:kb_file_stats:{kb_id}", 300)

    async def operation():
        """模拟已提交的文件索引结果。"""
        await KnowledgeFileRepository().update_fields(
            file_id=kb_id,
            kb_id=kb_id,
            data={"status": "indexed", "chunk_count": 158, "token_count": 900},
        )
        if fails:
            raise ValueError("operation failed")
        return "indexed"

    if fails:
        with pytest.raises(ValueError, match="operation failed"):
            await statistics.run_with_stats_refresh(kb_id, operation())
    else:
        assert await statistics.run_with_stats_refresh(kb_id, operation()) == "indexed"
    async with sessions() as session:
        row = (await session.execute(select(KnowledgeBase))).scalar_one()
        assert row.additional_params["keep"] is True
        assert row.additional_params["stats"]["chunk_count"] == 158
        assert row.additional_params["stats"]["token_count"] == 900
        assert row.additional_params["stats"]["pending_index_count"] == 0
    summaries = await knowledge_bases.get_knowledge_bases()
    assert len(summaries) == 1
    assert summaries[0].chunk_count == 158
    assert summaries[0].pending_index_count == 0
    fresh = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    assert fresh["chunk_count"] == row.additional_params["stats"]["chunk_count"]
    assert fresh["pending_index_count"] == 0


async def test_refresh_queries_after_acquiring_row_lock(stats_store):
    """等待行锁期间的文件提交必须进入最终聚合。"""
    kb_id, sessions, _ = stats_store
    async with sessions.begin() as blocker:
        await blocker.execute(select(KnowledgeBase).with_for_update())
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(statistics.refresh_stats(kb_id))
        try:
            async with asyncio.timeout(10):
                while True:
                    if task.done():
                        task.result()
                    async with sessions() as observer:
                        waiting = await observer.scalar(
                            text("SELECT count(*) FROM pg_stat_activity WHERE :blocker = ANY(pg_blocking_pids(pid))"),
                            {"blocker": blocker_pid},
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.02)
            await KnowledgeFileRepository().update_fields(
                file_id=kb_id,
                kb_id=kb_id,
                data={"status": "indexed", "chunk_count": 7, "token_count": 42},
            )
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    result = await asyncio.wait_for(task, timeout=10)
    assert result["chunk_count"] == 7
    assert result["pending_index_count"] == 0
    row = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    assert row.additional_params["stats"] == result


async def test_knowledge_base_list_returns_fresh_persisted_stats(stats_store, tmp_path, monkeypatch):
    """文件操作刷新后，真实 HTTP 列表读取持久投影而非旧统计缓存。"""
    kb_id, sessions, redis = stats_store
    cached = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    assert cached["pending_index_count"] == 1
    await redis.expire(f"yuxi:kb_file_stats:{kb_id}", 300)

    async def operation():
        """提交文件状态与统计，供实际聚合读取。"""
        await KnowledgeFileRepository().update_fields(
            file_id=kb_id,
            kb_id=kb_id,
            data={"status": "indexed", "chunk_count": 7, "token_count": 42},
        )

    await statistics.run_with_stats_refresh(kb_id, operation())
    # 本用例验证统计的 HTTP 投影；身份与可见性由知识库权限 integration 验证。
    monkeypatch.setattr(knowledge_access, "get_knowledge_bases_by_uid", lambda _uid: knowledge_bases.get_knowledge_bases())
    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    app.dependency_overrides[knowledge_router.get_admin_user] = lambda: SimpleNamespace(uid="stats-viewer")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        serving.result()
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}") as client:
                response = await client.get("/api/knowledge/knowledge-bases")
            assert response.status_code == 200, response.text
            payload_bases = response.json()["knowledge_bases"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
    assert len(payload_bases) == 1
    result = payload_bases[0]
    assert result["kb_id"] == kb_id
    assert result["stats"]["chunk_count"] == 7
    assert result["stats"]["token_count"] == 42
    assert result["stats"]["pending_index_count"] == 0
    row = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    file = await KnowledgeFileRepository().get_by_file_id(kb_id)
    assert row.additional_params["stats"] == result["stats"]
    assert file.chunk_count == result["stats"]["chunk_count"]
    assert file.token_count == result["stats"]["token_count"]
    fresh = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    assert fresh["chunk_count"] == row.additional_params["stats"]["chunk_count"]
    assert fresh["pending_index_count"] == 0


async def test_second_file_immediately_refreshes_detail_and_file_list_stats(stats_store, tmp_path):
    """旧统计已经缓存时，第二个文件提交后的详情与列表仍读取两个文件。"""
    kb_id, sessions, redis = stats_store
    assert (await KnowledgeFileRepository().get_kb_file_stats(kb_id))["file_count"] == 1
    await redis.set(f"yuxi:kb_file_stats:{kb_id}", '{"file_count":1,"row_count":1}', ex=300)

    async def upload():
        """提交第二个文件的真实元数据，外部文件接收留在上传 E2E 验证。"""
        async with sessions.begin() as db:
            db.add(KnowledgeFile(file_id=kb_id + "-second", kb_id=kb_id, filename="second.md", file_size=6, status="uploaded"))

    await statistics.run_with_stats_refresh(kb_id, upload())
    detail = await knowledge_bases.get_knowledge_base_info(kb_id)
    listing = await knowledge_documents.list_document_files(kb_id)
    assert detail.file_count == 2
    assert detail.total_size == 6
    assert listing["total"] == 2
    assert listing["stats"]["file_count"] == 2
    assert listing["stats"]["total_size"] == 6
