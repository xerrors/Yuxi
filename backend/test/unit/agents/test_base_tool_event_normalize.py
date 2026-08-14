from __future__ import annotations

from langchain_core.messages import ToolMessage
from langgraph.types import Command
import pytest

from yuxi.agents.base import _json_safe, _normalize_tool_event_data


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

