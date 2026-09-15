"""隔离 PostgreSQL 与真实 JWT/TCP HTTP 的文档限额验收。"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from server.routers.document_limits_router import document_limits_router
from server.routers.knowledge_router import knowledge
from server.routers.system_router import system
from server.utils.auth_middleware import get_db
from yuxi.config.options import ensure_options_in_db, list_options
from yuxi.services.document_limits_service import snapshot_document_limits
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ConfigOption, Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.asyncio
async def test_limits_http_persistence_and_acceptance(monkeypatch):
    """配置行锁仲裁并发保存，上传在对象写入前执行真实字节检查。"""
    url = os.getenv("TEST_POSTGRES_URL", "")
    if os.getenv("TEST_ALLOW_LIMITS_DB") != "1" or not url:
        pytest.skip("Requires an explicitly isolated test database")
    assert make_url(url).database == "limits"
    schema = "pytest_limits_" + uuid4().hex
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def request_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-limits-only-secret-not-a-production-credential")
    for key, value in {
        "DOCUMENT_UPLOAD_MAX_MIB": "2",
        "DOCUMENT_UPLOAD_HARD_MAX_MIB": "3",
        "DOCUMENT_OCR_MAX_PAGES": "5",
        "DOCUMENT_OCR_HARD_MAX_PAGES": "10",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "fixture-document-limits")
    server = task = sock = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: User.metadata.create_all(
                    c, tables=[Department.__table__, User.__table__, ConfigOption.__table__]
                )
            )
        headers = {}
        async with sessions() as db:
            await ensure_options_in_db(db)
            await ensure_options_in_db(db)
            db.add(Department(id=1, name="fixture-department"))
            await db.flush()
            for uid, role in [("member", "user"), ("admin", "admin"), ("super", "superadmin")]:
                user = User(uid=uid, username=uid, role=role, department_id=1, password_hash="fixture")
                db.add(user)
                await db.flush()
                headers[uid] = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
            await db.commit()
            assert "document_limits" not in [row.key for row in await list_options(db)]
        app = FastAPI()
        for router in (document_limits_router, system, knowledge):
            app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = request_db
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}") as client:
            path = "/api/system/document-limits"
            assert (await client.get(path)).status_code == 401
            response = await client.get(path, headers=headers["member"])
            assert response.status_code == 200, response.text
            initial = response.json()
            assert initial["upload_max_mib"] == 2 and initial["ocr_max_pages"] == 5
            before = await snapshot_document_limits()
            payload = {"upload_max_mib": 1, "ocr_max_pages": 2, "revision": 0}
            for role in ("member", "admin"):
                assert (await client.put(path, json=payload, headers=headers[role])).status_code == 403
            for key, value in [
                ("upload_max_mib", 4),
                ("upload_max_mib", True),
                ("upload_max_mib", 1.5),
                ("ocr_max_pages", 11),
                ("ocr_max_pages", 0),
                ("revision", -1),
            ]:
                response = await client.put(path, json={**payload, key: value}, headers=headers["super"])
                assert response.status_code == 422, response.text
            bypass = await client.put(
                "/api/system/config/options/document_limits", json={"value": payload}, headers=headers["super"]
            )
            assert bypass.status_code == 403
            results = await asyncio.gather(
                *(client.put(path, json=payload, headers=headers["super"]) for _ in range(2))
            )
            assert sorted(r.status_code for r in results) == [200, 409]
            async with sessions() as db:
                row = await db.scalar(select(ConfigOption).where(ConfigOption.key == "document_limits"))
                assert row.value == {"upload_max_mib": 1, "ocr_max_pages": 2, "revision": 1}
                assert row.updated_by == "super"
            after = await snapshot_document_limits()
            assert before == {"max_file_bytes": 2 * 1024 * 1024, "max_ocr_pages": 5, "version": 0}
            assert after == {"max_file_bytes": 1024 * 1024, "max_ocr_pages": 2, "version": 1}
            response = await client.post(
                "/api/knowledge/files/upload",
                files={"file": ("oversize.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
                headers=headers["super"],
            )
            assert response.status_code == 400 and "MiB" in response.text, response.text
            reset = await client.request("DELETE", path, json={"revision": 1}, headers=headers["super"])
            assert reset.status_code == 200, reset.text
            assert reset.json()["revision"] == 2 and reset.json()["upload_max_mib"] == 2
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
