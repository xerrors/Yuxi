"""独立PG schema和真实JWT/TCP供个人文件最终发送验收。"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from server.routers.personal_trash_router import personal_trash
from server.utils.auth_middleware import get_db
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Base,
    AgentRun,
    Conversation,
    Department,
    Message,
    OperationLog,
    Project,
    SubagentThread,
    User,
    UserConfig,
)
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from yuxi.utils.auth_utils import AuthUtils


@pytest_asyncio.fixture
async def personal_history_app(monkeypatch):
    """只允许显式 fixture 数据库；每例独立 schema，不访问应用运行数据。"""
    url = os.environ.get("TEST_POSTGRES_URL", "")
    if os.environ.get("TEST_ALLOW_PERSONAL_TRASH_DB") != "1" or not url:
        pytest.skip("需要明确允许的独立 PG fixture")
    assert make_url(url).database == "fixture", "测试仅接受独立 fixture 数据库"
    schema = "pytest_personal_history_" + uuid4().hex
    admin_engine = create_async_engine(url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    tables = [
        Department.__table__,
        User.__table__,
        Project.__table__,
        Conversation.__table__,
        SubagentThread.__table__,
        UserConfig.__table__,
        OperationLog.__table__,
        PersonalTrashEntry.__table__,
        AgentRun.__table__,
        Message.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as db:
            yield db

    async def request_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-personal-history-only-not-a-production-secret")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "fixture-personal-history")
    headers = {}
    async with sessions() as db:
        db.add_all([Department(id=1, name="dept-A"), Department(id=2, name="dept-B")])
        await db.flush()
        for uid, role, department in [("publisher", "admin", 1), ("member", "user", 1), ("outsider", "user", 2)]:
            user = User(uid=uid, username=uid, role=role, department_id=department, password_hash="fixture")
            db.add(user)
            await db.flush()
            headers[uid] = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
            db.add(UserConfig(uid=uid, enable_memory=True))
            db.add(
                Project(
                    id=uid,
                    uid=uid,
                    name=uid,
                    selection_status="selectable",
                    workdir_path=uid,
                    directory_mode="linked",
                    status="active",
                )
            )
        await db.flush()
        for thread, uid in [("main", "member"), ("child", "member"), ("outsider-thread", "outsider")]:
            db.add(
                Conversation(
                    thread_id=thread,
                    uid=uid,
                    agent_id="chatbot",
                    project_id=uid,
                    status="active",
                    extra_metadata={"unrelated": "keep"},
                )
            )
        await db.flush()
        parent = await db.scalar(select(Conversation).where(Conversation.thread_id == "main"))
        child = await db.scalar(select(Conversation).where(Conversation.thread_id == "child"))
        db.add(
            SubagentThread(
                uid="member",
                parent_conversation_id=parent.id,
                child_conversation_id=child.id,
                child_thread_id="child",
                subagent_slug="helper",
                created_by_run_id="fixture-run",
            )
        )
        await db.commit()
    app = FastAPI()
    app.include_router(personal_trash, prefix="/api")
    app.dependency_overrides[get_db] = request_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            yield SimpleNamespace(client=client, headers=headers, sessions=sessions)
    finally:
        server.should_exit = True
        await task
        sock.close()
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
