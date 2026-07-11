from __future__ import annotations

import pytest

from yuxi.agents.base import BaseAgent


@pytest.mark.asyncio
async def test_stream_messages_with_state_merges_state_updates_into_graph_input():
    captured: dict = {}

    class CaptureAgent(BaseAgent):
        async def _stream_input_with_state(self, graph_input, input_context=None, **kwargs):
            captured["graph_input"] = graph_input
            captured["input_context"] = input_context
            captured["kwargs"] = kwargs
            yield "values", graph_input

    agent = object.__new__(CaptureAgent)
    uploads = [{"path": "/home/gem/user-data/uploads/image.jpg"}]

    events = [
        event
        async for event in agent.stream_messages_with_state(
            ["hello"],
            input_context={"thread_id": "thread-1"},
            state_updates={"uploads": uploads},
            callbacks=["callback-1"],
        )
    ]

    assert captured["graph_input"] == {"messages": ["hello"], "uploads": uploads}
    assert captured["input_context"] == {"thread_id": "thread-1"}
    assert captured["kwargs"] == {"callbacks": ["callback-1"]}
    assert events == [("values", {"messages": ["hello"], "uploads": uploads})]
