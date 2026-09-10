"""invalid_tool_call 窄方案：在 ChatCompletionsAdapter 发送边界净化 wire 载荷。

langchain_openai 把 AIMessage.invalid_tool_calls 序列化成 type:"function"、arguments
为截断 JSON 的 tool_call，DeepSeek 等接口因此报参数解析失败。窄方案在
ChatCompletionsAdapter._get_request_payload 里按 id 移除这些截断调用，并在 content
里给模型明确的 text 反馈，覆盖同步/异步/流式/非流式四条路径；不改 Anthropic/Gemini，
不删孤儿 ToolMessage（按根因单独处理），原始 checkpoint 消息对象不动。
"""

from __future__ import annotations

import json
import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage, InvalidToolCall
from pydantic import SecretStr

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.models.chat import ChatCompletionsAdapter, _sanitize_wire_invalid_tool_calls


def _adapter() -> ChatCompletionsAdapter:
    return ChatCompletionsAdapter(
        model="deepseek-chat",
        api_key=SecretStr("test-key"),
        base_url="http://test.local/v1",
    )


def _invalid_call() -> InvalidToolCall:
    return InvalidToolCall(name="kbs_search", args='{"query":', id="call-bad", error="Unterminated string")


def _messages_with_invalid() -> list:
    return [
        HumanMessage(content="hi"),
        AIMessage(content="", invalid_tool_calls=[_invalid_call()]),
    ]


# ---- 单元：wire 净化函数 ----


def test_pure_invalid_call_is_removed_and_feedback_added():
    wire = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"type": "function", "id": "call-bad", "function": {"name": "kbs_search", "arguments": '{"query":'}}
            ],
        },
    ]
    originals = [
        HumanMessage(content="hi"),
        AIMessage(content="", invalid_tool_calls=[_invalid_call()]),
    ]

    _sanitize_wire_invalid_tool_calls(wire, originals)

    assert "tool_calls" not in wire[1]
    assert wire[1]["content"] == [{"type": "text", "text": "[工具调用失败] kbs_search: Unterminated string"}]


def test_mixed_keeps_valid_call_and_adds_feedback():
    wire = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "id": "call-good",
                    "function": {"name": "kbs_search", "arguments": '{"query": "ok"}'},
                },
                {"type": "function", "id": "call-bad", "function": {"name": "kbs_search", "arguments": '{"query":'}},
            ],
        },
    ]
    originals = [
        AIMessage(
            content="",
            tool_calls=[{"id": "call-good", "name": "kbs_search", "args": {"query": "ok"}, "type": "tool_call"}],
            invalid_tool_calls=[_invalid_call()],
        ),
    ]

    _sanitize_wire_invalid_tool_calls(wire, originals)

    assert [call["id"] for call in wire[0]["tool_calls"]] == ["call-good"]
    assert wire[0]["content"] == [{"type": "text", "text": "[工具调用失败] kbs_search: Unterminated string"}]


def test_no_invalid_call_leaves_wire_unchanged():
    wire = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "id": "call-good",
                    "function": {"name": "kbs_search", "arguments": '{"query": "ok"}'},
                }
            ],
        }
    ]
    originals = [
        AIMessage(
            content="",
            tool_calls=[{"id": "call-good", "name": "kbs_search", "args": {"query": "ok"}, "type": "tool_call"}],
        ),
    ]
    snapshot = json.loads(json.dumps(wire))

    _sanitize_wire_invalid_tool_calls(wire, originals)

    assert wire == snapshot


# ---- 集成：真实 adapter + mock HTTP，捕获请求体 ----


def _assert_body_has_no_invalid_tool_call(request) -> None:
    body = json.loads(request.content)
    assistant = body["messages"][1]
    # 截断 function 已移除，换成 text 反馈
    assert "tool_calls" not in assistant
    assert assistant["content"] == [{"type": "text", "text": "[工具调用失败] kbs_search: Unterminated string"}]


def _ok_response() -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _stream_response() -> str:
    return 'data: {"choices":[{"delta":{"content":"ok"},"index":0}]}\n\ndata: [DONE]\n\n'


def test_invoke_sanitizes_request_body(httpx_mock):
    httpx_mock.add_response(url="http://test.local/v1/chat/completions", json=_ok_response())

    _adapter().invoke(_messages_with_invalid())

    _assert_body_has_no_invalid_tool_call(httpx_mock.get_request())


@pytest.mark.asyncio
async def test_ainvoke_sanitizes_request_body(httpx_mock):
    httpx_mock.add_response(url="http://test.local/v1/chat/completions", json=_ok_response())

    await _adapter().ainvoke(_messages_with_invalid())

    _assert_body_has_no_invalid_tool_call(httpx_mock.get_request())


def test_stream_sanitizes_request_body(httpx_mock):
    httpx_mock.add_response(url="http://test.local/v1/chat/completions", text=_stream_response())

    list(_adapter().stream(_messages_with_invalid()))

    _assert_body_has_no_invalid_tool_call(httpx_mock.get_request())


@pytest.mark.asyncio
async def test_astream_sanitizes_request_body(httpx_mock):
    httpx_mock.add_response(url="http://test.local/v1/chat/completions", text=_stream_response())

    async for _ in _adapter().astream(_messages_with_invalid()):
        pass

    _assert_body_has_no_invalid_tool_call(httpx_mock.get_request())
