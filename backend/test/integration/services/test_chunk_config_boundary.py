"""真实 HTTP 和 PostgreSQL 验证非法分块配置不产生持久副作用。"""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.api.routers.knowledge import management as router
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs import dispatch
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile
from yuxi.modules.knowledge.repositories import bases as bases_repository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services import bases as knowledge_bases

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def boundary(monkeypatch):
    """隔离数据，固定认证与外部执行器准备，保留生产校验和登记路径。"""
    schema = f"pytest_chunk_{uuid4().hex}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sock = socket.socket()
    server = None
    task = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(KnowledgeBase.__table__.create)
            await conn.run_sync(BackgroundJobRecord.__table__.create)
            await conn.run_sync(KnowledgeFile.__table__.create)
        async with factory() as db, db.begin():
            db.add(KnowledgeBase(kb_id="fixture-kb", name="Fixture", kb_type="milvus", additional_params={"chunk_preset_id": "book"}))

        @asynccontextmanager
        async def session_context():
            """让生产 repositories 使用独占 schema。"""
            async with factory() as db, db.begin():
                yield db

        monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
        monkeypatch.setattr(dispatch, "publish_job", AsyncMock())
        executor = SimpleNamespace(normalize_additional_params=MilvusKB.normalize_additional_params)
        monkeypatch.setattr(router.knowledge_base_manager, "get_executor_for_type", AsyncMock(return_value=executor))
        detail = SimpleNamespace(name="Fixture", pending_parse_count=1, pending_index_count=1)
        monkeypatch.setattr(router, "_ensure_knowledge_base_supports_documents", AsyncMock(return_value=detail))

        @asynccontextmanager
        async def cache_lock(kb_id):
            """隔离共享 Redis；本测试验证 PostgreSQL 配置事实。"""
            yield

        monkeypatch.setattr(bases_repository, "kb_config_cache_lock", cache_lock)
        monkeypatch.setattr(bases_repository, "delete_cached_kb_config", AsyncMock())
        app = FastAPI()
        app.include_router(router.knowledge, prefix="/api")
        user = SimpleNamespace(uid="fixture-user", department_id=None, role="superadmin")
        app.dependency_overrides[router.get_admin_user] = lambda: user
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Knowledge test server did not start")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            yield client, factory
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            await asyncio.wait_for(task, 5)
        sock.close()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("action", ["parse", "index", "parse-pending", "index-pending", "", "add"])
async def test_invalid_document_request_creates_no_job(boundary, action):
    """所有文档作业提交入口都在登记前拒绝错误配置。"""
    client, factory = boundary
    params = {"chunk_preset_id": "typo"}
    payload = {"params": params}
    if not action.endswith("pending"):
        payload["file_ids"] = ["fixture-file"]
    if action in {"", "add"}:
        payload["items"] = ["minio://knowledgebases/fixture-kb/upload/fixture.md"]
    path = "/api/knowledge/knowledge-bases/fixture-kb/documents" + (f"/{action}" if action else "")
    response = await client.post(path, json=payload)
    assert response.status_code == 400, response.text
    assert next(iter(params)) in response.json()["detail"]
    async with factory() as observer:
        assert await observer.scalar(select(func.count()).select_from(BackgroundJobRecord)) == 0


@pytest.mark.parametrize("method", ["post", "put"])
async def test_invalid_knowledge_config_does_not_change_database(boundary, method):
    """管理创建与更新均在持久化前拒绝未知策略。"""
    client, factory = boundary
    path = "/api/knowledge/knowledge-bases" + ("/fixture-kb" if method == "put" else "")
    response = await client.request(
        method,
        path,
        json={"name": "Invalid", "description": "", "additional_params": {"chunk_preset_id": "typo"}},
    )
    assert response.status_code == 400, response.text
    assert "chunk_preset_id" in response.text
    async with factory() as observer:
        rows = list(await observer.scalars(select(KnowledgeBase)))
        assert len(rows) == 1
        assert rows[0].name == "Fixture"
        assert rows[0].additional_params == {"chunk_preset_id": "book"}


async def test_valid_document_config_is_preserved_in_job(boundary):
    """合法配置仍能登记，独立回读与请求完全一致。"""
    client, factory = boundary
    params = {"chunk_preset_id": "qa", "chunk_parser_config": {"chunk_token_num": 256}}
    response = await client.post(
        "/api/knowledge/knowledge-bases/fixture-kb/documents/index", json={"file_ids": ["fixture-file"], "params": params}
    )
    assert response.status_code == 200, response.text
    async with factory() as observer:
        row = await observer.get(BackgroundJobRecord, response.json()["job_id"])
        assert row is not None
        assert row.payload["params"] == params


