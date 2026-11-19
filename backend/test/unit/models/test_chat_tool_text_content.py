"""工具文本块在 Chat Completions wire 边界发送为字符串，原始消息保持不变。"""

from copy import deepcopy
import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yuxi.modules.models.chat import ChatCompletionsAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("reasoning", [False, True])
async def test_text_tool_blocks_work_with_string_only_provider(httpx_mock, entrypoint, reasoning):
    """真实 adapter 的四条发送路径都满足供应商工具字符串约束，并保留 checkpoint。"""
    model = ChatCompletionsAdapter(model="test-model", api_key="test-key", base_url="http://test.local/v1", replay_reasoning=reasoning)
    messages = [
        HumanMessage(content="查询本人考勤"),
        AIMessage(content="", tool_calls=[{"id": "attendance", "name": "query_my_attendance", "args": {}}]),
        ToolMessage(
            content=[{"type": "text", "text": '{"records": [{"status": "年假"}]}'}, {"type": "text", "text": "查询完成"}],
            tool_call_id="attendance",
            name="query_my_attendance",
            artifact={"records": [{"status": "年假"}]},
        ),
    ]
    original = deepcopy(messages)

    def provider(request):
        """供应商独立拒绝数组工具消息，避免宽松 replay 掩盖真实兼容问题。"""
        body = json.loads(request.content)
        tool = next(message for message in body["messages"] if message["role"] == "tool")
        if not isinstance(tool["content"], str):
            return httpx.Response(
                400, json={"error": {"message": "tool message content must be a string", "type": "invalid_request_error"}}
            )
        assert tool["content"] == '{"records": [{"status": "年假"}]}\n查询完成'
        assert tool["tool_call_id"] == "attendance"
        if body.get("stream"):
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"已查到年假"},"index":0}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "已查到年假"}}]})

    httpx_mock.add_callback(provider)
    if entrypoint == "invoke":
        output = model.invoke(messages).text
    elif entrypoint == "ainvoke":
        output = (await model.ainvoke(messages)).text
    elif entrypoint == "stream":
        output = "".join(chunk.text for chunk in model.stream(messages))
    else:
        output = "".join([chunk.text async for chunk in model.astream(messages)])
    assert output == "已查到年假"
    assert messages == original


def test_tool_text_normalization_preserves_user_multimodal_and_existing_strings():
    """只转换 tool 的纯文本数组，用户多模态块和已有文本按原协议发送。"""
    model = ChatCompletionsAdapter(model="test-model", api_key="test-key")
    user = HumanMessage(
        content=[{"type": "text", "text": "图片"}, {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]
    )
    tool = ToolMessage(content="原始文本", tool_call_id="text")
    empty = ToolMessage(content=[], tool_call_id="empty")
    messages = [user, tool, empty]
    original = deepcopy(messages)
    wire = model._get_request_payload(messages)["messages"]
    assert wire[0]["content"] == user.content
    assert wire[1]["content"] == "原始文本"
    assert wire[2]["content"] == ""
    assert messages == original
