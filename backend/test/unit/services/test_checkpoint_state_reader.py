"""真实 checkpoint 验证完整快照读取、中断与执行初始化隔离。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import yuxi.modules.agents.services.checkpoint_state as svc
from yuxi.modules.agents.repositories.public_items import PublicItemRepository
from yuxi.modules.agents.runtime.agent_backends.chatbot.state import ChatBotState
from yuxi.shared.errors import DomainError, ResourceNotFoundError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _approval(state):
    """产生可由真实图恢复的审批中断。"""
    answer = interrupt({"action_requests": [{"name": "execute", "args": {"command": "pwd"}}]})
    return {"messages": [AIMessage(content=answer)]}


def _unexpected_runtime(*args, **kwargs):
    """读取持久化数据不得初始化 Agent。"""
    raise AssertionError("state query initialized execution runtime")


def _patch_state_view_repositories(monkeypatch, *, session, latest_run=None, turn=None):
    """为读视图测试安装最小的授权与执行仓储边界。"""
    monkeypatch.setattr(
        svc,
        "SessionRepository",
        lambda _db: SimpleNamespace(get_session_by_thread_id=AsyncMock(return_value=session)),
    )
    monkeypatch.setattr(
        svc,
        "AgentRunRepository",
        lambda _db: SimpleNamespace(get_latest_run_by_thread_for_user=AsyncMock(return_value=latest_run)),
    )
    if turn is not None:
        monkeypatch.setattr(
            svc,
            "AgentTurnRepository",
            lambda _db: SimpleNamespace(get_for_scope=AsyncMock(return_value=turn)),
        )
    monkeypatch.setattr(PublicItemRepository, "list_items", AsyncMock(return_value=[]))


@pytest.fixture
def checkpoint_reader(monkeypatch):
    """使用真实内存 saver，并封锁所有执行准备入口。"""
    saver = InMemorySaver()
    monkeypatch.setattr(svc, "get_langgraph_checkpointer", lambda _manager: saver)
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

    _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid="user", thread_id="thread")
    snapshot = await graph.aget_state(config)
    assert values["artifacts"] == ["saved.txt"]
    assert snapshot.values["artifacts"] == ["saved.txt", "pending.txt"]
    assert pending == list(snapshot.tasks[0].interrupts)
    assert values["messages"][0].content == "start"

    await graph.ainvoke(Command(resume="approved"), config)
    _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid="user", thread_id="thread")
    assert pending is None
    assert values["messages"][-1].content == "approved"
    assert values["artifacts"] == ["saved.txt", "pending.txt"]


async def test_checkpoint_reader_missing_thread_is_empty(checkpoint_reader):
    """尚无 checkpoint 的线程返回空视图。"""
    assert await svc._read_checkpoint_state(uid="user", thread_id="missing") == (None, {}, None)


async def test_checkpoint_reader_keeps_next_human_interrupt_after_resume(checkpoint_reader):
    """只有恢复记录而没有业务结果的任务仍在等待，不能丢弃下一次人工提问。"""
    from yuxi.modules.agents.services.execution import _extract_interrupts

    def questions(_state):
        """同一任务连续中断，用于区分恢复写入和完成结果。"""
        interrupt({"questions": [{"question_id": "first", "question": "第一题？"}]})
        answer = interrupt({"questions": [{"question_id": "second", "question": "第二题？"}]})
        return {"messages": [AIMessage(content=answer)]}

    builder = StateGraph(ChatBotState)
    builder.add_node("questions", questions)
    builder.add_edge(START, "questions")
    builder.add_edge("questions", END)
    graph = builder.compile(checkpointer=checkpoint_reader)
    config = {"configurable": {"thread_id": "questions"}}
    await graph.ainvoke({"messages": [HumanMessage(content="start")]}, config)
    await graph.ainvoke(Command(resume="first answer"), config)
    _checkpoint_id, _values, pending = await svc._read_checkpoint_state(uid="user", thread_id="questions")
    assert len(pending) == 1
    assert pending[0].value == {"questions": [{"question_id": "second", "question": "第二题？"}]}
    assert _extract_interrupts(await graph.aget_state(config)) == pending
    await graph.ainvoke(Command(resume="second answer"), config)
    _checkpoint_id, values, pending = await svc._read_checkpoint_state(uid="user", thread_id="questions")
    assert pending is None and values["messages"][-1].content == "second answer"


@pytest.mark.parametrize("include_relations", [False, True])
async def test_state_view_reads_persisted_fields_without_agent_runtime(checkpoint_reader, monkeypatch, include_relations):
    """面板字段及消息来自快照，内部 channel 不进入响应。"""
    from typing import TypedDict

    class DisplayState(TypedDict):
        """包含面板字段及旧文件镜像的测试图。"""

        messages: list
        todos: list
        files: dict
        artifacts: list
        token_usage: dict
        internal_only: str
        warnings: list[dict]

    payload = {
        "messages": [HumanMessage(content="saved message")],
        "todos": [{"content": "saved todo", "status": "pending"}],
        "files": {"legacy.txt": {"content": ["old checkpoint content"]}},
        "artifacts": ["saved.txt"],
        "cooperation": {"sessions": []},
        "token_usage": {"total": 123},
        "internal_only": "not a response field",
        "warnings": [{"code": "mcp_unavailable", "source": "mcp:fixture", "message": "MCP fixture 未连接，请重新连接"}],
    }
    builder = StateGraph(DisplayState)
    builder.add_node("done", lambda state: {})
    builder.add_edge(START, "done")
    builder.add_edge("done", END)
    await builder.compile(checkpointer=checkpoint_reader).ainvoke(payload, {"configurable": {"thread_id": "thread"}})

    session = SimpleNamespace(
        id=1,
        uid="user",
        app_id=None,
        status="active",
        parent_thread_id=None,
        project_id="missing-project",
        agent_state_view=None,
    )
    _patch_state_view_repositories(monkeypatch, session=session)
    monkeypatch.setattr(svc, "tree_snapshot", AsyncMock(return_value={"sessions": []}))
    response = await svc.get_agent_state_view(
        thread_id="thread",
        current_user=SimpleNamespace(uid="user"),
        db=SimpleNamespace(commit=AsyncMock()),
        include_relations=include_relations,
    )
    assert response["agent_state"] == {key: payload[key] for key in ("artifacts", "cooperation", "token_usage", "warnings")}
    assert "todos" not in response["agent_state"]
    assert "items" not in response
    assert "saved message" not in str(response)
    assert "interrupt" not in response
    if include_relations:
        assert response["agent_state"]["cooperation"] == {"sessions": []}
        assert response["parent_thread_id"] is None
        assert set(response) == {"agent_state", "parent_thread_id"}
    else:
        assert set(response) == {"agent_state"}


@pytest.mark.parametrize(
    "agent_session",
    [
        None,
        SimpleNamespace(uid="other-user", app_id=None, status="active"),
        SimpleNamespace(uid="user", app_id=None, status="deleted"),
    ],
)
async def test_state_view_rejects_missing_or_invisible_thread_before_checkpoint(monkeypatch, agent_session):
    """不可见线程不能到达 checkpoint 读取边界。"""

    _patch_state_view_repositories(monkeypatch, session=agent_session)
    monkeypatch.setattr(svc, "get_langgraph_checkpointer", lambda _manager: _unexpected_runtime())
    with pytest.raises(DomainError) as exc:
        await svc.get_agent_state_view(thread_id="thread", current_user=SimpleNamespace(uid="user"), db=None)
    assert isinstance(exc.value, ResourceNotFoundError)


async def test_state_view_releases_read_transaction_before_checkpoint(monkeypatch):
    """负向守卫：授权与 Run 读完成后、进入 checkpoint 全量读前，请求级读事务必须已结束。"""
    events: list[str] = []

    async def commit():
        events.append("commit")

    async def read(*, uid, thread_id):
        events.append("checkpoint")
        return None, {}, None

    session = SimpleNamespace(id=1, uid="user", app_id=None, status="active", parent_thread_id=None, project_id="p1", agent_state_view=None)

    async def get_session(_thread_id):
        events.append("session-read")
        return session

    async def get_latest_run(_thread_id, _uid):
        events.append("run-read")
        return None

    monkeypatch.setattr(svc, "SessionRepository", lambda _db: SimpleNamespace(get_session_by_thread_id=get_session))
    monkeypatch.setattr(svc, "AgentRunRepository", lambda _db: SimpleNamespace(get_latest_run_by_thread_for_user=get_latest_run))
    monkeypatch.setattr(svc, "_read_checkpoint_state", read)

    await svc.get_agent_state_view(
        thread_id="thread",
        current_user=SimpleNamespace(uid="user"),
        db=SimpleNamespace(commit=commit),
        include_relations=False,
    )

    # 提交既不能缺席，也不能早于两次 SELECT（否则授权读取又开新事务，缺陷原样复活）。
    assert events == ["session-read", "run-read", "commit", "checkpoint"], "读事务未在 checkpoint 读取前正确结束"


@pytest.mark.parametrize("turn_status", ["waiting", "cancelled"])
async def test_get_agent_state_view_returns_interrupt_only_for_waiting_owner(monkeypatch: pytest.MonkeyPatch, turn_status):
    thread_id = "thread-1"

    async def _read_checkpoint_state(*, uid, thread_id):
        assert uid == "user-1"
        assert thread_id == "thread-1"
        return None, {}, [
            SimpleNamespace(
                value={
                    "action_requests": [{"name": "execute", "args": {"command": "pytest -q"}}],
                    "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
                }
            )
        ]

    session = SimpleNamespace(
        id=20,
        uid="user-1",
        app_id=None,
        agent_id="main",
        status="active",
        parent_thread_id=None,
        project_id="11111111-1111-4111-8111-111111111111",
        extra_metadata={"model_spec": "provider:agent_session-model"},
        agent_state_view=None,
    )
    latest_run = SimpleNamespace(
        id="run-1",
        turn_id="turn",
        status="interrupted",
        input_payload={"model_spec": "provider:stale-run-model"},
    )
    turn = SimpleNamespace(status=turn_status, current_run_id="run-1", waitpoint={"run_id": "run-1"})
    _patch_state_view_repositories(monkeypatch, session=session, latest_run=latest_run, turn=turn)
    monkeypatch.setattr(svc, "_read_checkpoint_state", _read_checkpoint_state)
    monkeypatch.setattr(svc, "tree_snapshot", AsyncMock(return_value={"sessions": []}))
    result = await svc.get_agent_state_view(
        thread_id=thread_id,
        current_user=SimpleNamespace(uid="user-1"),
        db=SimpleNamespace(commit=AsyncMock()),
    )

    if turn_status == "cancelled":
        assert "interrupt" not in result
        return
    assert result["interrupt"]["status"] == "human_approval_required"
    assert result["interrupt"]["run_id"] == "run-1"
    assert result["interrupt"]["approval"]["action_requests"][0]["name"] == "execute"