async def test_bad_persisted_knowledge_config_can_be_read_and_repaired(boundary):
    """管理读取保留坏值，真实权限依赖允许管理员提交有效修复。"""
    client, factory = boundary
    async with factory() as db, db.begin():
        row = await db.scalar(select(KnowledgeBase))
        row.additional_params = {"chunk_preset_id": "typo"}
    response = await client.get("/api/knowledge/knowledge-bases/fixture-kb")
    assert response.status_code == 200, response.text
    assert response.json()["additional_params"]["chunk_preset_id"] == "typo"
    assert any(row.kb_id == "fixture-kb" for row in await knowledge_bases.get_knowledge_bases())
    response = await client.put(
        "/api/knowledge/knowledge-bases/fixture-kb",
        json={"name": "Repaired", "description": "", "additional_params": {"chunk_preset_id": "qa"}},
    )
    assert response.status_code == 200, response.text
    async with factory() as observer:
        row = await observer.scalar(select(KnowledgeBase))
        assert row.additional_params["chunk_preset_id"] == "qa"


async def _claim_parse(params, *, additional_params=None, operator_id="modifier"):
    """走文件领取事务，配置和状态必须同时提交或回滚。"""
    record = await KnowledgeFileRepository().update_fields_if_status(
        kb_id="fixture-kb",
        file_id="fixture-file",
        allowed_statuses={"uploaded"},
        data={"status": "parsing", "updated_by": operator_id},
        processing_params=params,
        kb_additional_params=additional_params or {},
    )
    assert record is not None
    return record


async def test_processing_claim_repairs_persisted_config(boundary):
    """领取事务校验合并配置，并在持久化状态切换时修复坏值。"""
    _, factory = boundary
    async with factory() as db, db.begin():
        db.add(
            KnowledgeFile(
                file_id="fixture-file",
                kb_id="fixture-kb",
                filename="fixture.md",
                status="uploaded",
                processing_params={"chunk_preset_id": "typo"},
            )
        )
    patch = {"chunk_preset_id": "qa", "chunk_parser_config": {"chunk_token_num": 256}}
    await _claim_parse(patch)
    async with factory() as observer:
        row = await observer.scalar(select(KnowledgeFile))
        assert row.status == "parsing" and row.updated_by == "modifier"
        assert row.processing_params["chunk_preset_id"] == "qa"
        assert row.processing_params["chunk_parser_config"] == {"chunk_token_num": 256}


async def test_empty_claim_patch_preserves_current_chunk_configuration(boundary):
    """缺省补丁保留文件覆盖值和已有解析参数。"""
    _, factory = boundary
    original = {"chunk_preset_id": "qa", "chunk_parser_config": {"chunk_token_num": 256}}
    async with factory() as db, db.begin():
        db.add(
            KnowledgeFile(file_id="fixture-file", kb_id="fixture-kb", filename="fixture.md", status="uploaded", processing_params=original)
        )
    await _claim_parse(
        {"chunk_preset_id": "", "chunk_parser_config": None},
        additional_params={"chunk_preset_id": "book"},
    )
    async with factory() as observer:
        row = await observer.scalar(select(KnowledgeFile))
        assert row.status == "parsing" and row.updated_by == "modifier"
        assert row.processing_params["chunk_preset_id"] == "qa"
        assert row.processing_params["chunk_parser_config"] == original["chunk_parser_config"]


@pytest.mark.parametrize("invalid", [False, 0, []])
async def test_invalid_claim_patch_leaves_persisted_configuration_unchanged(boundary, invalid):
    """假值非法补丁也必须拒绝，不能被空值继承逻辑忽略。"""
    from yuxi.modules.knowledge.chunking.presets import ChunkConfigError

    _, factory = boundary
    original = {"chunk_preset_id": "book"}
    async with factory() as db, db.begin():
        db.add(
            KnowledgeFile(file_id="fixture-file", kb_id="fixture-kb", filename="fixture.md", status="uploaded", processing_params=original)
        )
    with pytest.raises(ChunkConfigError, match="chunk_preset_id"):
        await _claim_parse({"chunk_preset_id": invalid}, operator_id="modifier")
    async with factory() as observer:
        row = await observer.scalar(select(KnowledgeFile))
        assert row.processing_params == original
        assert row.status == "uploaded"
        assert row.processing_job_id is None and row.processing_owner is None
        assert row.updated_by is None


async def test_processing_claim_replaces_non_chunk_nested_options(boundary):
    """普通处理选项仍由请求整体覆盖，不递归保留已删除的键。"""
    _, factory = boundary
    async with factory() as db, db.begin():
        db.add(
            KnowledgeFile(
                file_id="fixture-file",
                kb_id="fixture-kb",
                filename="fixture.md",
                status="uploaded",
                processing_params={"custom": {"keep": 1, "removed": 2}},
            )
        )
    await _claim_parse({"custom": {"keep": 3}})
    async with factory() as observer:
        row = await observer.scalar(select(KnowledgeFile))
        assert row.status == "parsing" and row.updated_by == "modifier"
        assert row.processing_params["custom"] == {"keep": 3}
