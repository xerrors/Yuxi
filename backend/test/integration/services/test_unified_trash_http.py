"""真实 PostgreSQL、Redis、JWT 与 TCP HTTP 的回收站权限及生命周期。"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text, select, update
from datetime import timedelta
from yuxi.utils.datetime_utils import utc_now
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from server.routers.knowledge_router import knowledge
from server.utils.auth_middleware import get_db
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile
from yuxi.storage.redis import close_async_redis_client
from yuxi.knowledge.cache import delete_cached_kb_config
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.asyncio
async def test_trash_lifecycle_keeps_manage_permissions(monkeypatch):
    """管理权限、普通读取隔离、恢复、整库拒绝及到期结果均回读数据库。"""
    url = os.environ.get("TEST_TRASH_POSTGRES_URL", "")
    if os.environ.get("TEST_ALLOW_TRASH_DB") != "1" or not url:
        pytest.skip("需要显式隔离 fixture PostgreSQL")
    assert make_url(url).database == "trash"
    schema = "pytest_trash_http_" + uuid4().hex
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions.begin() as db:
            yield db

    async def request_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-trash-http-only-secret-20260915")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "fixture-trash-http")
    server = None
    task = None
    sock = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: User.metadata.create_all(c, tables=[Department.__table__, User.__table__]))
            await conn.run_sync(lambda c: KnowledgeBase.metadata.create_all(c))
        headers = {}
        async with sessions() as db:
            db.add_all([Department(id=1, name="department-one"), Department(id=2, name="department-two")])
            await db.flush()
            for uid, role, department in [("member", "user", 1), ("other", "user", 2), ("admin", "superadmin", 1)]:
                user = User(uid=uid, username=uid, role=role, department_id=department, password_hash="fixture")
                db.add(user)
                await db.flush()
                headers[uid] = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
            kb_id = "trash_http_" + uuid4().hex
            file_id = uuid4().hex
            db.add(
                KnowledgeBase(
                    kb_id=kb_id,
                    name="Synthetic trash fixture",
                    kb_type="milvus",
                    created_by="admin",
                    share_config={"version": 2, "read_scope": None, "manage_scope": None},
                    additional_params={},
                )
            )
            await db.flush()
            db.add(
                KnowledgeFile(
                    kb_id=kb_id,
                    file_id=file_id,
                    filename="contract.pdf",
                    status="uploaded",
                    file_type="file",
                    created_by="admin",
                )
            )
            await db.commit()
        app = FastAPI()
        app.include_router(knowledge, prefix="/api")
        app.dependency_overrides[get_db] = request_db
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            base = f"/api/knowledge/databases/{kb_id}"
            assert (await client.get(base + "/trash")).status_code == 401
            assert (await client.get(base + "/trash", headers=headers["member"])).status_code == 403
            response = await client.get("/api/knowledge/trash/databases", headers=headers["admin"])
            assert response.status_code == 200, response.text
            assert response.json() == {"databases": [{"kb_id": kb_id, "name": "Synthetic trash fixture"}]}
            response = await client.delete(base + f"/documents/{file_id}", headers=headers["admin"])
            assert response.status_code == 200, response.text
            async with sessions() as db:
                row = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
                assert row.deleted_at and row.purge_after - row.deleted_at == timedelta(days=30)
            response = await client.get(base + "/trash", headers=headers["admin"])
            assert response.status_code == 200 and response.json()["total"] == 1, response.text
            assert response.json()["items"][0]["status"] == "trashed"
            response = await client.get(base + f"/documents/{file_id}/download", headers=headers["admin"])
            assert response.status_code >= 400
            response = await client.delete(base, headers=headers["admin"])
            assert response.status_code == 409, response.text
            response = await client.post(base + f"/trash/{file_id}/restore", headers=headers["member"])
            assert response.status_code == 403
            response = await client.post(base + f"/trash/{file_id}/restore", headers=headers["admin"])
            assert response.status_code == 200 and response.json()["restored_count"] == 1, response.text
            async with sessions.begin() as db:
                row = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
                assert row.deleted_at is None and row.purge_after is None
                row.status = "parsing"
                row.processing_task_id = "busy"
            response = await client.delete(base + f"/documents/{file_id}", headers=headers["admin"])
            assert response.status_code == 409
            async with sessions.begin() as db:
                await db.execute(
                    update(KnowledgeFile)
                    .where(KnowledgeFile.file_id == file_id)
                    .values(status="uploaded", processing_task_id=None)
                )
            assert (await client.delete(base + f"/documents/{file_id}", headers=headers["admin"])).status_code == 200
            async with sessions.begin() as db:
                await db.execute(
                    update(KnowledgeFile)
                    .where(KnowledgeFile.file_id == file_id)
                    .values(deleted_at=utc_now() - timedelta(days=31), purge_after=utc_now() - timedelta(days=1))
                )
            response = await client.post(base + f"/trash/{file_id}/restore", headers=headers["admin"])
            assert response.status_code == 409
            async with sessions() as db:
                row = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
                assert row.deleted_at is not None
    finally:
        if server:
            server.should_exit = True
        if task:
            await task
        if sock:
            sock.close()
        if "kb_id" in locals():
            await delete_cached_kb_config(kb_id)
        await close_async_redis_client()
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
