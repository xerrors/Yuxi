"""执行边界使用原生协议和独立执行结果，业务持久化由 assembled E2E 证明。"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from test.unit.support.agent_context_fixtures import prepared_execution
from test.support.agent_stream_fixtures import FinalAnswerModel, native
from yuxi.modules.agents.runtime.base import BaseAgent, GraphExecutionResult
from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.runtime.middlewares.steer import SteerMiddleware
import yuxi.modules.agents.services.execution as svc
from yuxi.modules.agents.services.execution import RunExecutionResult
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.agents.services.tracing import LangfuseRunContext


class _FakeSession:
    """记录服务拥有的事务边界。"""

    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


def _patch_stream_scaffolding(monkeypatch, *, agent, supply_checkpoint=True, **_kwargs):
    """只隔离持久化与可选观测，不替换公开协议适配。"""

    async def resolve(**kwargs):
        return (
            SimpleNamespace(slug="test-agent", name="测试", backend_id="ChatbotAgent"),
            agent,
            prepared_execution().context,
            SimpleNamespace(id=1, uid="user-1", app_id=None),
        )

    class ConvRepo:
        def __init__(self, db):
            self.db = db

        async def list_for_thread(self, *_args, **_kwargs):
            return []

        async def for_messages(self, message_ids):
            """不含附件的正式消息仍返回完整键集合。"""
            return {message_id: [] for message_id in message_ids}

    async def save(**kwargs):
        return "interrupted" if kwargs["interrupt_run"] else "completed"

    async def save_item(adapter, operation, role, key, item, content_index=None):
        return {
            **deepcopy(item),
            "id": f"{operation}:{role}:{key}",
            "turn_id": adapter.turn_id,
            "yuxi": {**item.get("yuxi", {}), "run_id": adapter.run_id, "output_index": 0, "message_id": 1},
        }

    @asynccontextmanager
    async def session():
        yield _FakeSession()

    monkeypatch.setattr(svc, "_resolve_agent_runtime", resolve)
    monkeypatch.setattr(svc, "AttachmentRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", lambda db: SimpleNamespace(get_run=AsyncMock(return_value=None)))

    async def persist_trace(*, db, **_kwargs):
        """追踪准备不拥有提交。"""
        return None

    monkeypatch.setattr(svc, "_persist_agent_run_langfuse_trace", persist_trace)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kw: LangfuseRunContext(metadata={"run_id": kw["run_id"], "turn_id": kw["turn_id"]}),
    )
    monkeypatch.setattr(svc, "get_trace_info", lambda _: {"langfuse_trace_id": "trace"})
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", save)
    monkeypatch.setattr(svc, "save_partial_message", AsyncMock(return_value=SimpleNamespace(id=2)))
    monkeypatch.setattr(svc.RunMessageRecorder, "consume", AsyncMock(side_effect=lambda event: event))
    monkeypatch.setattr(svc.OpenAIEventAdapter, "_save", save_item)
    monkeypatch.setattr(svc.OpenAIEventAdapter, "finish", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.pg_manager, "get_async_session_context", session)


class FakeAgent:
    """控制真实执行边界的协议序列和独立 checkpoint。"""

    def __init__(self, events=(), checkpoint=None, error=None, missing=False):
        self.events = events
        self.checkpoint = checkpoint or SimpleNamespace(values={}, tasks=())
        self.error = error
        self.missing = missing
        self.input = None
        self.kwargs = None

    async def stream_messages_with_state(self, value, **kwargs):
        self.input, self.kwargs = value, kwargs
        if kwargs.get("on_prepared"):
            await kwargs["on_prepared"]()
        for event in self.events:
            yield event
        if self.error:
            raise self.error
        if not self.missing:
            yield GraphExecutionResult(checkpoint=self.checkpoint)

    stream_resume_with_state = stream_messages_with_state


def execute(mode="chat", *, db=None, on_prepared=None, image=None):
    """chat/resume 使用相同持久化身份和已冻结的执行快照。"""
    kwargs = dict(
        prepared_execution=prepared_execution(),
        thread_id="thread-1",
        meta={"turn_id": "turn-1", "run_id": "run-1", "worker_id": "worker-1", "input_id": "input-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=db or _FakeSession(),
        on_prepared=on_prepared,
    )
    return (
        svc.stream_agent_execution(
            **kwargs,
            agent_slug="test-agent",
            input_messages=[build_chat_input_message("hello", image).with_metadata({"message_id": 1})],
        )
        if mode == "chat"
        else svc.stream_agent_execution(is_resume=True, **kwargs, resume_input={"answer": "yes"})
    )


def text_events(text="hello", metadata=None):
    """原生模型正文有明确的模型操作和内容块身份。"""
    events = [
        native(0, {"event": "message-start", "id": "model"}),
        native(1, {"event": "content-block-delta", "index": 0, "delta": {"type": "text-delta", "text": text}}),
    ]
    if metadata:
        for event in events:
            event["params"]["data"] = (event["params"]["data"][0], metadata)
    return events


async def test_execution_keeps_attachments_on_each_inputs_message(monkeypatch):
    """各 Input 的附件只补入自己的末条消息，保留正文和图片。"""
    from yuxi.shared.datetime import utc_now

    agent = FakeAgent()
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    attachments = [
        SimpleNamespace(
            id=f"file-{message_id}",
            filename=f"file-{message_id}.txt",
            mime_type="text/plain",
            size_bytes=1,
            status="ready",
            created_at=utc_now(),
            path=f"/home/user/workspace/uploads/file-{message_id}.txt",
            original_path=f"/home/user/workspace/uploads/file-{message_id}.txt",
            input_id=input_id,
            message_id=message_id,
        )
        for message_id, input_id in ((2, "input-a"), (4, "input-b"), (99, "input-old"))
    ]
    attachments_by_message = {1: [], 2: [attachments[0]], 3: [], 4: [attachments[1]], 99: [attachments[2]]}

    async def for_messages(message_ids):
        """只返回正式 Message 已有的附件归属。"""
        return {message_id: attachments_by_message[message_id] for message_id in message_ids}

    monkeypatch.setattr(
        svc,
        "AttachmentRepository",
        lambda db: SimpleNamespace(
            for_messages=for_messages,
            # 恢复旧扫描时返回历史附件，让负控因附件错挂而失败。
            list_for_thread=AsyncMock(return_value=attachments),
        ),
    )
    messages = [
        build_chat_input_message("INPUT_A_FIRST").with_metadata({"message_id": 1}),
        build_chat_input_message("INPUT_A_LAST").with_metadata({"message_id": 2}),
        build_chat_input_message("INPUT_B_FIRST").with_metadata({"message_id": 3}),
        build_chat_input_message("INPUT_B_LAST", "image-proof").with_metadata({"message_id": 4}),
    ]
    original_messages = deepcopy(messages)

    results = [
        event
        async for event in svc.stream_agent_execution(
            prepared_execution=prepared_execution(),
            thread_id="thread-1",
            meta={"turn_id": "turn-1", "run_id": "run-1", "worker_id": "worker-1", "input_id": "input-1"},
            current_user=SimpleNamespace(uid="user-1"),
            db=_FakeSession(),
            agent_slug="test-agent",
            input_messages=messages,
        )
        if isinstance(event, RunExecutionResult)
    ]

    assert results[0].status == "completed"
    assert len(agent.input) == 4
    assert agent.input[0].content == "INPUT_A_FIRST"
    assert agent.input[2].content == "INPUT_B_FIRST"
    assert "file-2.txt" in agent.input[1].content and "file-4.txt" not in agent.input[1].content
    assert "file-4.txt" in str(agent.input[3].content) and "file-2.txt" not in str(agent.input[3].content)
    assert all("file-99.txt" not in str(message.content) for message in agent.input)
    assert messages == original_messages
    assert agent.input[3].content[:2] == messages[3].require_langchain_message().content



@pytest.mark.parametrize("boundary", ["before_model", "after_tools", "after_model"])
async def test_cancelled_steer_continues_real_graph_without_replaying_input(monkeypatch, boundary):
    """取消让位批次不重放输入或工具；模型后结束不产生额外模型调用。"""
    from langchain.agents import create_agent
    from langchain.messages import AIMessage
    from langchain.tools import tool
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver
    from yuxi.modules.agents.services import runs

    cancelled = False
    tool_calls = 0

    class Model(FinalAnswerModel):
        """工具场景先请求一次工具，然后生成最终回答。"""

        def bind_tools(self, tools, **kwargs):
            """测试模型接受图的工具绑定。"""
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            """生成固定工具请求或继承最终回答。"""
            if boundary == "after_tools" and self.call_count == 0:
                self.call_count += 1
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[{"name": "complete_tool", "args": {}, "id": "tool-1", "type": "tool_call"}],
                            )
                        )
                    ]
                )
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @tool
    def complete_tool() -> str:
        """记录实际工具执行，检查 checkpoint 继续不重复副作用。"""
        nonlocal tool_calls
        tool_calls += 1
        return "tool done"

    model = Model()

    async def should_yield(run_id):
        return not cancelled and (
            boundary == "before_model"
            or (boundary == "after_tools" and tool_calls > 0)
            or (boundary == "after_model" and model.call_count > 0)
        )

    monkeypatch.setattr(runs, "should_yield_for_steer", should_yield)
    graph = create_agent(
        model=model,
        middleware=[SteerMiddleware()],
        context_schema=BaseContext,
        tools=[complete_tool] if boundary == "after_tools" else [],
        checkpointer=InMemorySaver(),
    )

    class Agent(BaseAgent):
        """保持同一图和 checkpoint，使用实际 BaseAgent 协议适配。"""

        async def get_graph(self, **kwargs):
            return graph

    _patch_stream_scaffolding(monkeypatch, agent=Agent())
    # 本例验证 checkpoint 执行继续，公开投影和审计落库由各自测试拥有。
    monkeypatch.setattr(svc.OpenAIEventAdapter, "consume", AsyncMock(return_value=[]))
    checkpoints = []

    async def save(**kwargs):
        nonlocal cancelled
        checkpoints.append(kwargs["state"])
        assert (kwargs["run_id"], kwargs["worker_id"]) == ("run-1", "worker-1")
        cancelled = True
        if kwargs["steer_before_model"]:
            return "running"
        return "completed"

    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", save)
    results = [event async for event in execute() if isinstance(event, RunExecutionResult)]
    assert len(results) == 1 and results[0].status == "completed"
    assert len(checkpoints) == (1 if boundary == "after_model" else 2)
    assert model.call_count == (2 if boundary == "after_tools" else 1)
    assert tool_calls == (1 if boundary == "after_tools" else 0)
    assert all(state.metadata["yuxi_run_id"] == "run-1" and "run_id" not in state.metadata for state in checkpoints)
    messages = results[0].checkpoint.values["messages"]
    assert [message.content for message in messages if isinstance(message, HumanMessage)] == ["hello"]
    assert len([message for message in messages if isinstance(message, AIMessage)]) == model.call_count


async def test_cancelled_steer_continuation_keeps_extension_event_ids_unique(monkeypatch):
    """图继续时源 seq 重新开始，公开状态和压缩事件仍有不同身份。"""

    class Agent(FakeAgent):
        """生成两个图段，各自从相同 seq 开始。"""

        calls = 0

        async def stream_messages_with_state(self, value, **kwargs):
            """返回不同业务状态及可复现的源序号冲突。"""
            self.calls += 1
            state = {"artifacts": [f"segment-{self.calls}"]}
            yield {"method": "values", "seq": 1, "params": {"data": state, "namespace": []}}
            yield {
                "method": "custom",
                "seq": 2,
                "params": {
                    "data": {"type": "yuxi.context_compression", "summary": f"segment-{self.calls}"},
                },
            }
            yield GraphExecutionResult(checkpoint=SimpleNamespace(values=state, tasks=()), steer_before_model=self.calls == 1)

    _patch_stream_scaffolding(monkeypatch, agent=Agent())
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", AsyncMock(side_effect=["running", "completed"]))
    events = [event async for event in execute() if isinstance(event, dict)]
    assert len(events) == 4
    assert len({event["event_id"] for event in events}) == 4
    states = [event["agent_state"]["artifacts"][0] for event in events if "agent_state" in event]
    assert states == ["segment-1", "segment-2"]


@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize("defect", ["missing_checkpoint", "bad_interrupt", "save_rejected"])
async def test_execution_failure_cannot_publish_completed_result(monkeypatch, mode, defect):
    """缺 checkpoint、中断解码错误或事务拒绝都不能形成成功结果。"""
    agent = FakeAgent(missing=defect == "missing_checkpoint")
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    if defect == "bad_interrupt":
        monkeypatch.setattr(svc, "_extract_interrupts", lambda _: (_ for _ in ()).throw(ValueError("decode failed")))
    if defect == "save_rejected":
        monkeypatch.setattr(svc, "save_messages_from_langgraph_state", AsyncMock(side_effect=ValueError("binding rejected")))
    results = [event async for event in execute(mode)]
    result = results[-1]
    assert isinstance(result, RunExecutionResult) and result.status == "failed"
    assert result.committed is True
    assert result.error_type == "execution_error"
    assert not any(isinstance(e, RunExecutionResult) and e.status == "completed" for e in results)


@pytest.mark.parametrize("mode", ["chat", "resume"])
async def test_prepared_commit_precedes_graph_and_checkpoint_settlement(monkeypatch, mode):
    """图开始前提交 trace 事务，终态以独立执行结果传递。"""
    agent, db = FakeAgent(text_events()), _FakeSession()
    _patch_stream_scaffolding(monkeypatch, agent=agent)

    async def prepared():
        assert db.commit_count == 1

    events = [event async for event in execute(mode, db=db, on_prepared=prepared)]
    assert events[-1] == RunExecutionResult(checkpoint=agent.checkpoint, status="completed", committed=True)
    assert [e["delta"] for e in events if isinstance(e, dict) and e["type"].endswith("output_text.delta")] == ["hello"]
    assert "checkpoint" not in str([e for e in events if isinstance(e, dict)])
    if mode == "resume":
        assert isinstance(agent.input, Command) and agent.input.resume == {"answer": "yes"}
        assert not any(isinstance(e, dict) and "input" in e["type"] for e in events)
    else:
        assert isinstance(agent.input[0], HumanMessage)


@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize("failure", ["prepare", "commit", "cancel_commit"])
async def test_preparation_failure_rolls_back_before_graph_and_finishes_observation(monkeypatch, mode, failure):
    """准备或提交失败不得启动图，事务回滚且真实观察结束。"""
    agent = FakeAgent(text_events())
    _patch_stream_scaffolding(monkeypatch, agent=agent)

    class Transaction(_FakeSession):
        """未提交写入在失败后必须清空。"""

        def __init__(self):
            super().__init__()
            self.pending = []

        async def commit(self):
            raise asyncio.CancelledError() if failure == "cancel_commit" else RuntimeError("commit rejected")

        async def rollback(self):
            await super().rollback()
            self.pending.clear()

    class Observation:
        """保留实际观察的状态与结束结果。"""

        def __init__(self):
            self.status = None
            self.ended = False

        def update(self, *, metadata, level):
            self.status = metadata["status"]

        def end(self):
            self.ended = True

    db, observation = Transaction(), Observation()
    context = LangfuseRunContext(run_observation=observation)
    monkeypatch.setattr(svc, "_build_langfuse_run_context", lambda **kwargs: context)

    async def prepare(**kwargs):
        db.pending.append("uncommitted trace")
        if failure == "prepare":
            raise RuntimeError("preparation rejected")

    monkeypatch.setattr(svc, "_persist_agent_run_langfuse_trace", prepare)
    if failure == "cancel_commit":
        with pytest.raises(asyncio.CancelledError):
            _ = [event async for event in execute(mode, db=db)]
    else:
        events = [event async for event in execute(mode, db=db)]
        assert events[-1].status == "failed"
    assert agent.input is None
    assert db.pending == []
    assert db.rollback_count == 1
    assert observation.ended and observation.status == "failed"
    assert context.run_observation is None


async def test_execution_forwards_recorded_command_result_to_public_adapter(monkeypatch):
    """实际执行循环必须使用记录器返回的事件，不能再次投影原始 Command。"""
    consume = svc.RunMessageRecorder.consume
    command = Command(update={"messages": [ToolMessage(content="目标结果", tool_call_id="call")]})
    source = native(3, {"event": "tool-finished", "tool_call_id": "call", "output": command}, "tools")
    agent = FakeAgent(
        [
            native(0, {"event": "message-start", "id": "model"}),
            native(
                1,
                {
                    "event": "content-block-finish",
                    "content": {"type": "tool_call", "id": "call", "name": "search", "args": {}},
                },
            ),
            native(2, {"event": "tool-started", "tool_call_id": "call", "tool_name": "search", "input": {}}, "tools"),
            source,
        ]
    )
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    monkeypatch.setattr(svc.RunMessageRecorder, "consume", consume)
    monkeypatch.setattr(svc.RunMessageRecorder, "_consume_model", AsyncMock())
    monkeypatch.setattr(svc.RunMessageRecorder, "_consume_tool", AsyncMock())

    events = [event async for event in execute()]
    output = next(
        event["item"]
        for event in events
        if isinstance(event, dict)
        and event.get("item", {}).get("type") == "function_call_output"
        and event["item"]["status"] == "completed"
    )
    assert output["output"] == "目标结果" and output["call_id"] == "call"
    assert source["params"]["data"]["output"] is command
    assert events[-1].status == "completed"


async def test_multimodal_value_is_passed_to_model_without_public_init_wrapper(monkeypatch):
    """用户图片保留在真实模型输入，执行不再生成重复 init 消息。"""
    agent = FakeAgent()
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    events = [event async for event in execute(image="BASE64DATA")]
    assert "BASE64DATA" in str(agent.input[0].content)
    assert len(events) == 1 and events[0].status == "completed"


async def test_partial_failure_saves_displayed_text_and_trace_before_done(monkeypatch):
    """失败前已展示的内容先保存，失败结果不伪装为业务完成。"""
    agent = FakeAgent(text_events("partial"), error=RuntimeError("stream failed"))
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    saved = svc.save_partial_message
    incomplete = AsyncMock()
    monkeypatch.setattr(svc.OpenAIEventAdapter, "persist_incomplete", incomplete)
    events = [event async for event in execute()]
    assert saved.await_args.kwargs["full_msg"].content == "partial"
    assert saved.await_args.kwargs["trace_info"] == {"langfuse_trace_id": "trace"}
    incomplete.assert_awaited_once()
    assert events[-1].status == "failed"


@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize("close_path", ["paused_yield", "pending_next"])
@pytest.mark.parametrize(
    ("status", "worker_id", "expected_snapshot"),
    [("cancel_requested", "worker-1", True), ("running", "worker-1", False), ("cancel_requested", "other", False)],
)
async def test_cancel_close_preserves_only_owned_requested_partial(monkeypatch, mode, close_path, status, worker_id, expected_snapshot):
    """两种关闭异常保留已展示正文，普通关闭和错误 Owner 不写取消快照。"""
    import yuxi.modules.agents.services.openai_events as events_module

    waiting = asyncio.Event()
    persisted = {}

    class WaitingAgent(FakeAgent):
        """输出正文后阻塞，让关闭发生在 yield 或等待下一事件时。"""

        async def stream_messages_with_state(self, value, **kwargs):
            """使用真实协议正文并暴露确定性的等待边界。"""
            if kwargs.get("on_prepared"):
                await kwargs["on_prepared"]()
            for event in self.events:
                yield event
            waiting.set()
            await asyncio.Event().wait()

        stream_resume_with_state = stream_messages_with_state

    _patch_stream_scaffolding(monkeypatch, agent=WaitingAgent(text_events("displayed")))
    original_save = svc.OpenAIEventAdapter._save

    async def save_item(adapter, *args, **kwargs):
        item = await original_save(adapter, *args, **kwargs)
        persisted[item["id"]] = deepcopy(item)
        return item

    class ItemRepository:
        """保存独立副本，防止内存修改冒充持久化成功。"""

        def __init__(self, db):
            self.db = db

        async def save(self, *, item, **kwargs):
            """回读相同公开 item 的最终存储副本。"""
            persisted[item["id"]] = deepcopy(item)
            return deepcopy(item)

    monkeypatch.setattr(svc.OpenAIEventAdapter, "_save", save_item)
    monkeypatch.setattr(events_module, "PublicItemRepository", ItemRepository)
    monkeypatch.setattr(
        svc,
        "AgentRunRepository",
        lambda db: SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(status=status, worker_id=worker_id))),
    )
    stream = execute(mode)
    async for event in stream:
        if event.get("type") == "agent.session.turn.output_text.delta":
            assert event["delta"] == "displayed"
            break
    else:
        pytest.fail("未收到公开正文增量")

    initial = deepcopy(next(iter(persisted.values())))
    assert initial["status"] == "in_progress" and initial["content"] == []
    if close_path == "pending_next":
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(waiting.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    await stream.aclose()

    item = next(iter(persisted.values()))
    assert item["id"] == initial["id"]
    if expected_snapshot:
        assert item["status"] == "incomplete"
        assert item["content"] == [{"type": "output_text", "text": "displayed"}]
    else:
        assert item == initial


async def test_values_and_custom_events_use_explicit_extensions(monkeypatch):
    """状态白名单与压缩通知保持来源顺序，不泄漏 checkpoint 内容。"""
    events = [
        native(1, {"artifacts": ["result.txt"], "todos": [{"task": "legacy"}], "messages": ["private prompt"]}, "values"),
        native(2, {"type": "yuxi.context_compression", "status": "started"}, "custom"),
    ]
    events[0]["params"]["namespace"] = []
    agent = FakeAgent(events)
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    output = [event async for event in execute()]
    assert [event["type"] for event in output[:-1]] == [
        "yuxi.session.turn.state",
        "yuxi.session.turn.context_compression",
    ]
    assert output[0]["agent_state"]["artifacts"] == ["result.txt"]
    assert "todos" not in output[0]["agent_state"]
    assert "private prompt" not in str(output[:-1])


async def test_foreign_explicit_thread_is_not_routed_to_parent(monkeypatch):
    """不同执行 Thread 的模型事件不能进入父 Thread 输出。"""
    agent = FakeAgent(text_events("child", {"run_id": "model", "thread_id": "child"}))
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    events = [event async for event in execute()]
    assert len(events) == 1 and events[0].status == "completed"


@pytest.mark.parametrize("mode", ["chat", "resume"])
async def test_interrupt_settlement_binds_waitpoint_to_own_run(monkeypatch, mode):
    """问答等待从 checkpoint 解析并交由业务事务绑定真实 Run。"""
    state = SimpleNamespace(
        values={},
        tasks=[
            SimpleNamespace(interrupts=[SimpleNamespace(value={"questions": [{"question_id": "q", "question": "Where?"}]})], result=None)
        ],
    )
    agent = FakeAgent(checkpoint=state)
    _patch_stream_scaffolding(monkeypatch, agent=agent)
    saved = AsyncMock(return_value="interrupted")
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", saved)
    events = [event async for event in execute(mode)]
    assert events[-1].status == "interrupted" and events[-1].committed
    assert saved.await_args.kwargs["waitpoint"]["run_id"] == "run-1"
    assert saved.await_args.kwargs["waitpoint"]["kind"] == "answer"


async def test_slow_langfuse_flush_does_not_hold_run_completion(monkeypatch):
    """可选观测网络阻塞时，Run 收尾须在短时限内返回。"""
    release = threading.Event()
    monkeypatch.setattr(svc, "flush_langfuse", lambda: release.wait(1))
    started = asyncio.get_running_loop().time()
    try:
        await svc._flush_langfuse_best_effort(timeout=0.01)
        assert asyncio.get_running_loop().time() - started < 0.1
    finally:
        release.set()


@pytest.mark.parametrize("mode", ["chat", "resume"])
async def test_service_consumer_cancel_closes_real_graph(monkeypatch, mode):
    """真实图经 chat/resume 与 Worker 多层流后，消费侧取消仍关闭执行。"""
    from contextlib import aclosing
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import interrupt
    from yuxi.modules.agents.runtime.base import BaseAgent
    from yuxi.modules.agents.services.runner import RunContext, _consume_stream_with_cancel

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
        meta={"turn_id": "turn-1", "run_id": "run-1", "worker_id": "worker-1", "input_id": "input-1"},
        current_user=SimpleNamespace(uid="user-1"),
        db=_FakeSession(),
    )
    stream = (
        svc.stream_agent_execution(
            prepared_execution=prepared_execution(),
            **kwargs,
            agent_slug="test-agent",
            input_messages=[build_chat_input_message("hello").with_metadata({"message_id": 1})],
        )
        if mode == "chat"
        else svc.stream_agent_execution(
            is_resume=True,
            prepared_execution=prepared_execution(),
            **kwargs,
            resume_input="continue",
        )
    )

    async def consume():
        """精确在真实节点产生的事件处停止消费。"""
        async with aclosing(_consume_stream_with_cancel(stream, RunContext("run", "owner"))) as chunks:
            async for chunk in chunks:
                if isinstance(chunk, dict) and chunk["type"] == "yuxi.session.turn.context_compression":
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


@pytest.mark.asyncio
async def test_disabled_langfuse_does_not_write_or_own_transaction(monkeypatch: pytest.MonkeyPatch):
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


@pytest.mark.parametrize("mode", ["chat", "resume"])
@pytest.mark.parametrize(
    ("thread_id", "meta"),
    [
        (None, {"turn_id": "turn-1", "run_id": "run-1"}),
        ("", {"turn_id": "turn-1", "run_id": "run-1"}),
        ("thread-1", {}),
        ("thread-1", {"turn_id": "", "run_id": "run-1"}),
        ("thread-1", {"turn_id": "turn-1", "run_id": ""}),
    ],
)
async def test_execution_rejects_missing_persisted_identity(mode, thread_id, meta):
    """执行入口在任何数据库或模型动作前拒绝缺失的持久归属。"""
    kwargs = dict(
        thread_id=thread_id,
        meta=meta,
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
        prepared_execution=prepared_execution(),
    )
    stream = (
        svc.stream_agent_execution(**kwargs, agent_slug="test-agent", input_messages=[build_chat_input_message("hello")])
        if mode == "chat"
        else svc.stream_agent_execution(is_resume=True, **kwargs, resume_input={"answer": "ok"})
    )
    with pytest.raises(ValueError, match="执行需要已持久化的 Thread、Turn 和 Run"):
        await anext(stream)
    await stream.aclose()
