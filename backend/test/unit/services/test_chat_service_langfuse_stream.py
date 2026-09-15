from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.messages import AIMessageChunk, HumanMessage

from test.unit.agent_context_fixtures import prepared_execution
from yuxi.services import chat_service as svc
from yuxi.services.input_message_service import build_chat_input_message


@pytest.mark.parametrize("mode", ["chat", "resume"])
async def test_service_consumer_cancel_closes_real_graph(monkeypatch, mode):
    """真实图经 chat/resume 与 Worker 多层流后，消费侧取消仍关闭执行。"""
    from contextlib import aclosing
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import interrupt
    from yuxi.agents.base import BaseAgent
    from yuxi.services.run_worker import RunContext, _consume_stream_with_cancel

    consuming, release, closed = (asyncio.Event() for _ in range(3))
    effects, runs = [], []

    async def node(state):
        """恢复场景先建立真实中断 checkpoint，再在恢复节点中等待。"""
        if mode == "resume":
            interrupt("continue")
        try:
            get_stream_writer()({"type": "yuxi.context_compression", "stage": "waiting"})
            await release.wait()
            effects.append("late write")
            return {"messages": []}
        finally:
            closed.set()

    builder = StateGraph(MessagesState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    if mode == "resume":
        await graph.ainvoke({"messages": ["hello"]}, {"configurable": {"thread_id": "thread-1", "uid": "user-1"}})
    original = graph.astream_events

    async def track_run(*args, **kwargs):
        """保留变异失败时用于清理的实际图 Run。"""
        run = await original(*args, **kwargs)
        runs.append(run)
        return run

    monkeypatch.setattr(graph, "astream_events", track_run)

    class Agent(BaseAgent):
        """服务层使用真实 BaseAgent 流关闭协议。"""

        async def get_graph(self, **kwargs):
            """返回真实执行图。"""
            return graph

    _patch_stream_scaffolding(monkeypatch, agent=Agent(), supply_checkpoint=False)
    kwargs = dict(
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=_FakeSession(),
    )
    stream = (
        svc.stream_agent_chat(
            prepared_execution=prepared_execution(),
            **kwargs,
            agent_slug="test-agent",
            input_message=build_chat_input_message("hello"),
        )
        if mode == "chat"
        else svc.stream_agent_resume(
            prepared_execution=prepared_execution(),
            **kwargs,
            resume_input="continue",
        )
    )

    async def consume():
        """精确在真实节点产生的事件处停止消费。"""
        async with aclosing(_consume_stream_with_cancel(stream, RunContext("run", "owner"))) as chunks:
            async for chunk in chunks:
                if json.loads(chunk)["status"] == "context_compression":
                    consuming.set()
                    await asyncio.Event().wait()

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(consuming.wait(), 2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, 2)
        assert closed.is_set(), "服务已交还 owner，但后台节点尚未关闭"
        release.set()
        await asyncio.sleep(0)
        assert effects == []
    finally:
        release.set()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        for run in runs:
            await run.abort()


async def _fake_normalize_agent_context_config(context, **_kwargs):
    return dict(context or {})


async def _fake_save_messages_from_langgraph_state(
    *,
    state,
    thread_id,
    conv_repo,
    trace_info,
    run_id=None,
    request_id=None,
    worker_id=None,
    complete_run=False,
    interrupt_run=False,
    interrupt_error_type=None,
    interrupt_error_message=None,
    token_usage=None,
):
    del state, thread_id, conv_repo, trace_info
    del run_id, request_id, worker_id, interrupt_error_type, interrupt_error_message, token_usage
    return complete_run or interrupt_run


async def _fake_interrupts(state, make_chunk, meta, thread_id):
    if False:
        yield None
    return


@pytest.mark.parametrize("mode", ["chat", "resume"])
async def test_missing_final_checkpoint_cannot_publish_finished(monkeypatch, mode):
    """缺少持久状态时不能凭流已结束伪造完成。"""

    class Agent:
        """模拟缺少协议终点的执行器。"""

        async def stream_messages_with_state(self, *args, **kwargs):
            """正常耗尽，但未提供 checkpoint。"""
            if False:
                yield None

        stream_resume_with_state = stream_messages_with_state

    agent = Agent()
    original = agent.stream_messages_with_state
    saved = AsyncMock()
    _patch_stream_scaffolding(monkeypatch, agent=agent, save_messages=saved)
    monkeypatch.setattr(agent, "stream_messages_with_state", original)
    monkeypatch.setattr(agent, "stream_resume_with_state", original)
    monkeypatch.setattr(svc, "save_partial_message", AsyncMock())

    @asynccontextmanager
    async def error_session():
        """异常路径也使用隔离会话，不在 unit 中启动真实连接池。"""
        yield _FakeSession()

    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", error_session)
    kwargs = dict(
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=_FakeSession(),
    )
    stream = (
        svc.stream_agent_chat(
            prepared_execution=prepared_execution(),
            **kwargs,
            agent_slug="test-agent",
            input_message=build_chat_input_message("hi"),
        )
        if mode == "chat"
        else svc.stream_agent_resume(
            prepared_execution=prepared_execution(),
            **kwargs,
            resume_input={},
        )
    )
    chunks = [json.loads(chunk) async for chunk in stream]
    assert chunks[-1]["status"] == "error"
    assert "checkpoint" in json.dumps(chunks[-1], ensure_ascii=False)
    assert all(chunk["status"] != "finished" for chunk in chunks)
    saved.assert_not_awaited()


def _patch_stream_scaffolding(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent,
    runtime_context: dict | None = None,
    conversation: SimpleNamespace | None = None,
    save_messages=None,
    build_run_context=None,
    get_trace_info=None,
    flush_langfuse=None,
    supply_checkpoint=True,
):
    resolved_conversation = conversation or SimpleNamespace(
        id=1,
        uid="user-1",
        agent_id="test-agent",
        status="active",
        project_id="11111111-1111-4111-8111-111111111111",
        extra_metadata={},
    )
    if not hasattr(resolved_conversation, "project_id"):
        resolved_conversation.project_id = "11111111-1111-4111-8111-111111111111"

    def with_checkpoint(original):
        """为服务单测的假流提供与 BaseAgent 相同的最终 checkpoint 协议。"""

        async def stream(*args, **kwargs):
            """先耗尽测试事件，再提供测试图的状态。"""
            async for event in original(*args, **kwargs):
                yield event
            context = _FakeContext()
            context.update(kwargs.get("input_context") or {})
            state = SimpleNamespace(values={})
            if hasattr(agent, "get_graph"):
                graph = await agent.get_graph(context=context)
                state = await graph.aget_state({})
            yield "checkpoint", state

        return stream

    for method in ("stream_messages_with_state", "stream_resume_with_state"):
        if supply_checkpoint and hasattr(agent, method):
            monkeypatch.setattr(agent, method, with_checkpoint(getattr(agent, method)))

    async def fake_resolve_agent_runtime(**_kwargs):
        return (
            SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"),
            agent,
            prepared_execution(**(runtime_context or {})).context,
            resolved_conversation,
        )

    async def fake_resolve_workdir(**_kwargs):
        return "projects/11111111-1111-4111-8111-111111111111"

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_path", fake_resolve_workdir)
    monkeypatch.setattr(
        _FakeConvRepo,
        "default_attachments",
        list((resolved_conversation.extra_metadata or {}).get("attachments", [])),
    )
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(
        svc, "save_messages_from_langgraph_state", save_messages or _fake_save_messages_from_langgraph_state
    )
    monkeypatch.setattr(svc, "check_and_handle_interrupts", _fake_interrupts)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        build_run_context or (lambda **kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None)),
    )
    monkeypatch.setattr(svc, "get_trace_info", get_trace_info or (lambda _run_context: {}))
    monkeypatch.setattr(svc, "flush_langfuse", flush_langfuse or (lambda: None))


