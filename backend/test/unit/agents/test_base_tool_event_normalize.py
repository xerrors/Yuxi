from __future__ import annotations

from yuxi.agents.context import BaseContext

import asyncio
from contextlib import aclosing

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command

from yuxi.agents.base import BaseAgent, _json_safe, _normalize_tool_event_data


@pytest.mark.asyncio
async def test_final_checkpoint_belongs_to_each_executed_graph():
    """共享 Agent 的并发执行必须回读各自图，不能重建或共享可变缓存。"""

    class Graph:
        """状态只由本图的执行写入，未执行的新图读不到结果。"""

        state = None

        async def astream_events(self, graph_input, **kwargs):
            """交错执行两个用户，模拟最终持久状态。"""

            async def events():
                await asyncio.sleep(0)
                self.state = (kwargs["config"]["configurable"], graph_input["messages"])
                if False:
                    yield None

            return aclosing(events())

        async def aget_state(self, config):
            """校验读取者没有改用相邻 Thread。"""
            assert self.state is not None, "收尾重新构图，丢失本次执行结果"
            assert self.state[0] == config["configurable"]
            return self.state

    class Agent(BaseAgent):
        """注册表中的同一 Agent 服务不同用户。"""

        async def get_graph(self, **kwargs):
            """每轮返回独立图。"""
            return Graph()

    agent = Agent()

    async def run(uid):
        """耗尽后保留唯一的最终 checkpoint。"""
        return [
            event
            async for event in agent.stream_messages_with_state(
                [uid], context=BaseContext(**{"uid": uid, "thread_id": uid + "-thread"})
            )
        ]

    results = await asyncio.gather(run("a"), run("b"))
    for uid, events in zip(("a", "b"), results, strict=True):
        assert events == [("checkpoint", ({"uid": uid, "thread_id": uid + "-thread"}, [uid]))]


def _command_tool_finished(tool_call_id: str) -> dict:
    """模拟 write_todos / task 这类返回 Command 的工具的 tool-finished 事件。"""
    tool_message = ToolMessage(
        content="Updated todo list to [{'content': '步骤一', 'status': 'in_progress'}]",
        tool_call_id=tool_call_id,
    )
    command = Command(update={"todos": [{"content": "步骤一", "status": "in_progress"}], "messages": [tool_message]})
    return {"event": "tool-finished", "tool_call_id": tool_call_id, "output": command}


def test_command_tool_finished_extracts_tool_message_for_frontend_association():
    tool_call_id = "call_abc"
    data = _normalize_tool_event_data(_command_tool_finished(tool_call_id))
    safe = _json_safe(data)
    output = safe["output"]

    # 前端按 tool_call_id 关联结果，并要求 output 是对象（dict），否则会被丢弃。
    assert isinstance(output, dict)
    assert output["tool_call_id"] == tool_call_id
    assert output["type"] == "tool"
    assert "步骤一" in output["content"]


def test_command_tool_finished_prefers_message_matching_tool_call_id():
    other = ToolMessage(content="别的工具结果", tool_call_id="call_other")
    target = ToolMessage(content="目标结果", tool_call_id="call_target")
    data = {
        "event": "tool-finished",
        "tool_call_id": "call_target",
        "output": Command(update={"messages": [other, target]}),
    }

    output = _normalize_tool_event_data(data)["output"]
    assert isinstance(output, ToolMessage)
    assert output.tool_call_id == "call_target"
    assert output.content == "目标结果"


@pytest.mark.parametrize(
    "data",
    [
        {"event": "tool-finished", "tool_call_id": "call_x", "output": {"content": "plain", "type": "tool"}},
        {"event": "tool-started", "tool_call_id": "call_x", "output": None},
        {
            "event": "tool-finished",
            "tool_call_id": "call_x",
            "output": Command(update={"todos": [{"content": "无消息", "status": "pending"}]}),
        },
    ],
)
def test_untouched_tool_event_data_is_returned_as_is(data):
    assert _normalize_tool_event_data(data) is data


@pytest.mark.asyncio
async def test_stream_with_state_preserves_protocol_sequence_and_timestamp():
    """Model/Tool 生命周期转换不得丢失 StreamMux 顺序与观察时间。"""

    class FakeGraph:
        async def aget_state(self, config):
            """完成流后读取同一图的 checkpoint。"""
            return {"checkpoint": config["configurable"]}

        async def astream_events(self, *_args, **_kwargs):
            async def events():
                yield {
                    "seq": 7,
                    "method": "messages",
                    "params": {
                        "timestamp": 1_777_000_123_456,
                        "namespace": ["model:abc"],
                        "data": (AIMessageChunk(content="hello"), {"node": "model"}),
                    },
                }
                yield {
                    "seq": 8,
                    "method": "tools",
                    "params": {
                        "timestamp": 1_777_000_123_789,
                        "namespace": ["tools:abc"],
                        "data": {"event": "tool-started", "tool_call_id": "call-1"},
                    },
                }

            return aclosing(events())

    class FakeAgent(BaseAgent):
        async def get_graph(self, *, context=None):
            del context
            return FakeGraph()

    events = [
        event
        async for event in FakeAgent().stream_messages_with_state(
            ["hello"],
            context=BaseContext(**{"thread_id": "thread-1", "uid": "user-1"}),
        )
    ]

    mode, (_message, metadata) = events[0]
    assert events[-1] == ("checkpoint", {"checkpoint": {"thread_id": "thread-1", "uid": "user-1"}})
    assert mode == "messages"
    assert metadata["stream_event"] == {
        "method": "messages",
        "namespace": ["model:abc"],
        "seq": 7,
        "timestamp": 1_777_000_123_456,
    }
    assert events[1] == (
        "stream_event",
        {
            "method": "tools",
            "namespace": ["tools:abc"],
            "seq": 8,
            "timestamp": 1_777_000_123_789,
            "data": {"event": "tool-started", "tool_call_id": "call-1"},
        },
    )
