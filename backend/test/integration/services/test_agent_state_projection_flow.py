"""真实 PostgreSQL 上 Run 终态投影往返：终态写→禁读 checkpoint 快路径→陈旧懒回填与删除线程不写。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TypedDict
from uuid import uuid4

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from fastapi import FastAPI
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, select, text, update

import yuxi.modules.agents.services.checkpoint_state as svc
from test.integration.support.module_audit import serve_http
from yuxi.api.routers.public_v1.agents.capabilities import router as capabilities_router
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.security import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

VALUES = {"warnings": [{"code": "w"}], "artifacts": ["a.txt"], "token_usage": {"total": 1}}


@pytest.fixture(autouse=True)
async def _projection_manager():
    """投影读写经全局 manager 与其 LangGraph 池；与同域文件对称，initialize 后必 close。"""
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


async def test_terminal_projection_roundtrip_serves_fast_path_without_checkpoint_payload(postgres_sessions, monkeypatch):
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-flow", message_content="x", input_payload={}, status="completed"
    )
    try:
        uid = await _uid(postgres_sessions, thread_id)
        graph, config = await _start_checkpoint(SimpleNamespace(uid=uid, thread_id=thread_id), VALUES)
        snapshot = await graph.aget_state(config)
        checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
        await svc.project_agent_state_after_terminal(
            uid=uid, thread_id=thread_id, run_id=run_id, values=snapshot.values, checkpoint_id=checkpoint_id
        )
        stored = await _stored(postgres_sessions, thread_id)
        assert stored["run_id"] == str(run_id)
        assert stored["checkpoint_id"] == checkpoint_id
        assert stored["values"] == VALUES

        monkeypatch.setattr(svc, "get_langgraph_checkpointer", lambda _manager: pytest.fail("快路径不得读取 checkpoint 内容"))
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
        await get_langgraph_checkpointer(pg_manager).adelete_thread(thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


@pytest.fixture
async def state_http_thread(postgres_sessions, monkeypatch):
    """创建已终结的会话并使用属主 JWT 访问真实状态路由。"""
    monkeypatch.setenv("JWT_SECRET_KEY", uuid4().hex)
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-stale", message_content="x", input_payload={}, status="completed"
    )
    department_name = f"projection-{run_id}"
    try:
        uid = await _uid(postgres_sessions, thread_id)
        async with postgres_sessions.begin() as db:
            department = Department(name=department_name)
            db.add(department)
            await db.flush()
            user = await db.scalar(select(User).where(User.uid == uid))
            user.department_id = department.id
            token = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})

        app = FastAPI()
        app.include_router(capabilities_router, prefix="/api/v1/agents")
        async with serve_http(app) as client:
            yield SimpleNamespace(
                thread_id=thread_id,
                run_id=run_id,
                uid=uid,
                url=f"/api/v1/agents/sessions/{thread_id}/state",
                headers={"Authorization": f"Bearer {token}"},
                client=client,
            )
    finally:
        await get_langgraph_checkpointer(pg_manager).adelete_thread(thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
        async with postgres_sessions.begin() as db:
            await db.execute(delete(Department).where(Department.name == department_name))


@pytest.mark.parametrize("write_fails", [False, True])
async def test_stale_projection_falls_back_to_checkpoint_and_preserves_session_metadata(
    postgres_sessions, state_http_thread, monkeypatch, write_fails
):
    """真实 GET 在回填成功及 PostgreSQL 拒绝回填时都保留响应与会话时间。"""
    thread = state_http_thread
    graph, config = await _start_checkpoint(thread)
    checkpoint_id = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    stale_projection = {"values": VALUES, "run_id": thread.run_id}
    updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    async with postgres_sessions.begin() as db:
        await db.execute(
            update(Session).where(Session.thread_id == thread.thread_id).values(agent_state_view=stale_projection, updated_at=updated_at)
        )

    if write_fails:
        monkeypatch.setattr(SessionRepository, "save_agent_state_view", _fail_projection_write)

    response = await thread.client.get(thread.url, headers=thread.headers)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["agent_state"]["artifacts"] == []
    assert [member["session_id"] for member in view["agent_state"]["cooperation"]["sessions"]] == [thread.thread_id]
    async with postgres_sessions() as db:
        retagged, stored_updated_at = (
            await db.execute(select(Session.agent_state_view, Session.updated_at).where(Session.thread_id == thread.thread_id))
        ).one()
    if write_fails:
        assert retagged == stale_projection
    else:
        assert retagged == {
            "run_id": thread.run_id,
            "checkpoint_id": checkpoint_id,
            "values": {"warnings": [], "artifacts": [], "token_usage": {"total": 11}},
        }
    assert stored_updated_at == updated_at


async def test_compression_projection_survives_late_backfill(postgres_sessions, state_http_thread, monkeypatch):
    """暂停已读到压缩前状态的 GET，让它在压缩投影落库后回填。"""
    thread = state_http_thread
    graph, config = await _start_checkpoint(thread)
    paused = asyncio.Event()
    resume = asyncio.Event()
    save = SessionRepository.save_agent_state_view

    async def delay_old_write(repo, *, uid, thread_id, payload):
        """仅暂停携带旧用量的请求，保留真实 SQL 写入。"""
        if payload["values"]["token_usage"] == {"total": 11}:
            paused.set()
            await resume.wait()
        return await save(repo, uid=uid, thread_id=thread_id, payload=payload)

    monkeypatch.setattr(SessionRepository, "save_agent_state_view", delay_old_write)
    pending = asyncio.create_task(thread.client.get(thread.url, headers=thread.headers))
    try:
        await asyncio.wait_for(paused.wait(), timeout=5)
        compressed = await graph.aupdate_state(config, {"token_usage": {"total": 3}})
        await svc.refresh_state_projection_after_compression(uid=thread.uid, thread_id=thread.thread_id)
        fresh = await _stored(postgres_sessions, thread.thread_id)
        assert fresh["run_id"] == thread.run_id
        assert fresh["checkpoint_id"] == compressed["configurable"]["checkpoint_id"]
        assert fresh["values"]["token_usage"] == {"total": 3}
    finally:
        resume.set()
        old = await pending
    assert old.status_code == 200, old.text
    assert await _stored(postgres_sessions, thread.thread_id) == fresh
    latest = await thread.client.get(thread.url, headers=thread.headers)
    assert latest.json()["agent_state"]["token_usage"] == {"total": 3}


async def test_compression_projection_refresh_failure_falls_back_to_latest_checkpoint(postgres_sessions, state_http_thread, monkeypatch):
    """压缩后投影写 SQL 失败，后续 GET 回读 checkpoint 并补齐投影。"""
    thread = state_http_thread
    graph, config = await _start_checkpoint(thread)
    initial = await thread.client.get(thread.url, headers=thread.headers)
    assert initial.json()["agent_state"]["token_usage"] == {"total": 11}
    old = await _stored(postgres_sessions, thread.thread_id)
    assert old["run_id"] == thread.run_id

    compressed = await graph.aupdate_state(config, {"token_usage": {"total": 3}})
    with monkeypatch.context() as failure:
        failure.setattr(SessionRepository, "save_agent_state_view", _fail_projection_write)
        await svc.refresh_state_projection_after_compression(uid=thread.uid, thread_id=thread.thread_id)
    assert await _stored(postgres_sessions, thread.thread_id) == old

    latest = await thread.client.get(thread.url, headers=thread.headers)
    assert latest.json()["agent_state"]["token_usage"] == {"total": 3}
    recovered = await _stored(postgres_sessions, thread.thread_id)
    assert recovered["run_id"] == thread.run_id
    assert recovered["checkpoint_id"] == compressed["configurable"]["checkpoint_id"]
    assert recovered["values"]["token_usage"] == {"total": 3}


class _PanelState(TypedDict, total=False):
    """测试图的面板通道。"""

    token_usage: dict
    warnings: list[dict]
    artifacts: list[str]


async def _start_checkpoint(thread, values=None):
    """用真实 LangGraph saver 写入压缩前的状态。"""
    builder = StateGraph(_PanelState)
    builder.add_node("panel", lambda _: {})
    builder.add_edge(START, "panel")
    builder.add_edge("panel", END)
    graph = builder.compile(checkpointer=get_langgraph_checkpointer(pg_manager))
    config = {"configurable": {"uid": thread.uid, "thread_id": thread.thread_id}}
    await graph.ainvoke(values if values is not None else {"token_usage": {"total": 11}}, config)
    return graph, config


async def _fail_projection_write(repo, **_kwargs):
    """用真实 SQL 错误使投影事务进入待回滚状态。"""
    await repo.db.execute(text("SELECT 1 / 0"))


async def test_deleted_thread_never_receives_projection(postgres_sessions):
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-deleted", message_content="x", input_payload={}, status="completed"
    )
    try:
        uid = await _uid(postgres_sessions, thread_id)
        async with postgres_sessions() as db:
            await db.execute(update(Session).where(Session.thread_id == thread_id).values(status="deleted"))
            await db.commit()

        await svc.project_agent_state_after_terminal(uid=uid, thread_id=thread_id, run_id=run_id, values=VALUES, checkpoint_id="checkpoint")

        assert await _stored(postgres_sessions, thread_id) is None
    finally:
        async with postgres_sessions() as db:
            await db.execute(update(Session).where(Session.thread_id == thread_id).values(status="active"))
            await db.commit()
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
