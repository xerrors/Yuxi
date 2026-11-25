"""真实 PostgreSQL 验证：中断从 checkpoint pending writes 恢复。

上游单元测试（test_checkpoint_state_reader.py）用 InMemorySaver 覆盖了该语义；
本集成测试用真实 PostgreSQL 的 AsyncPostgresSaver 验证「持久化的 pending writes 里
__interrupt__ channel」在真实存储后端上同样可读——这是 InMemorySaver 覆盖不到的
存储格式差异，也是本项目现有集成测试（只验快照读取与用户隔离）未覆盖的角度。
"""

from __future__ import annotations

import asyncio
import operator
import os
import uuid
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg_pool import AsyncConnectionPool

import yuxi.modules.agents.services.checkpoint_state as svc
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer, setup_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.modules.agents.services.execution import _extract_interrupts, _waitpoint_from_interrupt, build_pending_interrupt_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


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
    monkeypatch.setattr("yuxi.modules.agents.services.checkpoint_state.pg_manager", manager)
    thread_id = f"pytest-interrupt-{uuid.uuid4()}"
    uid = "pytest-user"

    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        await setup_langgraph_checkpointer(manager)
        graph = StateGraph(_State)
        graph.add_node("approval", _approval_node)
        graph.add_edge(START, "approval")
        graph.add_edge("approval", END)
        compiled = graph.compile(checkpointer=get_langgraph_checkpointer(manager))
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}
        async for _ in compiled.astream({"messages": []}, config, stream_mode="values"):
            pass

        _checkpoint_id, _values, interrupt_info = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)

        assert interrupt_info is not None
        assert [item.value for item in interrupt_info] == [{"question": "是否允许执行该命令？", "tool": "execute"}]

        await get_langgraph_checkpointer(manager).adelete_thread(thread_id)


async def test_completed_checkpoint_returns_no_interrupt(monkeypatch):
    """真实 PG：已完成、无中断的 checkpoint 不得被误判为等待审批。"""
    manager = _new_manager()
    monkeypatch.setattr("yuxi.modules.agents.services.checkpoint_state.pg_manager", manager)
    thread_id = f"pytest-complete-{uuid.uuid4()}"
    uid = "pytest-user"

    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        await setup_langgraph_checkpointer(manager)
        graph = StateGraph(_State)
        graph.add_node("done", lambda state: {"messages": [*state["messages"], "ok"]})
        graph.add_edge(START, "done")
        graph.add_edge("done", END)
        compiled = graph.compile(checkpointer=get_langgraph_checkpointer(manager))
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}
        async for _ in compiled.astream({"messages": []}, config, stream_mode="values"):
            pass

        _checkpoint_id, _values, interrupt_info = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)

        assert interrupt_info is None

        await get_langgraph_checkpointer(manager).adelete_thread(thread_id)


