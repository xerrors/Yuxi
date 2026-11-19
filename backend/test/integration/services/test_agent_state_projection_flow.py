"""真实 PostgreSQL 上 Run 终态投影往返：终态写→禁读 checkpoint 快路径→陈旧懒回填与删除线程不写。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from sqlalchemy import select, update

import yuxi.modules.agents.services.checkpoint_state as svc
from yuxi.modules.agents.models.sessions import Session

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


async def test_stale_projection_falls_back_to_checkpoint_and_retags_latest_run(postgres_sessions):
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="proj-stale", message_content="x", input_payload={}, status="completed"
    )
    try:
        uid = await _uid(postgres_sessions, thread_id)
        await svc.project_agent_state_after_terminal(uid=uid, thread_id=thread_id, run_id=run_id, values=VALUES)
        async with postgres_sessions() as db:
            await db.execute(
                update(Session).where(Session.thread_id == thread_id).values(agent_state_view={"values": VALUES, "run_id": "run-gone"})
            )
            await db.commit()

        async with postgres_sessions() as db:
            view = await svc.get_agent_state_view(
                thread_id=thread_id,
                current_user=SimpleNamespace(uid=uid),
                db=db,
                include_relations=False,
            )
        # 陈旧投影退回全量 checkpoint 读（本测试线程无 checkpoint → 空值），随后懒回填重打最新 Run。
        assert view["agent_state"]["artifacts"] == []
        retagged = await _stored(postgres_sessions, thread_id)
        assert retagged["run_id"] == str(run_id)
        assert retagged["values"] == {"warnings": [], "artifacts": [], "token_usage": None}
    finally:
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


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
