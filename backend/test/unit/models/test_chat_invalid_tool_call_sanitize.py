"""invalid_tool_call 净化的 wire payload 回归测试。

净化必须落在「真正发给供应商的载荷」上：只清空解析字段(invalid_tool_calls)不够，
langchain_openai 在 tool_calls 与 invalid_tool_calls 都为空时会回退使用
additional_kwargs["tool_calls"]，把截断参数的调用原样重发。
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai.chat_models.base import _convert_message_to_dict

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.models.chat import _sanitize_invalid_tool_calls


def _malformed_raw_call(call_id: str = "call-bad") -> dict:
    """OpenAI 响应里的原始工具调用：arguments 被截断。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "kbs_search", "arguments": '{"query":'},
    }


def _valid_raw_call(call_id: str = "call-good") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "kbs_search", "arguments": '{"query": "ok"}'},
    }


def _wire_tool_calls(messages) -> list[dict]:
    """把消息翻译成供应商实际收到的 tool_calls 载荷。"""
    payloads = [_convert_message_to_dict(message) for message in messages]
    return [call for payload in payloads for call in (payload.get("tool_calls") or [])]


def test_pure_invalid_call_is_not_resent_on_the_wire():
    """只有无效调用时，净化后不得再发出该调用（否则请求有 tool_calls 却没有工具响应）。"""
    message = AIMessage(
        content="",
        additional_kwargs={"tool_calls": [_malformed_raw_call()]},
        invalid_tool_calls=[
            {"id": "call-bad", "name": "kbs_search", "args": '{"query":', "error": "Unterminated string"}
        ],
    )

    sanitized = _sanitize_invalid_tool_calls([message])

    assert _wire_tool_calls(sanitized) == []
    assert sanitized[0].invalid_tool_calls == []


def test_mixed_valid_and_invalid_keeps_only_valid_call_on_the_wire():
    """有效/无效混合时，wire payload 只保留有效调用。"""
    message = AIMessage(
        content="",
        # 真实解析结果：合法调用进入 tool_calls，原始 wire 表示完整保留在 additional_kwargs。
        tool_calls=[{"id": "call-good", "name": "kbs_search", "args": {"query": "ok"}, "type": "tool_call"}],
        additional_kwargs={"tool_calls": [_malformed_raw_call(), _valid_raw_call()]},
        invalid_tool_calls=[
            {"id": "call-bad", "name": "kbs_search", "args": '{"query":', "error": "Unterminated string"}
        ],
    )

    sanitized = _sanitize_invalid_tool_calls([message])

    wire_calls = _wire_tool_calls(sanitized)
    assert [call["id"] for call in wire_calls] == ["call-good"]
    assert wire_calls[0]["function"]["arguments"] == '{"query": "ok"}'


def test_sanitize_does_not_mutate_shared_state_messages():
    """不得原地修改入参：这些消息来自共享 graph state / checkpoint。"""
    raw_calls = [_malformed_raw_call()]
    message = AIMessage(
        content="",
        additional_kwargs={"tool_calls": raw_calls},
        invalid_tool_calls=[{"id": "call-bad", "name": "kbs_search", "args": '{"query":', "error": "bad"}],
    )

    sanitized = _sanitize_invalid_tool_calls([message])

    assert sanitized[0] is not message
    assert message.invalid_tool_calls  # 原始对象保持不变
    assert message.additional_kwargs["tool_calls"] == raw_calls


def test_orphan_tool_message_is_dropped_with_its_call():
    """无效调用的配对被删除后，请求里不得残留孤儿 ToolMessage。"""
    assistant = AIMessage(
        content="",
        additional_kwargs={"tool_calls": [_malformed_raw_call()]},
        invalid_tool_calls=[{"id": "call-bad", "name": "kbs_search", "args": '{"query":', "error": "bad"}],
    )
    orphan = ToolMessage(content="tool ran anyway", tool_call_id="call-bad")

    sanitized = _sanitize_invalid_tool_calls([HumanMessage(content="hi"), assistant, orphan])

    assert [type(message).__name__ for message in sanitized] == ["HumanMessage", "AIMessage"]
    assert _wire_tool_calls(sanitized) == []


def test_content_invalid_block_becomes_text_without_invalid_variant_on_the_wire():
    """content 数组里的 invalid_tool_call block 必须转文本，wire 上不出现该变体。"""
    message = AIMessage(
        content=[
            {"type": "text", "text": "先查询"},
            {"type": "invalid_tool_call", "name": "kbs_search", "error": "arguments malformed"},
        ],
    )

    sanitized = _sanitize_invalid_tool_calls([message])

    payload = _convert_message_to_dict(sanitized[0])
    assert payload["content"] == [
        {"type": "text", "text": "先查询"},
        {"type": "text", "text": "[工具调用失败] kbs_search: arguments malformed"},
    ]


@pytest.mark.asyncio
async def test_stream_entry_sanitizes_before_sending(monkeypatch):
    """流式入口同样在发送前净化，而不是只在 _generate 上生效。"""
    from yuxi.models.chat import _wrap_model

    captured: dict = {}

    class _FakeChatModel:
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            captured["messages"] = messages
            return iter([])

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            captured["messages"] = messages
            if False:  # pragma: no cover - 生成器语义
                yield None

    wrapped = _wrap_model(_FakeChatModel)()
    message = AIMessage(
        content="",
        additional_kwargs={"tool_calls": [_malformed_raw_call()]},
        invalid_tool_calls=[{"id": "call-bad", "name": "kbs_search", "args": '{"query":', "error": "bad"}],
    )

    list(wrapped._stream([message]))
    assert _wire_tool_calls(captured["messages"]) == []

    async for _ in wrapped._astream([message]):
        pass
    assert _wire_tool_calls(captured["messages"]) == []
