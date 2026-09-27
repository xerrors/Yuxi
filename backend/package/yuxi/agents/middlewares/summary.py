"""Yuxi 对 DeepAgents 会话摘要中间件的适配。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import warnings
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from typing import Any

from deepagents.middleware.summarization import (
    Command,
    ContextOverflowError,
    SummarizationMiddleware,
    _aclip_overflow_tail,
    _clip_overflow_tail,
)
from langchain.agents.middleware.summarization import ContextSize
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage, get_buffer_string
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.config import get_stream_writer
from langgraph.constants import TAG_NOSTREAM

from yuxi.agents.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_YUXI_SUMMARY_PROMPT,
)
from yuxi.models.chat import load_chat_model, resolve_chat_model_spec
from yuxi.utils.logging_config import logger

_APPROX_CHARS_PER_TOKEN = 4
_DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS = 300
_DEFAULT_TOOL_ARG_MAX_LENGTH = 2000
_TRUNCATED_TOOL_ARG_TEXT = "...(argument truncated for context view)"
_TOOL_RESULT_SAVED_MARKER = "yuxi_tool_result_saved"
_STRUCTURED_SEARCH_TOOL_NAMES = {"query_kb", "web_search"}
_SEARCH_CONTENT_KEYS = ("content", "text", "snippet", "summary")
_SUMMARY_COMPRESSION_STATE: ContextVar[dict[str, bool] | None] = ContextVar(
    "yuxi_summary_compression_state",
    default=None,
)


class YuxiSummarizationMiddleware(SummarizationMiddleware):
    """先确定性压缩工具结果，再按同一压力阈值决定是否生成摘要。"""

    _SUMMARY_INVOKE_CONFIG = {"metadata": {"lc_source": "summarization"}, "tags": [TAG_NOSTREAM]}

    def __init__(
        self,
        *args,
        tool_result_offload_token_limit: int | None = _DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS,
        tool_arg_max_length: int = _DEFAULT_TOOL_ARG_MAX_LENGTH,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tool_result_offload_token_limit = tool_result_offload_token_limit
        self.tool_arg_max_length = tool_arg_max_length

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步执行单阈值压缩流程。"""
        compression_state: dict[str, bool] = {"started": False}
        compression_token = _SUMMARY_COMPRESSION_STATE.set(compression_state)
        try:
            try:
                result = self._wrap_model_call_with_compaction(request, handler)
            except Exception as exc:
                if compression_state["started"]:
                    _emit_compression("failed", error=repr(exc))
                raise
            self._emit_completed(result)
            return result
        finally:
            _SUMMARY_COMPRESSION_STATE.reset(compression_token)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步执行单阈值压缩流程。"""
        compression_state: dict[str, bool] = {"started": False}
        compression_token = _SUMMARY_COMPRESSION_STATE.set(compression_state)
        try:
            try:
                result = await self._awrap_model_call_with_compaction(request, handler)
            except Exception as exc:
                if compression_state["started"]:
                    _emit_compression("failed", error=repr(exc))
                raise
            self._emit_completed(result)
            return result
        finally:
            _SUMMARY_COMPRESSION_STATE.reset(compression_token)

    async def aforce_summarize(self, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """主动压缩已有 checkpoint，并返回待持久化更新与结果指标。"""
        messages = list(state.get("messages") or [])
        previous_event = state.get("_summarization_event")
        effective_messages = self._apply_event_to_messages(messages, previous_event)
        before_tokens = self._count_tokens(effective_messages, None, [])
        compacted_messages = self._compact_messages(effective_messages)
        cutoff_index = self._determine_cutoff_index(compacted_messages)
        if cutoff_index <= 0:
            return {}, {
                "status": "no_op",
                "before_tokens": before_tokens,
                "after_tokens": self._count_tokens(compacted_messages, None, []),
                "reason": "insufficient_history",
            }

        messages_to_summarize, preserved_messages = self._partition_messages(
            compacted_messages,
            cutoff_index,
        )
        offloaded_messages, failed_media = await self._aoffload_inline_media(
            self._backend,
            messages_to_summarize,
        )
        session_id = self._get_session_id(state)
        file_path = await self._aoffload_to_backend(self._backend, offloaded_messages, session_id)
        if file_path is None:
            raise RuntimeError("主动压缩无法保存可恢复的对话历史")
        summary = await self._acreate_summary_or_raise(offloaded_messages)
        if failed_media:
            logger.warning(
                "Conversation history offloaded to %s, but %d media block(s) could not be offloaded.",
                file_path,
                failed_media,
            )

        summary_messages = self._build_new_messages_with_path(summary, file_path)
        state_cutoff_index = self._compute_state_cutoff(previous_event, cutoff_index)
        update = {
            "_summarization_event": {
                "cutoff_index": state_cutoff_index,
                "summary_message": summary_messages[0],
                "file_path": file_path,
            },
            "_summarization_session_id": session_id,
        }
        persisted_messages = self._apply_event_to_messages(messages, update["_summarization_event"])
        after_tokens = self._count_tokens(persisted_messages, None, [])
        return update, {
            "status": "completed",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "compressed_messages": cutoff_index,
            "file_path": file_path,
        }

    def _wrap_model_call_with_compaction(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        effective_messages = self._get_effective_messages(request)
        total_tokens = self._count_tokens(effective_messages, request.system_message, request.tools)
        truncated_messages, _ = self._truncate_args(effective_messages, total_tokens)
        should_compact = self._should_summarize(truncated_messages, total_tokens)

        overflow_triggered = False
        if not should_compact:
            try:
                return handler(request.override(messages=truncated_messages))
            except ContextOverflowError:
                overflow_triggered = True

        compacted_messages = self._compact_messages(truncated_messages)
        if should_compact:
            _emit_compression_started_once()

        compacted_tokens = self._count_tokens(compacted_messages, request.system_message, request.tools)
        pressure_threshold = self._entry_trigger_tokens()
        should_summarize = overflow_triggered or pressure_threshold is None or compacted_tokens >= pressure_threshold
        if not should_summarize:
            try:
                response = handler(request.override(messages=compacted_messages))
            except ContextOverflowError:
                overflow_triggered = True
            else:
                _emit_compression("completed")
                return response

        cutoff_index = self._determine_cutoff_index(compacted_messages)
        if cutoff_index <= 0:
            response = handler(request.override(messages=compacted_messages))
            if should_compact:
                _emit_compression("completed")
            return response

        messages_to_summarize, preserved_messages = self._partition_messages(compacted_messages, cutoff_index)
        new_state_tail: list[AnyMessage] = []
        if overflow_triggered:
            preserved_messages, new_state_tail = _clip_overflow_tail(
                preserved_messages,
                self._backend,
                keep=self._lc_helper.keep,
                max_input_tokens=self._get_profile_limits(),
                token_counter=self.token_counter,
                large_tool_results_prefix=self._large_tool_results_prefix,
            )

        offloaded_messages, failed_media = self._offload_inline_media(self._backend, messages_to_summarize)
        session_id = self._get_session_id(request.state)
        file_path = self._offload_to_backend(self._backend, offloaded_messages, session_id)
        self._report_offload_result(file_path, failed_media)

        summary = self._create_summary(offloaded_messages)
        new_messages = self._build_new_messages_with_path(summary, file_path)
        new_event = self._build_summary_event(request.state, cutoff_index, new_messages[0], file_path)
        response = handler(request.override(messages=[*new_messages, *preserved_messages]))
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update=self._build_state_update(new_event, session_id, new_state_tail)),
        )

    async def _awrap_model_call_with_compaction(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        effective_messages = self._get_effective_messages(request)
        total_tokens = self._count_tokens(effective_messages, request.system_message, request.tools)
        truncated_messages, _ = self._truncate_args(effective_messages, total_tokens)
        should_compact = self._should_summarize(truncated_messages, total_tokens)

        overflow_triggered = False
        if not should_compact:
            try:
                return await handler(request.override(messages=truncated_messages))
            except ContextOverflowError:
                overflow_triggered = True

        compacted_messages = self._compact_messages(truncated_messages)
        if should_compact:
            _emit_compression_started_once()

        compacted_tokens = self._count_tokens(compacted_messages, request.system_message, request.tools)
        pressure_threshold = self._entry_trigger_tokens()
        should_summarize = overflow_triggered or pressure_threshold is None or compacted_tokens >= pressure_threshold
        if not should_summarize:
            try:
                response = await handler(request.override(messages=compacted_messages))
            except ContextOverflowError:
                overflow_triggered = True
            else:
                _emit_compression("completed")
                return response

        cutoff_index = self._determine_cutoff_index(compacted_messages)
        if cutoff_index <= 0:
            response = await handler(request.override(messages=compacted_messages))
            if should_compact:
                _emit_compression("completed")
            return response

        messages_to_summarize, preserved_messages = self._partition_messages(compacted_messages, cutoff_index)
        new_state_tail: list[AnyMessage] = []
        if overflow_triggered:
            preserved_messages, new_state_tail = await _aclip_overflow_tail(
                preserved_messages,
                self._backend,
                keep=self._lc_helper.keep,
                max_input_tokens=self._get_profile_limits(),
                token_counter=self.token_counter,
                large_tool_results_prefix=self._large_tool_results_prefix,
            )

        offloaded_messages, failed_media = await self._aoffload_inline_media(
            self._backend,
            messages_to_summarize,
        )
        session_id = self._get_session_id(request.state)
        file_path, summary = await asyncio.gather(
            self._aoffload_to_backend(self._backend, offloaded_messages, session_id),
            self._acreate_summary(offloaded_messages),
        )
        self._report_offload_result(file_path, failed_media)

        new_messages = self._build_new_messages_with_path(summary, file_path)
        new_event = self._build_summary_event(request.state, cutoff_index, new_messages[0], file_path)
        response = await handler(request.override(messages=[*new_messages, *preserved_messages]))
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update=self._build_state_update(new_event, session_id, new_state_tail)),
        )

    def _compact_messages(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """压缩过大的工具结果及文件写入参数，保持消息顺序和标识不变。"""
        compacted: list[AnyMessage] = []
        modified = False
        for message in messages:
            updated = message
            if isinstance(message, AIMessage):
                updated = _truncate_ai_tool_call_args(message, max_length=self.tool_arg_max_length)
            elif (
                isinstance(message, ToolMessage)
                and getattr(message, "additional_kwargs", {}).get(_TOOL_RESULT_SAVED_MARKER) is not True
                and _should_offload_tool_message(message, self.tool_result_offload_token_limit)
            ):
                updated = _replace_tool_message_content(
                    message,
                    backend=self._backend,
                    tool_result_token_limit=self.tool_result_offload_token_limit,
                    large_tool_results_prefix=self._large_tool_results_prefix,
                )
            compacted.append(updated)
            modified = modified or updated is not message
        return compacted if modified else messages

    def _should_summarize(self, messages: list[AnyMessage], total_tokens: int) -> bool:
        if not self._lc_helper._trigger_clauses:
            return False
        for clause in self._lc_helper._trigger_clauses:
            if self._trigger_clause_met(clause, messages, total_tokens):
                return True
        return False

    def _trigger_clause_met(self, clause: dict[str, Any], messages: list[AnyMessage], total_tokens: int) -> bool:
        for kind, value in clause.items():
            if kind == "messages" and len(messages) < value:
                return False
            if kind == "tokens" and total_tokens < value:
                return False
            if kind == "fraction":
                max_input_tokens = self._get_profile_limits()
                if max_input_tokens is None or total_tokens < max(int(max_input_tokens * value), 1):
                    return False
        return True

    def _entry_trigger_tokens(self) -> int | None:
        thresholds: list[int] = []
        for clause in self._lc_helper._trigger_clauses or []:
            token_threshold = clause.get("tokens")
            if isinstance(token_threshold, int) and token_threshold > 0:
                thresholds.append(token_threshold)
            fraction = clause.get("fraction")
            if isinstance(fraction, int | float) and (max_input_tokens := self._get_profile_limits()) is not None:
                thresholds.append(max(int(max_input_tokens * fraction), 1))
        return min(thresholds) if thresholds else None

    def _build_summary_prompt(self, messages: list[AnyMessage]) -> str | None:
        trimmed = self._lc_helper._trim_messages_for_summary(messages)
        if not trimmed:
            return None
        return self._lc_helper.summary_prompt.format(messages=get_buffer_string(trimmed, format="xml")).rstrip()

    def _create_summary(self, messages: list[AnyMessage]) -> str:
        if not messages:
            return "No previous conversation history."
        prompt = self._build_summary_prompt(messages)
        if prompt is None:
            return "Previous conversation was too long to summarize."
        try:
            return self.model.invoke(prompt, config=self._SUMMARY_INVOKE_CONFIG).text.strip()
        except Exception as exc:
            return f"Error generating summary: {exc!s}"

    async def _acreate_summary(self, messages: list[AnyMessage]) -> str:
        if not messages:
            return "No previous conversation history."
        prompt = self._build_summary_prompt(messages)
        if prompt is None:
            return "Previous conversation was too long to summarize."
        try:
            response = await self.model.ainvoke(prompt, config=self._SUMMARY_INVOKE_CONFIG)
            return response.text.strip()
        except Exception as exc:
            return f"Error generating summary: {exc!s}"

    async def _acreate_summary_or_raise(self, messages: list[AnyMessage]) -> str:
        prompt = self._build_summary_prompt(messages) if messages else None
        if prompt is None:
            raise RuntimeError("没有可供主动压缩的对话历史")
        response = await self.model.ainvoke(prompt, config=self._SUMMARY_INVOKE_CONFIG)
        summary = response.text.strip()
        if not summary:
            raise RuntimeError("摘要模型返回空内容")
        return summary

    def _offload_to_backend(self, backend, messages: list[AnyMessage], session_id: str) -> str | None:
        _emit_compression_started_once()
        return super()._offload_to_backend(backend, messages, session_id)

    async def _aoffload_to_backend(self, backend, messages: list[AnyMessage], session_id: str) -> str | None:
        _emit_compression_started_once()
        return await super()._aoffload_to_backend(backend, messages, session_id)

    def _build_summary_event(
        self,
        state: dict[str, Any],
        cutoff_index: int,
        summary_message: AnyMessage,
        file_path: str | None,
    ) -> dict[str, Any]:
        return {
            "cutoff_index": self._compute_state_cutoff(state.get("_summarization_event"), cutoff_index),
            "summary_message": summary_message,
            "file_path": file_path,
        }

    @staticmethod
    def _build_state_update(
        event: dict[str, Any],
        session_id: str,
        new_state_tail: list[AnyMessage],
    ) -> dict[str, Any]:
        update: dict[str, Any] = {
            "_summarization_event": event,
            "_summarization_session_id": session_id,
        }
        if new_state_tail:
            update["messages"] = list(new_state_tail)
        return update

    @staticmethod
    def _report_offload_result(file_path: str | None, failed_media: int) -> None:
        if file_path is None:
            message = (
                "Offloading conversation history to backend failed during summarization. "
                "Older messages will not be recoverable."
            )
            logger.error(message)
            warnings.warn(message, stacklevel=3)
        elif failed_media:
            logger.warning(
                "Conversation history offloaded to %s, but %d media block(s) could not be offloaded.",
                file_path,
                failed_media,
            )

    @staticmethod
    def _summarization_event_from_result(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, ExtendedModelResponse):
            return None
        update = getattr(getattr(result, "command", None), "update", None)
        event = update.get("_summarization_event") if isinstance(update, dict) else None
        return event if isinstance(event, dict) else None

    def _emit_completed(self, result: Any) -> None:
        event = self._summarization_event_from_result(result)
        if event is not None:
            _emit_compression(
                "completed",
                cutoff_index=event.get("cutoff_index"),
                file_path=event.get("file_path"),
            )


def create_summary_middleware(
    model: str | BaseChatModel,
    *,
    backend,
    trigger: ContextSize | list[ContextSize] | None,
    keep: ContextSize | list[ContextSize] | None,
    summary_prompt: str | None = None,
    trim_tokens_to_summarize: int | None = None,
    tool_result_offload_token_limit: int | None = _DEFAULT_SUMMARY_TOOL_RESULT_LIMIT_TOKENS,
) -> YuxiSummarizationMiddleware:
    """创建绑定单次运行 backend 的摘要中间件。"""
    middleware_kwargs = {
        "model": model,
        "backend": backend,
        "trigger": trigger,
        "keep": keep,
        "token_counter": _count_tokens_for_summary_trigger,
        "trim_tokens_to_summarize": trim_tokens_to_summarize,
        "tool_result_offload_token_limit": tool_result_offload_token_limit,
    }
    if summary_prompt and summary_prompt.strip():
        middleware_kwargs["summary_prompt"] = summary_prompt
    return YuxiSummarizationMiddleware(**middleware_kwargs)


def create_summary_middleware_from_context(context, *, backend) -> YuxiSummarizationMiddleware:
    """按 Agent 运行时配置创建自动与主动压缩共用的摘要器。"""
    trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    model_spec = resolve_chat_model_spec(context.model)
    return create_summary_middleware(
        model=load_chat_model(fully_specified_name=model_spec, session_id=context.thread_id, uid=context.uid),
        backend=backend,
        trigger=("tokens", trigger_tokens),
        keep=("messages", getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES)),
        summary_prompt=getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT,
        trim_tokens_to_summarize=trigger_tokens,
        tool_result_offload_token_limit=getattr(
            context,
            "summary_tool_result_token_limit",
            DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        ),
    )


def _emit_compression(status: str, **extra: Any) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"type": "yuxi.context_compression", "status": status, **extra})


def _emit_compression_started_once() -> None:
    state = _SUMMARY_COMPRESSION_STATE.get()
    if state is not None and state["started"]:
        return
    if state is not None:
        state["started"] = True
    _emit_compression("started")


def _count_tokens_for_summary_trigger(messages: Iterable[Any], **kwargs: Any) -> int:
    kwargs.pop("use_usage_metadata_scaling", None)
    return count_tokens_approximately(messages, use_usage_metadata_scaling=False, **kwargs)


def _build_tool_result_preview(
    content: str,
    token_limit: int | None,
    *,
    tool_name: str | None = None,
    raw_content: Any = None,
) -> tuple[str, int]:
    """在单条工具预算内生成结构化检索或通用首中尾预览。"""
    text = content.strip()
    if token_limit is None:
        return text, 0
    if token_limit <= 0:
        return "", len(text)

    max_chars = token_limit * _APPROX_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text, 0

    structured_preview = _structured_search_preview(tool_name, raw_content, max_chars)
    if structured_preview is not None:
        return structured_preview, max(len(text) - len(structured_preview), 0)

    preview = _generic_tool_result_preview(text, max_chars)
    return preview, len(text) - len(preview)


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item if isinstance(item, str) else item["text"]
            for item in content
            if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _tool_result_path(tool_name: str | None, content: str, prefix: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (tool_name or "").strip()).strip(".-") or "tool-result"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}/{safe_name}-{digest}.txt"


def _write_tool_result(backend, path: str, content: str) -> str:
    if backend is None:
        raise RuntimeError(f"Cannot save tool result to {path}: backend is unavailable")
    result = backend.write(path, content)
    error = getattr(result, "error", None)
    if not error or "already exists" in str(error).lower():
        return path
    raise RuntimeError(f"Failed to write tool result to {path}: {error}")


def _should_offload_tool_message(message: ToolMessage, token_limit: int | None) -> bool:
    if token_limit is None or token_limit <= 0:
        return True
    content = _extract_text_content(message.content)
    estimated_tokens = max((len(content) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN, 1)
    return estimated_tokens > token_limit


def _replace_tool_message_content(
    message: ToolMessage,
    *,
    backend,
    tool_result_token_limit: int | None,
    large_tool_results_prefix: str,
) -> ToolMessage:
    content = _extract_text_content(message.content)
    tool_name = message.name if isinstance(message.name, str) and message.name else None
    path = _write_tool_result(
        backend,
        _tool_result_path(tool_name, content, large_tool_results_prefix),
        content,
    )
    preview, omitted_chars = _build_tool_result_preview(
        content,
        tool_result_token_limit,
        tool_name=tool_name,
        raw_content=message.content,
    )
    approx_tokens = max((len(content) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN, 1)
    lines = [
        "[Tool result saved]",
        f"Tool: {tool_name or 'unknown'}",
        f"Approx tokens: {approx_tokens}",
        f"SHA-256: {hashlib.sha256(content.encode('utf-8')).hexdigest()}",
        f"Full output path: {path}",
    ]
    if preview:
        lines.extend(["", "Output preview:", preview])
    if omitted_chars:
        lines.append(f"[Truncated {omitted_chars} chars. Read the full output from the saved file.]")

    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs[_TOOL_RESULT_SAVED_MARKER] = True
    return message.model_copy(update={"content": "\n".join(lines), "additional_kwargs": additional_kwargs})


def _structured_search_preview(tool_name: str | None, raw_content: Any, max_chars: int) -> str | None:
    if tool_name not in _STRUCTURED_SEARCH_TOOL_NAMES or max_chars <= 0:
        return None
    parsed = _parse_structured_tool_result(raw_content)
    if parsed is None:
        return None

    results = parsed["results"]
    preview = _search_header(tool_name, parsed, len(results))
    selected = [_search_result_record(tool_name, result) for result in results[:8]]
    while selected and len(_encode_search_preview(preview, selected, len(results))) > max_chars:
        selected.pop()

    if not selected:
        compact = _encode_search_preview(preview, [], len(results))
        minimal = _encode_search_preview(
            {
                "kind": "knowledge_base" if tool_name == "query_kb" else "web_search",
                "result_count": len(results),
            },
            [],
            len(results),
        )
        if len(compact) <= max_chars:
            return compact
        return minimal if len(minimal) <= max_chars else None

    base_text = _encode_search_preview(preview, selected, len(results))
    per_result_chars = max(max_chars - len(base_text) - 24 * len(selected), 0) // len(selected)
    for record, body in selected:
        if body and per_result_chars >= 24:
            record["content_preview"] = _clip_search_content(body, per_result_chars)

    encoded = _encode_search_preview(preview, selected, len(results))
    while len(encoded) > max_chars and any("content_preview" in record for record, _ in selected):
        for record, _body in selected:
            content_preview = record.get("content_preview")
            if isinstance(content_preview, str):
                shortened = content_preview[: max(len(content_preview) - 16, 0)]
                if shortened:
                    record["content_preview"] = shortened
                else:
                    record.pop("content_preview")
        encoded = _encode_search_preview(preview, selected, len(results))
    return encoded


def _parse_structured_tool_result(content: Any) -> dict[str, Any] | None:
    value = content
    if isinstance(content, list):
        text_parts = [item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
        if any(isinstance(item, dict) and ("type" in item or "text" in item) for item in content):
            if len(text_parts) != 1:
                return None
            value = text_parts[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        value = {"results": value}
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        return None
    return value


def _search_header(tool_name: str, parsed: dict[str, Any], result_count: int) -> dict[str, Any]:
    preview = {
        "kind": "knowledge_base" if tool_name == "query_kb" else "web_search",
        "result_count": result_count,
    }
    for key in ("kb_id", "query", "response_time", "error"):
        if parsed.get(key) is not None:
            preview[key] = _bounded_search_scalar(parsed[key])
    return preview


def _search_result_record(tool_name: str, result: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(result, dict):
        return {"value": _bounded_search_scalar(str(result))}, ""

    keys = (
        ("id", "kb_id", "file_id", "title", "source", "score", "distance")
        if tool_name == "query_kb"
        else ("title", "url", "site_name", "publish_time", "score")
    )
    record = {key: _bounded_search_scalar(result[key]) for key in keys if result.get(key) is not None}
    if tool_name == "query_kb" and isinstance(result.get("metadata"), dict):
        metadata = result["metadata"]
        metadata_keys = (
            "source",
            "filename",
            "title",
            "chunk_index",
            "score",
            "rerank_score",
            "hybrid_score",
            "graph_score",
            "distance",
        )
        selected_metadata = {
            key: _bounded_search_scalar(metadata[key]) for key in metadata_keys if metadata.get(key) is not None
        }
        if selected_metadata:
            record["metadata"] = selected_metadata
    body = next((result[key] for key in _SEARCH_CONTENT_KEYS if isinstance(result.get(key), str)), "")
    return record, body


def _encode_search_preview(
    preview: dict[str, Any],
    selected: list[tuple[dict[str, Any], str]],
    result_count: int,
) -> str:
    encoded = {**preview, "results": [record for record, _body in selected]}
    omitted_results = result_count - len(selected)
    if omitted_results:
        encoded["omitted_results"] = omitted_results
    return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))


def _bounded_search_scalar(value: Any, max_chars: int = 240) -> Any:
    return _clip_search_content(value, max_chars) if isinstance(value, str) else value


def _clip_search_content(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= 24:
        return value[:max_chars]
    marker = "…"
    head_length = ((max_chars - len(marker)) * 3) // 4
    tail_length = max_chars - len(marker) - head_length
    return f"{value[:head_length]}{marker}{value[-tail_length:]}"


def _generic_tool_result_preview(text: str, max_chars: int) -> str:
    labels = ("[HEAD]\n", "\n\n[MIDDLE]\n", "\n\n[TAIL]\n")
    content_budget = max_chars - sum(len(label) for label in labels)
    if content_budget <= 0:
        return text[:max_chars]

    head_length = (content_budget * 2) // 5
    middle_length = content_budget // 5
    tail_length = content_budget - head_length - middle_length
    middle_start = max((len(text) - middle_length) // 2, head_length)
    return "".join(
        (
            labels[0],
            text[:head_length],
            labels[1],
            text[middle_start : middle_start + middle_length],
            labels[2],
            text[-tail_length:],
        )
    )


def _truncate_ai_tool_call_args(message: AIMessage, *, max_length: int) -> AIMessage:
    if not message.tool_calls and not getattr(message, "additional_kwargs", None):
        return message

    updated_tool_calls = []
    tool_calls_modified = False
    for tool_call in message.tool_calls or []:
        if not isinstance(tool_call, dict):
            updated_tool_calls.append(tool_call)
            continue
        updated, modified = _truncate_tool_call_args(tool_call, max_length)
        updated_tool_calls.append(updated)
        tool_calls_modified = tool_calls_modified or modified

    additional_kwargs, provider_calls_modified = _truncate_provider_tool_calls(
        dict(getattr(message, "additional_kwargs", {}) or {}),
        max_length,
    )
    if not tool_calls_modified and not provider_calls_modified:
        return message

    updated_message = message.model_copy(update={"additional_kwargs": additional_kwargs})
    if tool_calls_modified:
        updated_message.tool_calls = updated_tool_calls
    return updated_message


def _truncate_tool_call_args(tool_call: dict[str, Any], max_length: int) -> tuple[dict[str, Any], bool]:
    args = tool_call.get("args")
    if tool_call.get("name") not in {"write_file", "edit_file"} or not isinstance(args, dict):
        return tool_call, False

    truncated_args = {
        key: _truncate_string_arg(value, max_length) if isinstance(value, str) else value for key, value in args.items()
    }
    if truncated_args == args:
        return tool_call, False
    return {**tool_call, "args": truncated_args}, True


def _truncate_provider_tool_calls(
    additional_kwargs: dict[str, Any],
    max_length: int,
) -> tuple[dict[str, Any], bool]:
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return additional_kwargs, False

    updated_tool_calls = []
    modified = False
    for raw_call in raw_tool_calls:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if (
            not isinstance(function, dict)
            or function.get("name") not in {"write_file", "edit_file"}
            or not isinstance(arguments, str)
            or len(arguments) <= max_length
        ):
            updated_tool_calls.append(raw_call)
            continue
        updated_tool_calls.append(
            {**raw_call, "function": {**function, "arguments": _truncate_string_arg(arguments, max_length)}}
        )
        modified = True

    if not modified:
        return additional_kwargs, False
    return {**additional_kwargs, "tool_calls": updated_tool_calls}, True


def _truncate_string_arg(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:20]}{_TRUNCATED_TOOL_ARG_TEXT}"
