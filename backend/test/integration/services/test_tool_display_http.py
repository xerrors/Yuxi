"""真实 PostgreSQL、JWT 与 TCP HTTP 的工具显示名称持久化。"""

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

from server.routers.tool_router import tools
from server.utils.auth_middleware import get_db
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User, ConfigOption
from yuxi.repositories import tool_display_repository
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.asyncio
async def test_tool_display_names_keep_identity_and_require_admin(monkeypatch):
    """真实鉴权后修改显示名称，数据库与HTTP投影一致且不更改调用身份。"""
    url = os.environ.get("TEST_POSTGRES_URL", "")
    if os.environ.get("TEST_ALLOW_DISPLAY_DB") != "1" or not url:
        pytest.skip("需要显式隔离 fixture PostgreSQL")
    assert make_url(url).database in ("fixture", "display")
    schema = "pytest_tool_names_" + uuid4().hex
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as db:
            yield db
            await db.commit()

    async def request_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-tool-names-only-secret-20260915")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "fixture-tool-names")
    server = None
    task = None
    sock = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: User.metadata.create_all(
                    c, tables=[Department.__table__, User.__table__, ConfigOption.__table__]
                )
            )
        headers = {}
        async with sessions() as db:
            db.add_all([Department(id=1, name="department-one"), Department(id=2, name="department-two")])
            await db.flush()
            for uid, role, department in [("member", "user", 1), ("other", "user", 2), ("admin", "superadmin", 1)]:
                user = User(uid=uid, username=uid, role=role, department_id=department, password_hash="fixture")
                db.add(user)
                await db.flush()
                headers[uid] = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
            await db.commit()
        app = FastAPI()
        app.include_router(tools, prefix="/api")
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
            assert (await client.get("/api/system/tools")).status_code == 401
            response = await client.get("/api/system/tools", headers=headers["member"])
            assert response.status_code == 200, response.text
            original = response.json()["data"][0]
            slug = original["slug"]
            path = f"/api/system/tools/{slug}/display-name"
            assert (await client.put(path, headers=headers["member"], json={"name": "拒绝写入"})).status_code == 403
            assert await tool_display_repository.read_names() == {}
            response = await client.put(path, headers=headers["admin"], json={"name": "中文工具"})
            assert response.status_code == 200, response.text
            assert (await tool_display_repository.read_names())[slug] == "中文工具"
            for _ in range(2):
                listed = await client.get("/api/system/tools", headers=headers["member"])
                current = next(row for row in listed.json()["data"] if row["slug"] == slug)
                assert current == {**original, "name": "中文工具"}
            options = await client.get("/api/system/tools/options", headers=headers["member"])
            assert {"label": "中文工具", "value": slug} in options.json()["data"]
            for invalid in ["<script>", "line\nline", "x" * 81]:
                assert (await client.put(path, headers=headers["admin"], json={"name": invalid})).status_code == 422
            assert (
                await client.put(
                    "/api/system/tools/missing-fixture-tool/display-name",
                    headers=headers["admin"],
                    json={"name": "未知"},
                )
            ).status_code == 404
            assert (await tool_display_repository.read_names())[slug] == "中文工具"
            assert (await client.put(path, headers=headers["admin"], json={"name": ""})).status_code == 200
            assert await tool_display_repository.read_names() == {}
            listed = await client.get("/api/system/tools", headers=headers["member"])
            assert next(row for row in listed.json()["data"] if row["slug"] == slug) == original
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
