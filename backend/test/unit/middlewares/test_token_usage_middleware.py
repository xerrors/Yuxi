from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from yuxi.agents.middlewares.token_usage import TokenUsageMiddleware


@pytest.mark.asyncio
async def test_token_usage_middleware_records_request_and_state_tokens() -> None:
    middleware = TokenUsageMiddleware()
    tool_schema = {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search project documents.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
    request = SimpleNamespace(
        model=SimpleNamespace(profile={"max_input_tokens": 2000}),
        state={"messages": [HumanMessage(content="old message")]},
        messages=[HumanMessage(content="current message")],
        system_message=SystemMessage(content="system prompt"),
        tools=[tool_schema],
        runtime=SimpleNamespace(context=SimpleNamespace(summary_threshold=2)),
    )

    async def handler(_request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="answer",
                    usage_metadata={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
                )
            ]
        )

    result = await middleware.awrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    token_usage = result.command.update["token_usage"]
    assert token_usage["state_message_count"] == 2
    assert token_usage["state_message_count_before_call"] == 1
    assert token_usage["llm_message_count"] == 1
    assert token_usage["state_messages_tokens"] >= token_usage["state_messages_tokens_before_call"]
    assert token_usage["llm_input_tokens"] >= token_usage["llm_messages_tokens"]
    assert token_usage["system_tokens"] > 0
    assert token_usage["tools_tokens"] > 0
    assert token_usage["tool_count"] == 1
    assert token_usage["context_window"] == 2000
    assert token_usage["remaining_context_tokens"] == 2000 - token_usage["llm_input_tokens"]
    assert token_usage["summary_trigger_tokens"] == 2048
    assert "summary_keep_tokens" not in token_usage
    assert token_usage["model_usage"] == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    assert token_usage["estimate"] is True


@pytest.mark.asyncio
async def test_token_usage_middleware_detects_effective_summary_message() -> None:
    middleware = TokenUsageMiddleware()
    summary_message = HumanMessage(
        content="conversation summary",
        additional_kwargs={"lc_source": "summarization"},
    )
    request = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        state={"messages": [HumanMessage(content="raw history")]},
        messages=[summary_message, HumanMessage(content="recent user turn")],
        system_message=None,
        tools=[],
        runtime=SimpleNamespace(context=SimpleNamespace(summary_threshold=100)),
    )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="answer")])

    result = await middleware.awrap_model_call(request, handler)
    token_usage = result.command.update["token_usage"]

    assert token_usage["summary_active"] is True
    assert token_usage["summary_message_tokens"] > 0
    assert token_usage["context_window"] is None
    assert token_usage["context_usage_ratio"] is None
