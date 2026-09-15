"""真实 checkpoint 验证完整快照读取、中断与执行初始化隔离。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from yuxi.agents.buildin.chatbot.state import ChatBotState
from yuxi.services import chat_service as svc

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _approval(state):
    """产生可由真实图恢复的审批中断。"""
    answer = interrupt({"action_requests": [{"name": "execute", "args": {"command": "pwd"}}]})
    return {"messages": [AIMessage(content=answer)]}


def _unexpected_runtime(*args, **kwargs):
    """读取持久化数据不得初始化 Agent。"""
    raise AssertionError("state query initialized execution runtime")


@pytest.fixture
def checkpoint_reader(monkeypatch):
    """使用真实内存 saver，并封锁所有执行准备入口。"""
    saver = InMemorySaver()
    monkeypatch.setattr(svc.pg_manager, "get_langgraph_checkpointer", lambda: saver)
    monkeypatch.setattr(svc.agent_manager, "get_agent", _unexpected_runtime)
    monkeypatch.setattr(svc, "AgentRepository", _unexpected_runtime)
    return saver


async def test_checkpoint_reader_keeps_complete_snapshot_and_recovers_interrupt(checkpoint_reader):
    """并行 pending writes 不进入快照，中断仍可恢复并完成。"""
    builder = StateGraph(ChatBotState)
    builder.add_node("approval", _approval)
    builder.add_node("sibling", lambda state: {"artifacts": ["pending.txt"]})
    for node in ("approval", "sibling"):
        builder.add_edge(START, node)
        builder.add_edge(node, END)
    graph = builder.compile(checkpointer=checkpoint_reader)
    config = {"configurable": {"thread_id": "thread"}}
    await graph.ainvoke({"messages": [HumanMessage(content="start")], "artifacts": ["saved.txt"]}, config)

    values, pending = await svc._read_checkpoint_state(uid="user", thread_id="thread")
    snapshot = await graph.aget_state(config)
    assert values["artifacts"] == ["saved.txt"]
    assert snapshot.values["artifacts"] == ["saved.txt", "pending.txt"]
    assert pending == snapshot.tasks[0].interrupts[0]
    assert values["messages"][0].content == "start"

    await graph.ainvoke(Command(resume="approved"), config)
    values, pending = await svc._read_checkpoint_state(uid="user", thread_id="thread")
    assert pending is None
    assert values["messages"][-1].content == "approved"
    assert values["artifacts"] == ["saved.txt", "pending.txt"]


async def test_checkpoint_reader_missing_thread_is_empty(checkpoint_reader):
    """尚无 checkpoint 的线程返回空视图。"""
    assert await svc._read_checkpoint_state(uid="user", thread_id="missing") == ({}, None)


async def test_state_view_reads_persisted_fields_without_agent_runtime(checkpoint_reader, monkeypatch):
    """面板字段及消息来自快照，内部 channel 不进入响应。"""
    from typing import TypedDict

    class DisplayState(TypedDict):
        """包含面板消费字段的测试图。"""

        messages: list
        todos: list
        files: dict
        artifacts: list
        subagent_runs: list
        token_usage: dict
        internal_only: str

    payload = {
        "messages": [HumanMessage(content="saved message")],
        "todos": [{"content": "saved todo", "status": "pending"}],
        "files": {},
        "artifacts": ["saved.txt"],
        "subagent_runs": [{"run_id": "child-run"}],
        "token_usage": {"total": 123},
        "internal_only": "not a response field",
    }
    builder = StateGraph(DisplayState)
    builder.add_node("done", lambda state: {})
    builder.add_edge(START, "done")
    builder.add_edge("done", END)
    await builder.compile(checkpointer=checkpoint_reader).ainvoke(payload, {"configurable": {"thread_id": "thread"}})

    async def conversation(thread_id):
        """返回已授权的持久化线程。"""
        return SimpleNamespace(id=1, uid="user", status="active")

    async def latest_run(thread_id, uid):
        """返回已完成运行。"""
        return SimpleNamespace(status="completed")

    async def workdir(**kwargs):
        """返回所属 Project 的 Workdir。"""
        return "projects/test"

    monkeypatch.setattr(
        svc, "ConversationRepository", lambda db: SimpleNamespace(get_conversation_by_thread_id=conversation)
    )
    monkeypatch.setattr(
        svc, "AgentRunRepository", lambda db: SimpleNamespace(get_latest_run_by_thread_for_user=latest_run)
    )
    monkeypatch.setattr(svc, "resolve_conversation_workdir_path", workdir)
    response = await svc.get_agent_state_view(
        thread_id="thread",
        current_user=SimpleNamespace(uid="user"),
        db=None,
        include_messages=True,
        include_relations=False,
    )
    assert response["agent_state"] == {
        key: payload[key] for key in ("todos", "files", "artifacts", "subagent_runs", "token_usage")
    }
    assert response["messages"][0]["content"] == "saved message"
    assert "interrupt" not in response
    assert set(response) == {"agent_state", "messages"}


@pytest.mark.parametrize("owner,status", [("other-user", "active"), ("user", "deleted")])
async def test_state_view_rejects_invisible_thread_before_checkpoint(monkeypatch, owner, status):
    """不可见线程不能到达 checkpoint 读取边界。"""

    async def conversation(thread_id):
        """返回不可见的线程。"""
        return SimpleNamespace(uid=owner, status=status)

    monkeypatch.setattr(
        svc, "ConversationRepository", lambda db: SimpleNamespace(get_conversation_by_thread_id=conversation)
    )
    monkeypatch.setattr(svc.pg_manager, "get_langgraph_checkpointer", _unexpected_runtime)
    with pytest.raises(HTTPException) as exc:
        await svc.get_agent_state_view(thread_id="thread", current_user=SimpleNamespace(uid="user"), db=None)
    assert exc.value.status_code == 404
