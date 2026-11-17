"""真实 PostgreSQL 验证 /state 在 checkpoint 全量读前释放请求级读事务后仍能完成响应装配。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TypedDict

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text

import yuxi.modules.agents.services.checkpoint_state as svc
from yuxi.modules.agents.models.sessions import Session

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture(autouse=True)
async def _checkpoint_saver_manager():
    """读 checkpoint 需要全局 manager 与其 LangGraph 池；映射必须先加载，否则 mapper 配置失败。"""
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.infrastructure.postgres.models import load_models

    pg_manager.initialize()
    load_models()
    yield
    await pg_manager.close()


class _PanelState(TypedDict, total=False):
    token_usage: dict


async def _seed_checkpoint(dsn: str, uid: str, thread_id: str, total: int) -> None:
    """用真实 saver 写入一个可被面板读取的 checkpoint 版本。"""
    graph = StateGraph(_PanelState)
    graph.add_node("bump", lambda state: {})
    graph.add_edge(START, "bump")
    graph.add_edge("bump", END)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        config = RunnableConfig(configurable={"uid": uid, "thread_id": thread_id, "checkpoint_ns": ""})
        await graph.compile(checkpointer=saver).ainvoke({"token_usage": {"total": total}}, config)


async def _thread_uid(sessions, thread_id: str) -> str:
    async with sessions() as db:
        return await db.scalar(select(Session.uid).where(Session.thread_id == thread_id))


async def _drop_checkpoint_rows(sessions, thread_id: str) -> None:
    """三张 checkpoint 表不在 cleanup_agent_run_threads 的清理面内，由本用例自行归还。"""
    async with sessions() as db:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await db.execute(text(f"delete from {table} where thread_id = :t"), {"t": thread_id})
        await db.commit()


@pytest.mark.parametrize("include_relations", [False, True])
async def test_state_view_holds_no_transaction_during_checkpoint_read(postgres_sessions, monkeypatch, include_relations):
    """checkpoint 读取时请求级会话必须已无事务；其后的关系快照与载荷装配在同一真实会话上继续完成。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-txn", message_content="x", input_payload={}, status="completed"
    )
    uid = await _thread_uid(postgres_sessions, thread_id)
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")
    await _seed_checkpoint(dsn, uid, thread_id, 42)

    held: list[bool] = []
    original = svc._read_checkpoint_state

    try:
        async with postgres_sessions() as db:

            async def probe(*, uid: str, thread_id: str) -> tuple[dict, list | None]:
                """记录 checkpoint 读取瞬间请求级会话是否仍持有事务。"""
                held.append(db.in_transaction())
                return await original(uid=uid, thread_id=thread_id)

            monkeypatch.setattr(svc, "_read_checkpoint_state", probe)
            view = await svc.get_agent_state_view(
                thread_id=thread_id,
                current_user=SimpleNamespace(uid=uid),
                db=db,
                include_relations=include_relations,
            )

        assert held == [False], "checkpoint 读取期间请求级会话仍持有事务（idle in transaction）"
        assert view["agent_state"]["token_usage"] == {"total": 42}
        if include_relations:
            assert "cooperation" in view["agent_state"]
            assert "parent_thread_id" in view
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