class _FakeContext:
    def __init__(self):
        self.thread_id = ""
        self.uid = ""
        self.temperature = None

    def update(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)


class _FakeSession:
    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


class _FakeConvRepo:
    default_attachments: list[dict] = []

    def __init__(self, _db):
        self.saved_messages: list[dict] = []
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1,
                uid="user-1",
                agent_id="test-agent",
                thread_id=thread_id,
                status="active",
                project_id="11111111-1111-4111-8111-111111111111",
                extra_metadata={},
            ),
        )

    async def add_message_by_thread_id(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
    ):
        self.saved_messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "message_type": message_type,
                "extra_metadata": extra_metadata,
                "image_content": image_content,
                "run_id": run_id,
                "request_id": request_id,
            }
        )
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self._conversation(thread_id)

    async def get_attachments_by_request_id(self, conversation_id: int, request_id: str):
        return []

    async def get_attachments(self, conversation_id: int):
        del conversation_id
        return [dict(item) for item in self.default_attachments]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize("cancel_during_flush", [False, True])
async def test_trace_flush_yields_to_other_requests_and_is_awaited(
    monkeypatch: pytest.MonkeyPatch, mode: str, cancel_during_flush: bool
):
    """受控上报阻塞期间其他请求能推进，流正常退出仍等待上报完成。"""
    loop = asyncio.get_running_loop()
    flush_started = asyncio.Event()
    release_flush = threading.Event()
    flush_finished = threading.Event()
    statuses = []

    def blocking_flush():
        """超时只为旧实现的负控兜底；成功路径由测试主动释放。"""
        loop.call_soon_threadsafe(flush_started.set)
        release_flush.wait(timeout=2)
        flush_finished.set()

    class FakeAgent:
        """使用共同装配验证普通流与恢复流的 finally。"""

        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            """发出可持久化的模型响应。"""
            yield "messages", (AIMessageChunk(content="hello"), {"node": "llm"})

        stream_resume_with_state = stream_messages_with_state

        async def get_graph(self, *, context=None):
            """提供收尾读取的已完成状态。"""

            class FakeGraph:
                """模拟 checkpoint 的独立状态结果。"""

                async def aget_state(self, config):
                    """返回空附件和文件的最终状态。"""
                    return SimpleNamespace(values={"messages": []})

            return FakeGraph()

    _patch_stream_scaffolding(monkeypatch, agent=FakeAgent(), flush_langfuse=blocking_flush)
    kwargs = {
        "thread_id": "thread-1",
        "meta": {"request_id": "req-1"},
        "current_user": SimpleNamespace(uid="user-1", role="user", department_id=None),
        "db": _FakeSession(),
    }
    if mode == "chat":
        stream = svc.stream_agent_chat(
            prepared_execution=prepared_execution(),
            **kwargs,
            agent_slug="test-agent",
            input_message=build_chat_input_message("hello"),
        )
    else:
        stream = svc.stream_agent_resume(
            prepared_execution=prepared_execution(),
            **kwargs,
            resume_input={"answer": "continue"},
        )

    async def consume():
        """耗尽真实生成器，使 finally 在消费任务中执行。"""
        async for chunk in stream:
            statuses.append(json.loads(chunk)["status"])

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(flush_started.wait(), timeout=3)
        assert not flush_finished.is_set(), "同步 flush 阻塞了无关请求的恢复调度"
        assert not consumer.done(), "正常退出必须等待 flush，不允许 fire-and-forget"
        assert statuses[-1] == "finished"
        if cancel_during_flush:
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
    finally:
        release_flush.set()
        if not consumer.cancelled():
            await asyncio.wait_for(consumer, timeout=3)
        # 取消不能终止已执行的线程，测试必须等它真正退出再还原 monkeypatch。
        assert await asyncio.to_thread(flush_finished.wait, 3)


