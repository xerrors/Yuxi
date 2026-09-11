"""真实 PostgreSQL 验证：骨架图读取时，中断从 checkpoint pending writes 恢复。

单元测试用 InMemorySaver 验证了 ``_read_pending_interrupt`` 的语义；本集成测试用
真实 PostgreSQL 的 AsyncPostgresSaver 验证「持久化的 pending writes 里 __interrupt__
channel」在真实存储后端上同样可读——这是 InMemorySaver 覆盖不到的存储格式差异。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from psycopg_pool import AsyncConnectionPool
from yuxi.services import chat_service as svc
from yuxi.storage.postgres.manager import PostgresManager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _State(TypedDict):
    messages: list


def _approval_node(state):
    approved = interrupt({"question": "是否允许执行该命令？", "tool": "execute"})
    return {"messages": [*state["messages"], approved]}


def _pg_url() -> str:
    return os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")


def _new_manager() -> PostgresManager:
    manager = object.__new__(PostgresManager)
    manager.__init__()
    manager._initialized = True
    return manager


async def test_pending_interrupt_recovered_from_real_postgres_checkpoint(monkeypatch):
    """真实 PG：停在 interrupt 的 checkpoint，其 pending writes 里的中断可被恢复。"""
    manager = _new_manager()
    monkeypatch.setattr("yuxi.services.chat_service.pg_manager", manager)
    thread_id = f"pytest-interrupt-{uuid.uuid4()}"
    uid = "pytest-user"

    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        graph = StateGraph(_State)
        graph.add_node("approval", _approval_node)
        graph.add_edge(START, "approval")
        graph.add_edge("approval", END)
        compiled = graph.compile(checkpointer=manager.get_langgraph_checkpointer())
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}
        async for _ in compiled.astream({"messages": []}, config, stream_mode="values"):
            pass

        interrupt_info = await svc._read_pending_interrupt(uid=uid, thread_id=thread_id)

        assert interrupt_info is not None
        assert interrupt_info.value == {"question": "是否允许执行该命令？", "tool": "execute"}

        await manager.get_langgraph_checkpointer().adelete_thread(thread_id)


async def test_completed_checkpoint_returns_no_interrupt(monkeypatch):
    """真实 PG：已完成、无中断的 checkpoint 不得被误判为等待审批。"""
    manager = _new_manager()
    monkeypatch.setattr("yuxi.services.chat_service.pg_manager", manager)
    thread_id = f"pytest-complete-{uuid.uuid4()}"
    uid = "pytest-user"

    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        graph = StateGraph(_State)
        graph.add_node("done", lambda state: {"messages": [*state["messages"], "ok"]})
        graph.add_edge(START, "done")
        graph.add_edge("done", END)
        compiled = graph.compile(checkpointer=manager.get_langgraph_checkpointer())
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}
        async for _ in compiled.astream({"messages": []}, config, stream_mode="values"):
            pass

        assert await svc._read_pending_interrupt(uid=uid, thread_id=thread_id) is None

        await manager.get_langgraph_checkpointer().adelete_thread(thread_id)
