"""使用真实 LangGraph 节点验证消费者退出时的执行关闭链。"""

import asyncio

import pytest
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph

from yuxi.agents.base import BaseAgent
from yuxi.agents.context import BaseContext
from yuxi.services.run_worker import RunContext, _consume_stream_with_cancel
from contextlib import aclosing


@pytest.mark.asyncio
async def test_consumer_body_cancel_stops_real_graph_node(monkeypatch):
    """消费者处理事件时被取消，真实后台节点也必须在 owner 退出前关闭。"""
    entered, release, closed, consuming = (asyncio.Event() for _ in range(4))
    effects, runs = [], []

    async def node(state):
        """发出事件后等待，后续副作用不能在取消后出现。"""
        try:
            entered.set()
            get_stream_writer()({"stage": "waiting"})
            await release.wait()
            effects.append("late side effect")
            return {"messages": []}
        finally:
            closed.set()

    builder = StateGraph(MessagesState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile()
    original = graph.astream_events

    async def track_run(*args, **kwargs):
        """只保留真实 Run 引用用于负向变异后的独立清理。"""
        run = await original(*args, **kwargs)
        runs.append(run)
        return run

    monkeypatch.setattr(graph, "astream_events", track_run)

    execution_context = BaseContext(thread_id="prepared-thread", uid="prepared-user")

    class Agent(BaseAgent):
        """执行真实图，不使用模型或数据库。"""

        async def get_graph(self, **kwargs):
            """返回本测试执行图。"""
            assert kwargs["context"] is execution_context
            return graph

    async def consume():
        """将取消落在处理事件的消费者，而非图的迭代调用中。"""
        stream = Agent().stream_messages_with_state(["hello"], context=execution_context)
        async with aclosing(_consume_stream_with_cancel(stream, RunContext("run", "owner"))) as chunks:
            async for mode, _ in chunks:
                if mode == "custom":
                    consuming.set()
                    await asyncio.Event().wait()

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(consuming.wait(), 2)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, 2)
        assert closed.is_set(), "owner 已退出但真实图节点仍在运行"
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
async def test_stream_rejects_legacy_dictionary_input():
    """显式Context不能与旧字典双输入共存。"""

    class Agent(BaseAgent):
        """只验证入口拒绝，不创建执行图。"""

        async def get_graph(self, **kwargs):
            raise AssertionError("旧输入必须在构图前拒绝")

    with pytest.raises(TypeError, match="input_context"):
        async for _ in Agent().stream_messages_with_state([], context=BaseContext(), input_context={}):
            pass
    with pytest.raises(TypeError, match="context"):
        async for _ in Agent().stream_messages_with_state([]):
            pass
