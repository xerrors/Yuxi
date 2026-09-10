"""骨架图 state 读取必须保留审批中断，否则刷新状态接口会丢失继续入口。

用**真实 LangGraph checkpoint**（InMemorySaver）对照原图与骨架图：骨架图只有 noop
节点，`aget_state` 无法按原图节点重建 `tasks`，中断必须从 checkpoint 的
`pending_writes`（`__interrupt__` channel）直接恢复。
"""

from __future__ import annotations

import os

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services import chat_service as svc

pytestmark = [pytest.mark.unit]


class _State(TypedDict):
    messages: list


def _approval_node(state):
    approved = interrupt({"question": "是否允许执行该命令？", "tool": "execute"})
    return {"messages": [*state["messages"], approved]}


def _compile_real(saver):
    graph = StateGraph(_State)
    graph.add_node("approval", _approval_node)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=saver)


def _compile_skeleton(saver):
    """与 `_get_state_reader_graph` 同构：零工具、单 noop 节点。"""
    graph = StateGraph(_State)
    graph.add_node("state_reader_noop", lambda state: {})
    graph.set_entry_point("state_reader_noop")
    return graph.compile(checkpointer=saver)


@pytest.fixture()
def interrupted_checkpoint():
    """构造一条停在 interrupt 上的真实 checkpoint，返回 (saver, config)。"""
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "thread-interrupt"}}
    list(_compile_real(saver).stream({"messages": []}, config, stream_mode="values"))
    return saver, config


def test_skeleton_graph_cannot_rebuild_tasks_from_interrupted_checkpoint(interrupted_checkpoint):
    """记录骨架图的固有限制：它重建不出 tasks，因此中断取不到。"""
    saver, config = interrupted_checkpoint

    real_snapshot = _compile_real(saver).get_state(config)
    skeleton_snapshot = _compile_skeleton(saver).get_state(config)

    assert len(real_snapshot.tasks) == 1
    assert real_snapshot.tasks[0].interrupts
    assert not skeleton_snapshot.tasks
    assert svc._extract_interrupt_info(skeleton_snapshot) is None


@pytest.mark.asyncio
async def test_pending_interrupt_is_recovered_from_checkpoint_writes(monkeypatch, interrupted_checkpoint):
    """修复点：中断回退到 checkpoint 原始写入读取，不依赖执行图结构。"""
    saver, config = interrupted_checkpoint
    monkeypatch.setattr(svc.pg_manager, "get_langgraph_checkpointer", lambda: saver)

    interrupt_info = await svc._read_pending_interrupt(uid="user-1", thread_id="thread-interrupt")

    assert interrupt_info is not None
    assert interrupt_info.value == {"question": "是否允许执行该命令？", "tool": "execute"}


@pytest.mark.asyncio
async def test_reader_snapshot_plus_recovered_interrupt_matches_real_graph(monkeypatch, interrupted_checkpoint):
    """骨架图 values + 恢复出的中断，与真实图的审批信息等价。"""
    saver, config = interrupted_checkpoint
    monkeypatch.setattr(svc.pg_manager, "get_langgraph_checkpointer", lambda: saver)

    skeleton_snapshot = _compile_skeleton(saver).get_state(config)
    recovered = await svc._read_pending_interrupt(uid="user-1", thread_id="thread-interrupt")
    real_snapshot = _compile_real(saver).get_state(config)

    assert skeleton_snapshot.values["messages"] == real_snapshot.values["messages"]
    assert recovered.id == real_snapshot.tasks[0].interrupts[0].id
    assert recovered.value == real_snapshot.tasks[0].interrupts[0].value


@pytest.mark.asyncio
async def test_pending_interrupt_returns_none_without_interrupt(monkeypatch):
    """没有中断的 checkpoint 不应被误判为等待审批。"""
    saver = InMemorySaver()
    monkeypatch.setattr(svc.pg_manager, "get_langgraph_checkpointer", lambda: saver)

    complete = StateGraph(_State)
    complete.add_node("done", lambda state: {"messages": [*state["messages"], "ok"]})
    complete.add_edge(START, "done")
    complete.add_edge("done", END)
    graph = complete.compile(checkpointer=saver)
    list(graph.stream({"messages": []}, {"configurable": {"thread_id": "thread-done"}}, stream_mode="values"))

    assert await svc._read_pending_interrupt(uid="user-1", thread_id="thread-done") is None
    assert await svc._read_pending_interrupt(uid="user-1", thread_id="thread-missing") is None
