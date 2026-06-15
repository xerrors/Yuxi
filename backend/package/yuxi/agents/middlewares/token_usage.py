"""Token usage observation middleware for Yuxi agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command


class TokenUsagePayload(TypedDict, total=False):
    """Serializable token usage snapshot stored in LangGraph state."""

    state_message_count: int
    state_message_count_before_call: int
    state_messages_tokens: int
    state_messages_tokens_before_call: int
    llm_message_count: int
    llm_messages_tokens: int
    llm_input_tokens: int
    system_tokens: int
    tools_tokens: int
    tool_count: int
    context_window: int | None
    context_usage_ratio: float | None
    remaining_context_tokens: int | None
    summary_active: bool
    summary_message_tokens: int
    summary_trigger_tokens: int | None
    model_usage: dict[str, int]
    counter: str
    estimate: bool
    measured_at: str


class TokenUsageState(AgentState):
    """Agent state extension with the latest token usage snapshot."""

    token_usage: NotRequired[TokenUsagePayload]


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _model_context_window(model: Any) -> int | None:
    profile = getattr(model, "profile", None)
    if not isinstance(profile, Mapping):
        return None
    max_input_tokens = profile.get("max_input_tokens")
    return max_input_tokens if isinstance(max_input_tokens, int) and max_input_tokens > 0 else None


def _summary_trigger_tokens(runtime_context: Any) -> int | None:
    threshold = _safe_int(getattr(runtime_context, "summary_threshold", None))
    if threshold is None or threshold <= 0:
        return None
    return threshold * 1024


def _is_summary_message(message: AnyMessage) -> bool:
    return getattr(message, "additional_kwargs", {}).get("lc_source") == "summarization"


def _model_usage_from_response(response: ModelResponse) -> dict[str, int]:
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            continue
        return {str(key): value for key, value in usage.items() if isinstance(value, int)}
    return {}


class TokenUsageMiddleware(AgentMiddleware[TokenUsageState]):
    """Record approximate context token usage for the current model request."""

    state_schema = TokenUsageState

    def __init__(self, token_counter=count_tokens_approximately) -> None:
        super().__init__()
        self.token_counter = token_counter

    def _count_tokens(self, messages: Iterable[Any], *, tools: list[Any] | None = None) -> int:
        message_list = list(messages)
        if tools is not None:
            return int(self.token_counter(message_list, tools=tools))
        return int(self.token_counter(message_list))

    def _build_snapshot(self, request: ModelRequest, response: ModelResponse) -> TokenUsagePayload:
        state_messages = list(request.state.get("messages") or [])
        llm_messages = list(request.messages or [])
        system_messages = [request.system_message] if request.system_message is not None else []
        tools = list(request.tools or [])
        response_messages = list(response.result or [])

        state_tokens_before_call = self._count_tokens(state_messages)
        next_state_messages = [*state_messages, *response_messages]
        state_messages_tokens = self._count_tokens(next_state_messages)
        llm_messages_tokens = self._count_tokens(llm_messages)
        system_tokens = self._count_tokens(system_messages)
        tools_tokens = self._count_tokens([], tools=tools) if tools else 0
        llm_input_tokens = self._count_tokens([*system_messages, *llm_messages], tools=tools)

        context_window = _model_context_window(request.model)
        context_usage_ratio = None
        remaining_context_tokens = None
        if context_window:
            context_usage_ratio = min(1.0, round(llm_input_tokens / context_window, 4))
            remaining_context_tokens = max(context_window - llm_input_tokens, 0)

        summary_message = llm_messages[0] if llm_messages and _is_summary_message(llm_messages[0]) else None
        summary_trigger_tokens = _summary_trigger_tokens(getattr(request.runtime, "context", None))

        return {
            "state_message_count": len(next_state_messages),
            "state_message_count_before_call": len(state_messages),
            "state_messages_tokens": state_messages_tokens,
            "state_messages_tokens_before_call": state_tokens_before_call,
            "llm_message_count": len(llm_messages),
            "llm_messages_tokens": llm_messages_tokens,
            "llm_input_tokens": llm_input_tokens,
            "system_tokens": system_tokens,
            "tools_tokens": tools_tokens,
            "tool_count": len(tools),
            "context_window": context_window,
            "context_usage_ratio": context_usage_ratio,
            "remaining_context_tokens": remaining_context_tokens,
            "summary_active": summary_message is not None,
            "summary_message_tokens": self._count_tokens([summary_message]) if summary_message else 0,
            "summary_trigger_tokens": summary_trigger_tokens,
            "model_usage": _model_usage_from_response(response),
            "counter": "langchain.count_tokens_approximately",
            "estimate": True,
            "measured_at": datetime.now(UTC).isoformat(),
        }

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ExtendedModelResponse:
        response = handler(request)
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": self._build_snapshot(request, response)}),
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        response = await handler(request)
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"token_usage": self._build_snapshot(request, response)}),
        )
