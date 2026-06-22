"""Yuxi adapter for DeepAgents conversation summarization middleware."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.summarization import ContextSize
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, ToolMessage

from yuxi.agents.backends.composite import create_agent_composite_backend
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS

_APPROX_CHARS_PER_TOKEN = 4
_DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS = 500
_TOOL_RESULT_SAVED_MARKER = "yuxi_tool_result_saved"
_SUMMARY_BACKEND: ContextVar[Any | None] = ContextVar("yuxi_summary_backend", default=None)
_SUMMARY_SANITIZED_MESSAGES: ContextVar[dict[tuple[int, ...], list[AnyMessage]] | None] = ContextVar(
    "yuxi_summary_sanitized_messages",
    default=None,
)


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _tool_result_path(tool_name: str | None, content: str, large_tool_results_prefix: str) -> str:
    safe_tool_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (tool_name or "").strip()).strip(".-") or "tool-result"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{large_tool_results_prefix}/{safe_tool_name}-{digest}.txt"


def _preview_tool_result(content: str, token_limit: int | None) -> tuple[str, int]:
    text = content.strip()
    if token_limit is None:
        return text, 0
    if token_limit <= 0:
        return "", len(text)

    max_chars = token_limit * _APPROX_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text, 0

    preview = text[:max_chars].rstrip()
    return preview, len(text) - len(preview)


def _write_tool_result(backend, path: str, content: str) -> str | None:
    if backend is None:
        return None

    result = backend.write(path, content)
    error = getattr(result, "error", None)
    if not error:
        return path
    if "already exists" in str(error).lower():
        return path
    raise RuntimeError(f"Failed to write tool result to {path}: {error}")


def _tool_result_replacement_content(
    message: ToolMessage,
    *,
    backend,
    tool_result_offload_token_limit: int | None,
    large_tool_results_prefix: str,
) -> str:
    content = _extract_text_content(message.content)
    approx_tokens = max((len(content) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN, 1)
    tool_name = message.name if isinstance(message.name, str) and message.name else None
    path = _write_tool_result(backend, _tool_result_path(tool_name, content, large_tool_results_prefix), content)
    preview, omitted_chars = _preview_tool_result(content, tool_result_offload_token_limit)

    lines = [
        "[Tool result saved]",
        f"Tool: {tool_name or 'unknown'}",
        f"Approx tokens: {approx_tokens}",
    ]
    if path:
        lines.append(f"Full output path: {path}")
    if preview:
        lines.extend(["", "Output preview:", preview])
    if omitted_chars:
        lines.append(f"\n[Truncated {omitted_chars} chars. Read the full output from the saved file.]")
    return "\n".join(lines)


def _replace_tool_message_content(
    message: ToolMessage,
    *,
    backend,
    tool_result_offload_token_limit: int | None,
    large_tool_results_prefix: str,
) -> ToolMessage:
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs[_TOOL_RESULT_SAVED_MARKER] = True
    return message.model_copy(
        update={
            "content": _tool_result_replacement_content(
                message,
                backend=backend,
                tool_result_offload_token_limit=tool_result_offload_token_limit,
                large_tool_results_prefix=large_tool_results_prefix,
            ),
            "additional_kwargs": additional_kwargs,
        }
    )


def sanitize_messages_for_summary(
    messages: list[AnyMessage],
    *,
    backend=None,
    tool_result_offload_token_limit: int | None = _DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS,
    large_tool_results_prefix: str = VIRTUAL_PATH_LARGE_TOOL_RESULTS,
) -> list[AnyMessage]:
    """Build a compact summary/offload view by replacing only ToolMessage content."""
    sanitized: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            if getattr(message, "additional_kwargs", {}).get(_TOOL_RESULT_SAVED_MARKER) is True:
                sanitized.append(message)
                continue
            sanitized.append(
                _replace_tool_message_content(
                    message,
                    backend=backend,
                    tool_result_offload_token_limit=tool_result_offload_token_limit,
                    large_tool_results_prefix=large_tool_results_prefix,
                )
            )
            continue
        sanitized.append(message)
    return sanitized


class YuxiSummarizationMiddleware(SummarizationMiddleware):
    """DeepAgents summarization middleware with Yuxi-specific tool-call sanitization."""

    def __init__(
        self,
        *args,
        tool_result_offload_token_limit: int | None = _DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tool_result_offload_token_limit = tool_result_offload_token_limit

    def _sanitize_messages_for_summary(
        self,
        messages: list[AnyMessage],
        *,
        backend,
    ) -> list[AnyMessage]:
        cache = _SUMMARY_SANITIZED_MESSAGES.get()
        cache_key = tuple(id(message) for message in messages)
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        sanitized = sanitize_messages_for_summary(
            messages,
            backend=backend,
            tool_result_offload_token_limit=self.tool_result_offload_token_limit,
            large_tool_results_prefix=self._large_tool_results_prefix,
        )
        if cache is not None:
            cache[cache_key] = sanitized
        return sanitized

    def _backend_for_request(self, request: ModelRequest):
        try:
            return self._get_backend(request.state, request.runtime)
        except Exception:
            return None

    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        return super()._create_summary(
            self._sanitize_messages_for_summary(
                messages_to_summarize,
                backend=_SUMMARY_BACKEND.get(),
            )
        )

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        return await super()._acreate_summary(
            self._sanitize_messages_for_summary(
                messages_to_summarize,
                backend=_SUMMARY_BACKEND.get(),
            )
        )

    def _offload_to_backend(self, backend, messages: list[AnyMessage]) -> str | None:
        return super()._offload_to_backend(
            backend,
            self._sanitize_messages_for_summary(
                messages,
                backend=backend,
            ),
        )

    async def _aoffload_to_backend(self, backend, messages: list[AnyMessage]) -> str | None:
        return await super()._aoffload_to_backend(
            backend,
            self._sanitize_messages_for_summary(
                messages,
                backend=backend,
            ),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        backend_token = _SUMMARY_BACKEND.set(self._backend_for_request(request))
        sanitized_token = _SUMMARY_SANITIZED_MESSAGES.set({})
        try:
            return super().wrap_model_call(request, handler)
        finally:
            _SUMMARY_SANITIZED_MESSAGES.reset(sanitized_token)
            _SUMMARY_BACKEND.reset(backend_token)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        backend_token = _SUMMARY_BACKEND.set(self._backend_for_request(request))
        sanitized_token = _SUMMARY_SANITIZED_MESSAGES.set({})
        try:
            return await super().awrap_model_call(request, handler)
        finally:
            _SUMMARY_SANITIZED_MESSAGES.reset(sanitized_token)
            _SUMMARY_BACKEND.reset(backend_token)


def create_summary_middleware(
    model: str | BaseChatModel,
    *,
    trigger: ContextSize | list[ContextSize] | None,
    keep: ContextSize | list[ContextSize] | None,
    summary_prompt: str | None = None,
    trim_tokens_to_summarize: int | None = None,
    tool_result_offload_token_limit: int | None = _DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS,
) -> SummarizationMiddleware:
    """Create DeepAgents summarization middleware using Yuxi's virtual outputs root."""
    middleware_kwargs = {
        "model": model,
        "backend": create_agent_composite_backend,
        "trigger": trigger,
        "keep": keep,
        "trim_tokens_to_summarize": trim_tokens_to_summarize,
        "tool_result_offload_token_limit": tool_result_offload_token_limit,
    }
    if summary_prompt and summary_prompt.strip():
        middleware_kwargs["summary_prompt"] = summary_prompt
    middleware = YuxiSummarizationMiddleware(**middleware_kwargs)
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    return middleware