def test_subagent_attachment_root_rejects_same_path_from_different_project() -> None:
    """共享目录路径不能替代 Project 执行树身份。"""

    child = SimpleNamespace(uid="user-1", project_id="project-child", workdir_path="projects/shared")
    root = SimpleNamespace(uid="user-1", project_id="project-root", workdir_path="projects/shared")

    with pytest.raises(ValueError, match="Project Workdir"):
        svc._validate_subagent_attachment_root(
            root_conversation=root,
            conversation=child,
            uid="user-1",
        )


@pytest.mark.asyncio
async def test_persist_agent_run_langfuse_trace_commits_before_execution(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    db = _FakeSession()

    class FakeRunRepository:
        def __init__(self, session):
            assert session is db

        async def set_langfuse_trace_id(self, run_id, trace_id, *, worker_id):
            calls.update(run_id=run_id, trace_id=trace_id, worker_id=worker_id)
            return SimpleNamespace(id=run_id)

    monkeypatch.setattr(svc, "AgentRunRepository", FakeRunRepository)

    await svc._persist_agent_run_langfuse_trace(
        db=db,
        meta={"run_id": "run-1", "worker_id": "worker-1"},
        run_context=SimpleNamespace(trace_id="trace-1"),
    )

    assert calls == {"run_id": "run-1", "trace_id": "trace-1", "worker_id": "worker-1"}
    assert db.commit_count == 1
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_persist_agent_run_langfuse_trace_skips_when_langfuse_is_disabled(monkeypatch: pytest.MonkeyPatch):
    db = _FakeSession()

    class UnexpectedRepository:
        def __init__(self, _session):
            raise AssertionError("Langfuse 禁用时不应访问 AgentRun repository")

    monkeypatch.setattr(svc, "AgentRunRepository", UnexpectedRepository)

    await svc._persist_agent_run_langfuse_trace(
        db=db,
        meta={"run_id": "run-1", "worker_id": "worker-1"},
        run_context=SimpleNamespace(trace_id=None),
    )

    assert db.commit_count == 0
    assert db.rollback_count == 0


def test_build_langfuse_run_context_reads_evaluation_from_invocation_meta(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    def fake_build_run_context(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(metadata=kwargs.get("extra_metadata") or {}, tags=kwargs.get("extra_tags") or [])

    monkeypatch.setattr(svc, "build_run_context", fake_build_run_context)

    result = svc._build_langfuse_run_context(
        current_user=SimpleNamespace(id=1, uid="user-1", username="alice", department_id=7),
        thread_id="thread-1",
        agent_id="agent-a",
        request_id="req-1",
        operation="agent_chat_stream",
        meta={
            "source": "agent_evaluation",
            "agent_invocation_meta": {
                "evaluation": {
                    "dataset_name": "dataset-a",
                    "dataset_item_id": "item-1",
                    "experiment_name": "exp-1",
                }
            },
        },
    )

    assert result.metadata == {
        "source": "agent_evaluation",
        "feature": "agent_evaluation",
        "evaluation_dataset_name": "dataset-a",
        "evaluation_dataset_item_id": "item-1",
        "evaluation_experiment_name": "exp-1",
    }
    assert result.tags == ["agent_evaluation", "dataset:dataset-a", "experiment:exp-1"]
    assert "evaluation" not in result.metadata


@pytest.mark.asyncio
async def test_stream_agent_chat_commits_before_stream_and_persists_langfuse_context(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}
    lifecycle: list[str] = []
    db = _FakeSession()

    class FakeRunRepository:
        def __init__(self, session):
            assert session is db

        async def set_langfuse_trace_id(self, run_id, trace_id, *, worker_id):
            calls["trace_binding"] = {
                "run_id": run_id,
                "trace_id": trace_id,
                "worker_id": worker_id,
            }
            return SimpleNamespace(id=run_id)

    monkeypatch.setattr(svc, "AgentRunRepository", FakeRunRepository)

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            await kwargs.pop("on_prepared")()
            assert db.commit_count == 2
            assert calls["trace_binding"] == {
                "run_id": "run-1",
                "trace_id": "trace-seeded",
                "worker_id": "worker-1",
            }
            assert lifecycle == ["prepared"]
            lifecycle.append("streaming")
            calls["stream_messages"] = messages
            calls["stream_input_context"] = vars(kwargs.pop("context"))
            calls["stream_kwargs"] = kwargs
            yield "messages", (AIMessageChunk(content="hello"), {"node": "llm"})

        async def get_graph(self, *, context=None):
            class FakeGraph:
                async def aget_state(self, config):
                    return SimpleNamespace(values={"messages": [], "files": {}, "artifacts": []})

            return FakeGraph()

    async def fake_save_messages_from_langgraph_state(
        *,
        state,
        thread_id,
        conv_repo,
        trace_info,
        run_id=None,
        request_id=None,
        worker_id=None,
        complete_run=False,
        interrupt_run=False,
        interrupt_error_type=None,
        interrupt_error_message=None,
        token_usage=None,
    ):
        calls["saved_state"] = {
            "thread_id": thread_id,
            "state": state,
            "trace_info": trace_info,
            "run_id": run_id,
            "request_id": request_id,
            "worker_id": worker_id,
            "complete_run": complete_run,
            "interrupt_run": interrupt_run,
            "interrupt_error_type": interrupt_error_type,
            "interrupt_error_message": interrupt_error_message,
            "token_usage": token_usage,
        }
        return complete_run or interrupt_run

    _patch_stream_scaffolding(
        monkeypatch,
        agent=FakeAgent(),
        runtime_context={
            "model_retry_times": 3,
        },
        conversation=SimpleNamespace(
            id=1,
            uid="user-1",
            agent_id="test-agent",
            status="active",
            extra_metadata={
                "attachments": [
                    {
                        "file_id": "file-1",
                        "file_name": "current.txt",
                        "path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/current.txt",
                        "request_id": "req-1",
                    },
                    {
                        "file_id": "file-2",
                        "file_name": "history.txt",
                        "path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/history.txt",
                        "request_id": "req-old",
                    },
                ]
            },
        ),
        save_messages=fake_save_messages_from_langgraph_state,
        build_run_context=lambda **kwargs: SimpleNamespace(
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": kwargs["current_user"].uid, "langfuse_session_id": kwargs["thread_id"]},
            tags=["yuxi", "chat"],
            trace_id="trace-seeded",
        ),
        get_trace_info=lambda _run_context: {
            "langfuse_trace_id": "trace-seeded",
            "langfuse_session_id": "thread-1",
        },
        flush_langfuse=lambda: calls.setdefault("flushed", True),
    )

    async def on_prepared() -> None:
        assert db.commit_count == 2
        lifecycle.append("prepared")

    def reject_error_fallback(**kwargs):
        """正常结果来自最终 checkpoint，不能再次拼装仅错误路径使用的消息。"""
        raise AssertionError("正常收尾不应构建备用 AIMessage")

    monkeypatch.setattr(svc, "AIMessage", reject_error_fallback)
    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1", "run_id": "run-1", "worker_id": "worker-1"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=db,
        on_prepared=on_prepared,
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert (
        calls["stream_input_context"].items()
        >= {
            "model_retry_times": 3,
            "uid": "user-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "request_id": "req-1",
        }.items()
    )
    assert calls["stream_kwargs"] == {
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1", "langfuse_session_id": "thread-1"},
        "tags": ["yuxi", "chat"],
    }
    model_message = calls["stream_messages"][0]
    assert model_message.content.startswith("hello\n\n<attachment_context>")
    assert "current.txt" in model_message.content
    assert "history.txt" in model_message.content
    assert calls["saved_state"]["trace_info"] == {
        "langfuse_trace_id": "trace-seeded",
        "langfuse_session_id": "thread-1",
    }
    assert calls["saved_state"]["state"].values == {"messages": [], "files": {}, "artifacts": []}
    assert calls["saved_state"]["complete_run"] is True
    assert chunks[-1]["status"] == "finished"
    assert calls["stream_input_context"]["workdir_relative_path"] == "projects/11111111-1111-4111-8111-111111111111"
    assert (
        calls["stream_input_context"]["workdir_path"]
        == "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111"
    )
    assert calls["stream_input_context"]["runtime_scope_id"] == "thread-1"
    [init_attachment] = chunks[0]["msg"]["extra_metadata"]["attachments"]
    assert init_attachment["file_name"] == "current.txt"
    assert init_attachment["path"].endswith("/uploads/current.txt")
    assert calls["flushed"] is True
    assert isinstance(calls["stream_messages"][0], HumanMessage)
    assert lifecycle == ["prepared", "streaming"]


@pytest.mark.asyncio
async def test_stream_agent_chat_partial_failure_preserves_trace_info(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, object] = {}

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            del messages, input_context, kwargs
            yield "messages", (AIMessageChunk(content="partial"), {"node": "llm"})
            raise RuntimeError("stream failed")

    @asynccontextmanager
    async def fake_session_context():
        yield _FakeSession()

    async def fake_save_partial_message(
        _conv_repo,
        thread_id,
        *,
        full_msg,
        trace_info,
        **_kwargs,
    ):
        calls["partial"] = {
            "thread_id": thread_id,
            "content": full_msg.content,
            "trace_info": trace_info,
        }

    _patch_stream_scaffolding(
        monkeypatch,
        agent=FakeAgent(),
        build_run_context=lambda **_kwargs: SimpleNamespace(
            callbacks=[],
            metadata={},
            tags=[],
            trace_id="trace-partial",
        ),
        get_trace_info=lambda _run_context: {
            "langfuse_trace_id": "trace-partial",
            "langfuse_session_id": "thread-partial",
        },
    )
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(svc, "save_partial_message", fake_save_partial_message)

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-partial",
        meta={"request_id": "request-partial"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert calls["partial"] == {
        "thread_id": "thread-partial",
        "content": "partial",
        "trace_info": {
            "langfuse_trace_id": "trace-partial",
            "langfuse_session_id": "thread-partial",
        },
    }
    assert chunks[-1]["status"] == "error"
    assert chunks[-1]["error_type"] == "unexpected_error"


@pytest.mark.asyncio
async def test_stream_agent_chat_does_not_bootstrap_sandbox_before_agent_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    agent_started = False

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            nonlocal agent_started
            del messages, input_context, kwargs
            agent_started = True
            yield "messages", (AIMessageChunk(content="runs without sandbox"), {"node": "llm"})

    _patch_stream_scaffolding(
        monkeypatch,
        agent=FakeAgent(),
        conversation=SimpleNamespace(
            id=1,
            uid="user-1",
            agent_id="test-agent",
            status="active",
            extra_metadata={"attachments": [{"file_id": "file-1"}]},
        ),
    )

    class UnexpectedSandboxBackend:
        def __init__(self, **_kwargs):
            raise AssertionError("纯文本 Agent 流不应构造 Sandbox Backend")

        def ensure_available(self):
            raise AssertionError("纯文本 Agent 流不应预创建 Sandbox")

    monkeypatch.setattr(svc, "ProvisionerSandboxBackend", UnexpectedSandboxBackend, raising=False)
    monkeypatch.setattr(
        svc,
        "get_user_skills_root_dir",
        lambda _uid: (_ for _ in ()).throw(AssertionError("纯文本 Agent 流不应物化 Skill 投影根")),
        raising=False,
    )

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert agent_started is True
    assert chunks[-1]["status"] == "finished"
    assert any(chunk.get("response") == "runs without sandbox" for chunk in chunks)


@pytest.mark.asyncio
async def test_stream_agent_chat_output_persistence_failure_is_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            del messages, input_context, kwargs
            yield "messages", (AIMessageChunk(content="answer"), {"node": "llm"})

        async def get_graph(self, *, context=None):
            del context

            class FakeGraph:
                async def aget_state(self, _config):
                    return SimpleNamespace(values={"messages": []})

            return FakeGraph()

    async def fail_output_persistence(**_kwargs):
        raise ValueError("output binding rejected")

    _patch_stream_scaffolding(
        monkeypatch,
        agent=FakeAgent(),
        save_messages=fail_output_persistence,
    )

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-output-error",
        meta={
            "run_id": "run-output-error",
            "request_id": "request-output-error",
            "worker_id": "worker-output-error:attempt-1",
        },
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    assert chunks[-1]["status"] == "error"
    assert chunks[-1]["error_type"] == "output_persistence_error"
    assert all(chunk.get("status") not in {"finished", "warning"} for chunk in chunks)


@pytest.mark.asyncio
async def test_stream_agent_chat_maps_raw_protocol_events_to_yuxi_stream_events(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [], "files": {}, "artifacts": []})

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            del messages, input_context, kwargs
            metadata = {"run_id": "run-1"}
            yield "messages", ({"event": "message-start", "id": "msg-1", "role": "ai"}, metadata)
            yield "messages", ({"event": "content-block-start", "index": 0, "content": {"type": "text"}}, metadata)
            yield (
                "messages",
                (
                    {"event": "content-block-delta", "index": 0, "delta": {"type": "text-delta", "text": "hello"}},
                    metadata,
                ),
            )
            yield (
                "messages",
                (
                    {
                        "event": "content-block-delta",
                        "index": 1,
                        "delta": {
                            "type": "block-delta",
                            "fields": {
                                "type": "tool_call_chunk",
                                "id": "call-1",
                                "name": "task",
                                "args": '{"description":"do',
                                "index": 0,
                            },
                        },
                    },
                    metadata,
                ),
            )
            yield (
                "messages",
                (
                    {
                        "event": "content-block-finish",
                        "index": 1,
                        "content": {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "task",
                            "args": {"description": "do work", "subagent_slug": "worker"},
                        },
                    },
                    metadata,
                ),
            )
            yield "messages", ({"event": "message-finish", "usage": {}}, metadata)

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self, *, context=None):
            return FakeGraph()

    _patch_stream_scaffolding(monkeypatch, agent=FakeAgent())

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    loading_chunks = [chunk for chunk in chunks if chunk.get("status") == "loading"]
    assert [chunk["stream_event"]["type"] for chunk in loading_chunks] == ["message_delta", "tool_call"]
    assert loading_chunks[0]["response"] == "hello"
    assert loading_chunks[0]["stream_event"] == {
        "type": "message_delta",
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "namespace": [],
        "content": "hello",
    }
    assert loading_chunks[1]["response"] == ""
    assert loading_chunks[1]["stream_event"] == {
        "type": "tool_call",
        "message_id": "msg-1",
        "tool_call_id": "call-1",
        "name": "task",
        "args": {"description": "do work", "subagent_slug": "worker"},
        "index": 1,
        "thread_id": "thread-1",
        "namespace": [],
    }
    assert all("msg" not in chunk for chunk in loading_chunks)


@pytest.mark.asyncio
async def test_stream_agent_chat_emits_realtime_agent_state_from_values(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"todos": [{"content": "done", "status": "completed"}]})

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "pending"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "values", {"messages": [], "todos": [{"content": "step 1", "status": "in_progress"}]}
            yield "messages", (AIMessageChunk(content="hello"), {"node": "llm"})

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self, *, context=None):
            return FakeGraph()

    _patch_stream_scaffolding(monkeypatch, agent=FakeAgent())

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    agent_state_chunks = [chunk for chunk in chunks if chunk.get("status") == "agent_state"]
    assert len(agent_state_chunks) == 3
    assert agent_state_chunks[0]["agent_state"]["todos"][0]["status"] == "pending"
    assert agent_state_chunks[1]["agent_state"]["todos"][0]["status"] == "in_progress"
    assert agent_state_chunks[2]["agent_state"]["todos"][0]["status"] == "completed"
    assert all("agent_slug" in chunk.get("meta", {}) for chunk in chunks if isinstance(chunk.get("meta"), dict))
    assert all("agent_id" not in chunk.get("meta", {}) for chunk in chunks if isinstance(chunk.get("meta"), dict))


