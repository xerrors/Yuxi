"""真实 PostgreSQL 上 Run 终态投影往返：终态写→禁读 checkpoint 快路径→陈旧懒回填与删除线程不写。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from fastapi import FastAPI
from sqlalchemy import delete, select, text, update

import yuxi.modules.agents.services.checkpoint_state as svc
from test.integration.support.module_audit import serve_http
from yuxi.api.routers.public_v1.agents.capabilities import router as capabilities_router
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.security import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

VALUES = {"warnings": [{"code": "w"}], "artifacts": ["a.txt"], "token_usage": {"total": 1}}


@pytest.fixture(autouse=True)
async def _projection_manager():
    """投影读写经全局 manager 与其 LangGraph 池；与同域文件对称，initialize 后必 close。"""
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.infrastructure.postgres.models import load_models

    pg_manager.initialize()
    load_models()
    yield
    await pg_manager.close()


async def _uid(sessions, thread_id):
    async with sessions() as db:
        return await db.scalar(select(Session.uid).where(Session.thread_id == thread_id))


async def _stored(sessions, thread_id):
    async with sessions() as db:
        return await db.scalar(select(Session.agent_state_view).where(Session.thread_id == thread_id))


async def test_terminal_projection_roundtrip_serves_fast_path_without_checkpoint(postgres_sessions, monkeypatch):
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-flow", message_content="x", input_payload={}, status="completed"
    )
    try:
        uid = await _uid(postgres_sessions, thread_id)
        await svc.project_agent_state_after_terminal(uid=uid, thread_id=thread_id, run_id=run_id, values=VALUES)
        stored = await _stored(postgres_sessions, thread_id)
        assert stored["run_id"] == str(run_id)
        assert stored["values"] == VALUES

        monkeypatch.setattr(svc, "get_langgraph_checkpointer", lambda _manager: pytest.fail("快路径不得读取 checkpoint"))
        async with postgres_sessions() as db:
            view = await svc.get_agent_state_view(
                thread_id=thread_id,
                current_user=SimpleNamespace(uid=uid),
                db=db,
            )
        assert view["agent_state"]["artifacts"] == ["a.txt"]
        assert view["agent_state"]["warnings"] == [{"code": "w"}]
        assert "cooperation" in view["agent_state"]
    finally:
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


@pytest.mark.parametrize("write_fails", [False, True])
async def test_stale_projection_falls_back_to_checkpoint_and_preserves_session_metadata(postgres_sessions, monkeypatch, write_fails):
    """真实 GET 在回填成功及 PostgreSQL 拒绝回填时都保留响应与会话时间。"""
    monkeypatch.setenv("JWT_SECRET_KEY", uuid4().hex)
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-stale", message_content="x", input_payload={}, status="completed"
    )
    department_name = f"projection-{run_id}"
    try:
        uid = await _uid(postgres_sessions, thread_id)
        stale_projection = {"values": VALUES, "run_id": "run-gone"}
        updated_at = datetime(2020, 1, 1, tzinfo=UTC)
        async with postgres_sessions.begin() as db:
            department = Department(name=department_name)
            db.add(department)
            await db.flush()
            user = await db.scalar(select(User).where(User.uid == uid))
            user.department_id = department.id
            token = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})
            await db.execute(
                update(Session).where(Session.thread_id == thread_id).values(agent_state_view=stale_projection, updated_at=updated_at)
            )

        if write_fails:

            async def fail_backfill(repo, **_kwargs):
                """用真实 SQL 错误使回填事务进入待回滚状态。"""
                await repo.db.execute(text("SELECT 1 / 0"))

            monkeypatch.setattr(SessionRepository, "save_agent_state_view", fail_backfill)

        app = FastAPI()
        app.include_router(capabilities_router, prefix="/api/v1/agents")
        async with serve_http(app) as client:
            response = await client.get(f"/api/v1/agents/sessions/{thread_id}/state", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["agent_state"]["artifacts"] == []
        assert [member["session_id"] for member in view["agent_state"]["cooperation"]["sessions"]] == [thread_id]
        async with postgres_sessions() as db:
            retagged, stored_updated_at = (
                await db.execute(select(Session.agent_state_view, Session.updated_at).where(Session.thread_id == thread_id))
            ).one()
        if write_fails:
            assert retagged == stale_projection
        else:
            assert retagged == {"run_id": run_id, "values": {"warnings": [], "artifacts": [], "token_usage": None}}
        assert stored_updated_at == updated_at
    finally:
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
        async with postgres_sessions.begin() as db:
            await db.execute(delete(Department).where(Department.name == department_name))


async def test_deleted_thread_never_receives_projection(postgres_sessions):
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-deleted", message_content="x", input_payload={}, status="completed"
    )
    try:
        uid = await _uid(postgres_sessions, thread_id)
        async with postgres_sessions() as db:
            await db.execute(update(Session).where(Session.thread_id == thread_id).values(status="deleted"))
            await db.commit()

        await svc.project_agent_state_after_terminal(uid=uid, thread_id=thread_id, run_id=run_id, values=VALUES)

        assert await _stored(postgres_sessions, thread_id) is None
    finally:
        async with postgres_sessions() as db:
            await db.execute(update(Session).where(Session.thread_id == thread_id).values(status="active"))
            await db.commit()
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
