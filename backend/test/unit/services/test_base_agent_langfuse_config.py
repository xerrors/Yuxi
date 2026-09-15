from __future__ import annotations

from yuxi.agents.context import BaseContext

from contextlib import aclosing
from types import SimpleNamespace

import pytest

from yuxi.agents.base import BaseAgent


class _FakeGraph:
    def __init__(self):
        self.last_stream_config = None
        self.last_invoke_config = None

    async def astream(self, payload, *, stream_mode, context, config):
        self.last_stream_config = config
        yield SimpleNamespace(model_dump=lambda: {"type": "ai"}), {"node": "llm"}

    async def ainvoke(self, payload, *, context, config):
        self.last_invoke_config = config
        return {"messages": []}


class _LifecycleGraph:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle

    async def aget_state(self, config):
        """最终状态只在流耗尽后读取。"""
        self.lifecycle.append("checkpoint")
        return {"messages": []}

    async def astream_events(self, *_args, **_kwargs):
        self.lifecycle.append("stream-created")

        async def events():
            self.lifecycle.append("first-event")
            yield {"method": "values", "params": {"namespace": [], "data": {}}}

        return aclosing(events())


class _TestAgent(BaseAgent):
    name = "test_agent"
    description = "test"

    async def get_graph(self, **kwargs):
        if getattr(self, "_graph", None) is None:
            self._graph = _FakeGraph()
        return self._graph


_TestAgent.__module__ = "yuxi.agents.tests.fake"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream", "invoke"])
async def test_base_agent_passes_callbacks_metadata_and_tags(mode):
    agent = _TestAgent()

    if mode == "stream":
        items = []
        async for item in agent.stream_messages(
            ["hello"],
            context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1"}),
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": "user-1"},
            tags=["yuxi"],
        ):
            items.append(item)
        assert len(items) == 1
        config_attr = "last_stream_config"
    else:
        await agent.invoke_messages(
            ["hello"],
            context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1"}),
            callbacks=["handler-1"],
            metadata={"langfuse_user_id": "user-1"},
            tags=["yuxi"],
        )
        config_attr = "last_invoke_config"

    graph = await agent.get_graph()
    assert getattr(graph, config_attr) == {
        "configurable": {"thread_id": "thread-1", "uid": "user-1"},
        "recursion_limit": 300,
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1"},
        "tags": ["yuxi"],
    }


@pytest.mark.asyncio
async def test_base_agent_uses_configured_max_execution_steps():
    agent = _TestAgent()

    await agent.invoke_messages(
        ["hello"],
        context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1", "max_execution_steps": 42}),
    )

    graph = await agent.get_graph()
    assert graph.last_invoke_config["recursion_limit"] == 42


@pytest.mark.asyncio
async def test_base_agent_records_prepared_after_stream_creation_before_first_event():
    lifecycle = []

    class LifecycleAgent(_TestAgent):
        async def get_graph(self, **kwargs):
            lifecycle.append("graph-ready")
            return _LifecycleGraph(lifecycle)

    async def on_prepared():
        lifecycle.append("prepared")

    agent = LifecycleAgent()
    events = []
    async for event in agent.stream_messages_with_state(
        ["hello"],
        context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1"}),
        on_prepared=on_prepared,
    ):
        events.append(event)

    assert events == [("values", {}), ("checkpoint", {"messages": []})]
    assert lifecycle == ["graph-ready", "stream-created", "prepared", "first-event", "checkpoint"]