async def test_explicit_waitpoint_cleanup_keeps_context_without_executing_approval(monkeypatch):
    """跳过等待节点后保留历史，旧恢复命令不能执行被取消的副作用。"""
    manager = _new_manager()
    monkeypatch.setattr("yuxi.modules.agents.services.checkpoint_state.pg_manager", manager)
    thread_id = f"pytest-wait-cleanup-{uuid.uuid4()}"
    uid = "pytest-user"
    effects: list[str] = []

    def approval(state):
        """只有明确恢复审批才产生工具效果。"""
        decision = interrupt({"question": "是否执行？"})
        if decision == "approve":
            effects.append("executed")
        return {"messages": [*state["messages"], decision]}

    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        await setup_langgraph_checkpointer(manager)
        saver = get_langgraph_checkpointer(manager)
        graph = StateGraph(_State)
        graph.add_node("approval", approval)
        graph.add_edge(START, "approval")
        graph.add_edge("approval", END)
        compiled = graph.compile(checkpointer=saver)
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}

        try:
            await compiled.ainvoke({"messages": ["prior-context"]}, config)
            _checkpoint_id, _values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
            assert pending is not None

            await compiled.aupdate_state(config, {}, as_node="approval")
            _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
            assert values["messages"] == ["prior-context"]
            assert pending is None
            assert (await compiled.aget_state(config)).next == ()

            await compiled.ainvoke(Command(resume="approve"), config)
            assert effects == []
            _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
            assert values["messages"] == ["prior-context"]
            assert pending is None
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.parametrize("partial_resume", [False, True])
async def test_parallel_waits_recover_by_checkpoint_interrupt_id(monkeypatch, partial_resume):
    """真实图和 PostgreSQL 保留两个等待的身份，按 ID 恢复后各自收到准确结果。"""

    class WaitState(TypedDict):
        """并行节点通过 reducer 保存彼此独立的恢复结果。"""

        results: Annotated[list, operator.add]

    def wait_input(_state):
        """等待精确任务结果。"""
        value = interrupt({"kind": "cooperation", "target_inputs": ["input-1"], "deadline": "2026-10-24T00:00:00+00:00"})
        return {"results": [{"node": "input", "value": value}]}

    def wait_session(_state):
        """等待会话游标后的更新。"""
        value = interrupt(
            {"kind": "cooperation", "target_sessions": ["session-1"], "after_cursor": 3, "deadline": "2026-10-24T00:01:00+00:00"}
        )
        return {"results": [{"node": "session", "value": value}]}

    manager = _new_manager()
    monkeypatch.setattr(svc, "pg_manager", manager)
    thread_id = f"pytest-parallel-interrupt-{uuid.uuid4()}"
    uid = "pytest-user"
    async with (
        asyncio.timeout(20),
        AsyncConnectionPool(_pg_url(), min_size=1, max_size=2, open=False, kwargs={"autocommit": True}) as pool,
    ):
        manager.langgraph_pool = pool
        await setup_langgraph_checkpointer(manager)
        saver = get_langgraph_checkpointer(manager)
        builder = StateGraph(WaitState)
        for name, node in (("input", wait_input), ("session", wait_session)):
            builder.add_node(name, node)
            builder.add_edge(START, name)
            builder.add_edge(name, END)
        graph = builder.compile(checkpointer=saver)
        config = {"configurable": {"uid": uid, "thread_id": thread_id}}
        try:
            await graph.ainvoke({"results": []}, config)
            _checkpoint_id, _values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
            assert pending is not None and len(pending) == 2
            snapshot = await graph.aget_state(config)
            expected_interrupts = {item.id: item.value for item in snapshot.interrupts}
            assert {item.id: item.value for item in pending} == expected_interrupts
            assert {item.id: item.value for item in _extract_interrupts(snapshot)} == expected_interrupts

            payload = build_pending_interrupt_payload(pending, thread_id)
            waitpoint = _waitpoint_from_interrupt(payload, "interrupted-run")
            entries = waitpoint["waits"]
            assert len(entries) == 2 and len({entry["interrupt_id"] for entry in entries}) == 2
            input_id = next(entry["interrupt_id"] for entry in entries if "target_inputs" in entry)
            session_id = next(entry["interrupt_id"] for entry in entries if "target_sessions" in entry)
            input_result = {"results": [{"input_id": "input-1", "output": "precise task result"}], "wait_timed_out": False}
            session_result = {"updates": [{"id": 4, "session_id": "session-1"}], "cursor": 4, "wait_timed_out": False}

            # 原缺陷的单值恢复在真实图上因多个等待没有中断 ID 而失败。
            with pytest.raises(RuntimeError, match="multiple pending interrupts"):
                await graph.ainvoke(Command(resume={"results": []}), config)

            if partial_resume:
                await graph.ainvoke(Command(resume={input_id: input_result}), config)
                _checkpoint_id, _values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
                assert [item.id for item in pending] == [session_id]
                assert build_pending_interrupt_payload(pending, thread_id)["waits"] == [
                    next(entry for entry in entries if entry["interrupt_id"] == session_id)
                ]
                assert [item.id for item in _extract_interrupts(await graph.aget_state(config))] == [session_id]
                await graph.ainvoke(Command(resume={session_id: session_result}), config)
            else:
                # 与原提取顺序相反地构造映射，结果不得依赖等待的列表顺序。
                await graph.ainvoke(Command(resume={session_id: session_result, input_id: input_result}), config)

            _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid=uid, thread_id=thread_id)
            assert pending is None
            assert {item["node"]: item["value"] for item in values["results"]} == {"input": input_result, "session": session_result}
            assert (await graph.aget_state(config)).next == ()
        finally:
            await saver.adelete_thread(thread_id)