@pytest.mark.asyncio
async def test_stream_agent_chat_maps_custom_compression_event_to_context_compression_chunk(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": []})

    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            yield "custom", {"type": "yuxi.context_compression", "status": "started"}
            yield "messages", (AIMessageChunk(content="hi"), {"node": "llm"})
            yield (
                "custom",
                {
                    "type": "yuxi.context_compression",
                    "status": "completed",
                    "cutoff_index": 5,
                    "file_path": "/conv/x.md",
                },
            )

        async def stream_messages(self, messages, input_context=None, **kwargs):
            raise AssertionError("stream_messages fallback should not be used")

        async def get_graph(self, *, context=None):
            return FakeGraph()

    _patch_stream_scaffolding(monkeypatch, agent=FakeAgent())

    chunks = []
    async for chunk in svc.stream_agent_chat(
        prepared_execution=prepared_execution(),
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        input_message=build_chat_input_message("hello"),
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))

    compression_chunks = [chunk for chunk in chunks if chunk.get("status") == "context_compression"]
    assert len(compression_chunks) == 2
    assert compression_chunks[0]["compression"]["status"] == "started"
    assert compression_chunks[1]["compression"]["status"] == "completed"
    assert compression_chunks[1]["compression"]["cutoff_index"] == 5
    assert compression_chunks[1]["compression"]["file_path"] == "/conv/x.md"


