"""invalid_tool_call 窄方案：在 ChatCompletionsAdapter 发送边界净化 wire 载荷。

langchain_openai 把 AIMessage.invalid_tool_calls 序列化成 type:"function"、arguments
为截断 JSON 的 tool_call，DeepSeek 等接口因此报参数解析失败。窄方案在
ChatCompletionsAdapter._get_request_payload 里按 id 移除这些截断调用，并在 content
里给模型明确的 text 反馈，覆盖同步/异步/流式/非流式四条路径；不改 Anthropic/Gemini，
仅过滤同批已移除无效调用的补齐结果，保留真实孤儿 ToolMessage，原始 checkpoint 消息对象不动。
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, InvalidToolCall, SystemMessage, ToolMessage
from pydantic import SecretStr


def _adapter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from yuxi.modules.models.chat import ChatCompletionsAdapter

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
        ToolMessage(content="patched error", tool_call_id="call-bad"),
    ]


# ---- 单元：wire 净化函数 ----


def test_pure_invalid_call_is_removed_and_feedback_added(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from yuxi.modules.models.chat import _sanitize_wire_invalid_tool_calls

    wire = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"type": "function", "id": "call-bad", "function": {"name": "kbs_search", "arguments": '{"query":'}}],
        },
    ]
    originals = [
        HumanMessage(content="hi"),
        AIMessage(content="", invalid_tool_calls=[_invalid_call()]),
    ]

    _sanitize_wire_invalid_tool_calls(wire, originals)

    assert "tool_calls" not in wire[1]
    assert wire[1]["content"] == [{"type": "text", "text": "[工具调用失败] kbs_search: Unterminated string"}]


def test_mixed_keeps_valid_call_and_adds_feedback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from yuxi.modules.models.chat import _sanitize_wire_invalid_tool_calls

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


def test_no_invalid_call_leaves_wire_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from yuxi.modules.models.chat import _sanitize_wire_invalid_tool_calls

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


async def test_streamed_invalid_tool_chunk_is_absent_from_followup_body(httpx_mock, monkeypatch):
    """流式调用的截断工具片保留在内部消息，续答请求只发送错误说明。"""
    model = _adapter(monkeypatch)
    delta = {
        "role": "assistant",
        "tool_calls": [{"index": 0, "id": "call-bad", "type": "function", "function": {"name": "kbs_search", "arguments": "not-json"}}],
    }
    event = {"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls"}]}
    httpx_mock.add_response(text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    httpx_mock.add_response(json=_ok_response())

    user = HumanMessage("hi")
    chunks = [part async for part in model.astream([user])]
    result = chunks[0]
    for part in chunks[1:]:
        result += part
    assert result.invalid_tool_calls[0]["id"] == "call-bad"
    assert (await model.ainvoke([user, result, ToolMessage("patched error", tool_call_id="call-bad")])).text == "ok"

    wire = json.loads(httpx_mock.get_requests()[-1].content)["messages"]
    assert len(wire) == 2
    assert "tool_calls" not in wire[1]
    assert [part["type"] for part in wire[1]["content"]] == ["text"]
    assert "[工具调用失败] kbs_search" in wire[1]["content"][0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["invoke", "ainvoke", "stream", "astream"])
async def test_adapter_entrypoints_sanitize_request_body(httpx_mock, monkeypatch, entrypoint):
    adapter = _adapter(monkeypatch)
    if entrypoint in {"stream", "astream"}:
        httpx_mock.add_response(url="http://test.local/v1/chat/completions", text=_stream_response())
    else:
        httpx_mock.add_response(url="http://test.local/v1/chat/completions", json=_ok_response())

    if entrypoint == "invoke":
        adapter.invoke(_messages_with_invalid())
    elif entrypoint == "ainvoke":
        await adapter.ainvoke(_messages_with_invalid())
    elif entrypoint == "stream":
        list(adapter.stream(_messages_with_invalid()))
    else:
        async for _ in adapter.astream(_messages_with_invalid()):
            pass

    _assert_body_has_no_invalid_tool_call(httpx_mock.get_request())


@pytest.mark.parametrize("reasoning", [False, True])
def test_patched_invalid_result_is_removed_without_losing_valid_pairs(monkeypatch, reasoning):
    """补齐的无效结果与被删除调用一起过滤，合法结果及后续复用 ID 保留。"""
    from copy import deepcopy

    adapter = _adapter(monkeypatch)
    adapter.replay_reasoning = reasoning
    original = AIMessage(
        content="",
        tool_calls=[{"id": "good", "name": "read", "args": {}}],
        invalid_tool_calls=[_invalid_call()],
        additional_kwargs={"reasoning_content": "原始推理"},
    )
    messages = [
        HumanMessage(content="hi"),
        original,
        ToolMessage(content="invalid result", tool_call_id="call-bad"),
        ToolMessage(content="valid result", tool_call_id="good"),
        AIMessage(content="", tool_calls=[{"id": "call-bad", "name": "read", "args": {}}]),
        ToolMessage(content="reused valid result", tool_call_id="call-bad"),
        ToolMessage(content="unrelated orphan", tool_call_id="orphan"),
        HumanMessage(content="继续"),
    ]
    before = deepcopy(messages)
    payload = adapter._get_request_payload(messages)["messages"]
    assert [m["content"] for m in payload if m["role"] == "tool"] == ["valid result", "reused valid result", "unrelated orphan"]
    assert [c["id"] for c in payload[1]["tool_calls"]] == ["good"]
    assert "工具调用失败" in str(payload[1]["content"])
    if reasoning:
        assert payload[1]["reasoning_content"] == "原始推理"
    assert messages == before


@pytest.mark.parametrize("boundary", [HumanMessage(content="下一轮"), SystemMessage(content="边界")])
def test_invalid_call_filter_stops_at_non_tool_boundary(monkeypatch, boundary):
    """同 ID 出现在用户或系统消息之后的孤儿结果不能被静默删除。"""

    payload = _adapter(monkeypatch)._get_request_payload(
        [
            AIMessage(content="", invalid_tool_calls=[_invalid_call()]),
            boundary,
            ToolMessage(content="true orphan", tool_call_id="call-bad"),
        ]
    )
    assert payload["messages"][-1] == {"role": "tool", "content": "true orphan", "tool_call_id": "call-bad"}
