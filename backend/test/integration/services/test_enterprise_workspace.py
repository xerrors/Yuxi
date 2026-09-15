"""真实 PostgreSQL、JWT 与 TCP HTTP 的企业资料列表投影。"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from server.routers.knowledge_router import knowledge
from server.utils.auth_middleware import get_db
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_knowledge import KnowledgeBase
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.asyncio
async def test_enterprise_scope_keeps_authorized_visibility(monkeypatch):
    """只读投影不把私有或定向用户共享误标为企业，不扩大现有可见性。"""
    url = os.environ.get("TEST_POSTGRES_URL", "")
    if os.environ.get("TEST_ALLOW_ENTERPRISE_WORKSPACE_DB") != "1" or not url:
        pytest.skip("需要显式隔离 fixture PostgreSQL")
    assert make_url(url).database == "fixture"
    schema = "pytest_enterprise_space_" + uuid4().hex
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as db:
            yield db

    async def request_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-enterprise-space-only-secret-20260915")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "fixture-enterprise-space")
    server = None
    task = None
    sock = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: User.metadata.create_all(c, tables=[Department.__table__, User.__table__]))
            await conn.run_sync(lambda c: KnowledgeBase.metadata.create_all(c, tables=[KnowledgeBase.__table__]))
        headers = {}
        async with sessions() as db:
            db.add_all([Department(id=1, name="department-one"), Department(id=2, name="department-two")])
            await db.flush()
            for uid, role, department in [("member", "user", 1), ("other", "user", 2), ("admin", "superadmin", 1)]:
                user = User(uid=uid, username=uid, role=role, department_id=department, password_hash="fixture")
                db.add(user)
                await db.flush()
                headers[uid] = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
            for kb_id, scope in [
                ("global", {"access_level": "global"}),
                ("department-one", {"access_level": "department", "department_ids": [1]}),
                ("department-two", {"access_level": "department", "department_ids": [2]}),
                ("direct", {"access_level": "user", "user_uids": ["member"]}),
                ("private", None),
            ]:
                db.add(
                    KnowledgeBase(
                        kb_id=kb_id,
                        name=kb_id,
                        kb_type="milvus",
                        created_by="other",
                        share_config={"version": 2, "read_scope": scope, "manage_scope": None},
                        additional_params={},
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
            anonymous = await client.get("/api/knowledge/databases/accessible")
            assert anonymous.status_code == 401
            response = await client.get("/api/knowledge/databases/accessible", headers=headers["member"])
            assert response.status_code == 200, response.text
            assert "message" not in response.json(), response.text
            rows = {row["kb_id"]: row for row in response.json()["databases"]}
            assert set(rows) == {"global", "department-one", "direct"}
            assert rows["global"]["is_enterprise_shared"] is True
            assert rows["department-one"]["is_enterprise_shared"] is True
            assert rows["direct"]["is_enterprise_shared"] is False
            response = await client.get("/api/knowledge/databases/accessible", headers=headers["admin"])
            assert "message" not in response.json(), response.text
            rows = {row["kb_id"]: row for row in response.json()["databases"]}
            assert len(rows) == 5
            assert rows["private"]["is_enterprise_shared"] is False
            assert rows["direct"]["is_enterprise_shared"] is False
            assert rows["department-two"]["is_enterprise_shared"] is True
    finally:
        if server:
            server.should_exit = True
        if task:
            await task
        if sock:
            sock.close()
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