@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize(
    ("thread_id", "meta"),
    [
        (None, {"request_id": "req-1"}),
        ("", {"request_id": "req-1"}),
        ("thread-1", {}),
        ("thread-1", {"request_id": ""}),
    ],
)
async def test_execution_rejects_missing_persisted_identity(mode, thread_id, meta):
    """执行入口在任何数据库或模型动作前拒绝缺失身份，不能自动创建请求。"""
    kwargs = dict(
        thread_id=thread_id,
        meta=meta,
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
        prepared_execution=prepared_execution(),
    )
    stream = (
        svc.stream_agent_chat(**kwargs, agent_slug="test-agent", input_message=build_chat_input_message("hello"))
        if mode == "chat"
        else svc.stream_agent_resume(**kwargs, resume_input={"answer": "ok"})
    )
    with pytest.raises(ValueError, match="执行需要已持久化的 thread_id 和 request_id"):
        await anext(stream)
    await stream.aclose()


@pytest.mark.parametrize("mode", ["chat", "resume"])
def test_execution_requires_worker_snapshot(mode):
    """调用方必须显式提供执行快照，不能启用重新读取配置的旧路径。"""
    kwargs = dict(
        thread_id="thread-1", meta={"request_id": "req-1"}, current_user=SimpleNamespace(uid="user-1"), db=object()
    )
    with pytest.raises(TypeError, match="prepared_execution"):
        if mode == "chat":
            svc.stream_agent_chat(**kwargs, agent_slug="test-agent", input_message=build_chat_input_message("hello"))
        else:
            svc.stream_agent_resume(**kwargs, resume_input={"answer": "ok"})
