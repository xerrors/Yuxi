"""回归测试：流式 tool_call 续片空串 name/id 归一化，规避 LangGraph v3 累积缺陷。

背景：v3 流式累积对 tool_call 字段是“后值覆盖”，部分 OpenAI 兼容提供商
（siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片的
真实值（丢 name / 丢 id），导致工具结果无法按 tool_call_id 关联。
`_normalize_tool_call_chunks` 把空串归一化为 None（对齐 OpenAI 官方）来规避。

本测试用 fake 流式模型确定性复现该缺陷（无需网络/API key），并验证修复有效。
"""

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from yuxi.agents.models import _normalize_tool_call_chunks


@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气。"""
    return f"{city} 晴 25℃"


class _FakeSiliconFlowModel(BaseChatModel):
    """模拟 SiliconFlow 流式：首片带 name+id，续片 name=''（空串）。

    `apply_fix=True` 时在续片产出后调用 `_normalize_tool_call_chunks`，
    复刻 `_ToolCallChunkFixChatOpenAI` 的归一化行为。
    """

    apply_fix: bool = False
    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-siliconflow"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.call_count += 1
        call_id = f"call_{self.call_count}"
        deltas = [
            tool_call_chunk(name="get_weather", args="", id=call_id, index=0),
            tool_call_chunk(name="", args='{"city": ', id=None, index=0),
            tool_call_chunk(name="", args='"北京"}', id=None, index=0),
        ]
        for delta in deltas:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[delta]))
            if self.apply_fix:
                _normalize_tool_call_chunks(chunk.message)
            yield chunk

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        raise NotImplementedError("仅用于流式测试")


async def _run_and_get_tool_calls(model: BaseChatModel) -> list[dict]:
    agent = create_agent(model=model, tools=[get_weather], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}, "recursion_limit": 4}
    graph_input = {"messages": [HumanMessage("北京天气？")]}
    try:
        run = await agent.astream_events(graph_input, config=config, version="v3")
        async for _ in run:
            pass
    except GraphRecursionError:
        pass  # name 丢失会导致死循环，这里只取已落到 state 的 tool_call
    state = await agent.aget_state(config)
    tool_calls: list[dict] = []
    for msg in state.values.get("messages", []):
        if msg.type == "ai" and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    return tool_calls


def test_normalize_replaces_empty_string_with_none():
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            tool_call_chunk(name="", args="{}", id="", index=0),
            tool_call_chunk(name="foo", args="{}", id="abc", index=1),
        ],
    )
    _normalize_tool_call_chunks(msg)
    assert msg.tool_call_chunks[0]["name"] is None
    assert msg.tool_call_chunks[0]["id"] is None
    # 非空值保持不变
    assert msg.tool_call_chunks[1]["name"] == "foo"
    assert msg.tool_call_chunks[1]["id"] == "abc"


async def test_v3_loses_name_without_fix():
    """对照组：复现上游缺陷——不归一化时 v3 累积出的 tool_call 真实 name 被空串覆盖。"""
    tool_calls = await _run_and_get_tool_calls(_FakeSiliconFlowModel(apply_fix=False))
    assert tool_calls, "应至少累积出一个 tool_call"
    assert tool_calls[0]["name"] == "", "未修复时首片真实 name 应被续片空串覆盖"


async def test_v3_preserves_name_with_fix():
    """修复组：归一化空串后 v3 累积出的 tool_call 保留完整 name/id 与参数。"""
    tool_calls = await _run_and_get_tool_calls(_FakeSiliconFlowModel(apply_fix=True))
    assert tool_calls, "应至少累积出一个 tool_call"
    assert all(tc["name"] == "get_weather" for tc in tool_calls)
    assert all(tc["id"] for tc in tool_calls)
    assert tool_calls[0]["args"] == {"city": "北京"}
