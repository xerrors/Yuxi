from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, get_buffer_string

from yuxi.agents.backends.composite import create_agent_composite_backend
from yuxi.agents.middlewares.summary import (
    YuxiSummarizationMiddleware,
    create_summary_middleware,
    sanitize_messages_for_summary,
)
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS


class _DummyModel:
    _llm_type = "test-chat"
    profile = {"max_input_tokens": 128000}

    def _get_ls_params(self) -> dict[str, str]:
        return {"ls_provider": "openai"}

    def invoke(self, _prompt: str, config: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(text="summary")


class _RecordingModel(_DummyModel):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, config: dict | None = None) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(text="summary")


class _MemoryBackend:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def download_files(self, paths: list[str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(content=None, error="file_not_found") for _ in paths]

    def write(self, path: str, content: str) -> SimpleNamespace:
        self.writes.append((path, content))
        return SimpleNamespace(error=None)

    def edit(self, path: str, old_string: str, new_string: str) -> SimpleNamespace:
        self.writes.append((path, new_string))
        return SimpleNamespace(error=None)


def _expected_tool_result_path(content: str, tool_name: str = "query_kb") -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{VIRTUAL_PATH_LARGE_TOOL_RESULTS}/{tool_name}-{digest}.txt"


def _tool_messages() -> list:
    return [
        HumanMessage(content="请查询一下项目资料"),
        AIMessage(
            content="我先查资料",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "query_kb",
                    "args": {"query": "very sensitive query payload"},
                }
            ],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "query_kb", "arguments": '{"query":"raw"}'},
                    }
                ],
                "function_call": {"name": "query_kb"},
            },
            response_metadata={"finish_reason": "tool_calls"},
        ),
        ToolMessage(content="TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED", tool_call_id="call-1", name="query_kb"),
        AIMessage(content="最终答案保留"),
    ]


def _model_request(messages: list) -> ModelRequest:
    return ModelRequest(
        model=_DummyModel(),
        messages=messages,
        system_message=None,
        tools=[],
        runtime=SimpleNamespace(context={}, config={}),
        state={"messages": messages},
    )


def _content_char_counter(messages, **_kwargs) -> int:
    total = 0
    for message in messages:
        if message is None:
            continue
        content = getattr(message, "content", "")
        if isinstance(content, list):
            total += sum(len(str(item)) for item in content)
        else:
            total += len(str(content))
    return total


@pytest.mark.unit
def test_create_summary_middleware_uses_deepagents_with_yuxi_outputs_root() -> None:
    middleware = create_summary_middleware(
        model=_DummyModel(),
        trigger=("tokens", 90_000),
        keep=("tokens", 45_000),
        trim_tokens_to_summarize=4000,
    )

    assert isinstance(middleware, SummarizationMiddleware)
    assert isinstance(middleware, YuxiSummarizationMiddleware)
    assert middleware._backend is create_agent_composite_backend
    assert middleware._history_path_prefix == VIRTUAL_PATH_CONVERSATION_HISTORY
    assert middleware._large_tool_results_prefix == VIRTUAL_PATH_LARGE_TOOL_RESULTS
    assert middleware._lc_helper.trigger == ("tokens", 90_000)
    assert middleware._lc_helper.keep == ("tokens", 45_000)
    assert middleware._lc_helper.trim_tokens_to_summarize == 4000
    assert middleware.tool_result_offload_token_limit == 500


@pytest.mark.unit
def test_create_summary_middleware_passes_custom_summary_prompt() -> None:
    model = _RecordingModel()
    middleware = create_summary_middleware(
        model=model,
        trigger=("messages", 3),
        keep=("messages", 1),
        summary_prompt="CUSTOM SUMMARY PROMPT\n用户要求和偏好必须记录\n{messages}",
        trim_tokens_to_summarize=None,
    )

    assert middleware._create_summary(_tool_messages()) == "summary"

    prompt = model.prompts[0]
    assert prompt.startswith("CUSTOM SUMMARY PROMPT")
    assert "用户要求和偏好必须记录" in prompt
    assert "最终答案保留" in prompt


@pytest.mark.unit
def test_sanitize_messages_for_summary_only_replaces_tool_message_content() -> None:
    backend = _MemoryBackend()
    messages = _tool_messages()

    sanitized = sanitize_messages_for_summary(messages, backend=backend)

    assert [message.type for message in sanitized] == ["human", "ai", "tool", "ai"]
    assert sanitized[0] is messages[0]
    assert sanitized[1] is messages[1]
    assert sanitized[3] is messages[3]
    assert sanitized[1].tool_calls == messages[1].tool_calls
    assert sanitized[1].additional_kwargs == messages[1].additional_kwargs
    assert sanitized[1].response_metadata == messages[1].response_metadata
    assert isinstance(sanitized[2], ToolMessage)
    assert sanitized[2] is not messages[2]
    assert sanitized[2].tool_call_id == messages[2].tool_call_id
    assert sanitized[2].content != messages[2].content

    assert backend.writes == [(_expected_tool_result_path(messages[2].content), messages[2].content)]
    formatted = get_buffer_string(sanitized)
    assert "Tool calls omitted from summary input" not in formatted
    assert "[Tool result saved]" in formatted
    assert "Tool: query_kb" in formatted
    assert "Tool call id" not in formatted
    assert f"Full output path: {_expected_tool_result_path(messages[2].content)}" in formatted
    assert "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED" in formatted
    assert "最终答案保留" in formatted


@pytest.mark.unit
def test_sanitize_messages_for_summary_writes_large_tool_result_and_limits_preview() -> None:
    backend = _MemoryBackend()
    large_result = "BEGIN\n" + ("middle\n" * 2000) + "END"
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=large_result, tool_call_id="call-1", name="query_kb"),
    ]

    sanitized = sanitize_messages_for_summary(messages, backend=backend, tool_result_offload_token_limit=10)
    formatted = get_buffer_string(sanitized)

    assert backend.writes == [(_expected_tool_result_path(large_result), large_result)]
    assert sanitized[1] is messages[1]
    assert isinstance(sanitized[2], ToolMessage)
    assert "[Tool result saved]" in formatted
    assert f"Full output path: {_expected_tool_result_path(large_result)}" in formatted
    assert "BEGIN" in formatted
    assert "END" not in formatted
    assert "Truncated" in formatted
    assert len(sanitized[2].content) < len(large_result)


@pytest.mark.unit
def test_sanitize_messages_for_summary_omits_preview_when_limit_is_zero() -> None:
    backend = _MemoryBackend()
    result_content = "SECRET_RESULT_SHOULD_NOT_BE_IN_PROMPT"
    messages = [
        ToolMessage(content=result_content, tool_call_id="call-1", name="query_kb"),
    ]

    sanitized = sanitize_messages_for_summary(messages, backend=backend, tool_result_offload_token_limit=0)
    formatted = get_buffer_string(sanitized)

    assert backend.writes == [(_expected_tool_result_path(result_content), result_content)]
    assert f"Full output path: {_expected_tool_result_path(result_content)}" in formatted
    assert result_content not in formatted
    assert "Output preview:" not in formatted
    assert "Truncated" in formatted


@pytest.mark.unit
def test_wrap_model_call_keeps_summary_keep_messages_inline_when_summary_triggers() -> None:
    backend = _MemoryBackend()
    model = _RecordingModel()
    large_result = "BEGIN\n" + ("raw result payload\n" * 200)
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=large_result, tool_call_id="call-1", name="query_kb"),
        AIMessage(content="资料已整理"),
        HumanMessage(content="继续"),
    ]
    middleware = YuxiSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", 3),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=1,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    assert len(model.prompts) == 1
    assert captured_messages is not None
    formatted = get_buffer_string(captured_messages)
    assert "[Tool result saved]" not in formatted
    assert "raw result payload" in formatted
    assert (_expected_tool_result_path(large_result), large_result) not in backend.writes
    assert any(write_path.startswith(VIRTUAL_PATH_CONVERSATION_HISTORY) for write_path, _content in backend.writes)


@pytest.mark.unit
def test_wrap_model_call_does_not_sanitize_without_summary_trigger() -> None:
    backend = _MemoryBackend()
    messages = [
        *_tool_messages(),
        HumanMessage(content="新的问题"),
    ]
    middleware = create_summary_middleware(
        model=_DummyModel(),
        trigger=("messages", 100),
        keep=("messages", 10),
        trim_tokens_to_summarize=None,
    )
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware._backend_for_request = lambda _request: backend
    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ModelResponse)
    assert captured_messages is not None
    formatted = get_buffer_string(captured_messages)
    assert backend.writes == []
    assert "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED" in formatted
    assert "[Tool result saved]" not in formatted


@pytest.mark.unit
def test_wrap_model_call_offloads_tool_messages_outside_keep_window_when_summary_triggers() -> None:
    backend = _MemoryBackend()
    model = _RecordingModel()
    old_result = "BEGIN\n" + ("raw result payload\n" * 200)
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=old_result, tool_call_id="call-1", name="query_kb"),
        AIMessage(content="资料已整理"),
        HumanMessage(content="继续"),
        AIMessage(content="可以继续"),
        HumanMessage(content="新问题"),
    ]
    middleware = YuxiSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", 2),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=1,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    assert len(model.prompts) == 1
    assert captured_messages is not None
    formatted = get_buffer_string(captured_messages)
    assert "[Tool result saved]" in model.prompts[0]
    assert "[Tool result saved]" not in formatted
    assert "raw result payload" not in formatted
    tool_result_write = (_expected_tool_result_path(old_result), old_result)
    assert backend.writes.count(tool_result_write) == 1
    assert any(write_path.startswith(VIRTUAL_PATH_CONVERSATION_HISTORY) for write_path, _content in backend.writes)


@pytest.mark.unit
def test_summary_event_reuses_original_preserved_window_on_later_calls() -> None:
    backend = _MemoryBackend()
    old_result = "SAFE\nPRESERVED_TOOL_RESULT_SHOULD_STAY_INLINE"
    new_result = "NEW_TOOL_RESULT_MUST_STAY_INLINE"
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-old", "name": "query_kb", "args": {}}]),
        ToolMessage(content=old_result, tool_call_id="call-old", name="query_kb"),
        AIMessage(content="资料已整理"),
        HumanMessage(content="继续"),
    ]
    middleware = YuxiSummarizationMiddleware(
        model=_RecordingModel(),
        backend=backend,
        trigger=("messages", 5),
        keep=("messages", 3),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=1,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured: list[str] = []

    def handler(request: ModelRequest) -> ModelResponse:
        captured.append(get_buffer_string(request.messages))
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    assert "[Tool result saved]" not in captured[-1]
    assert "PRESERVED_TOOL_RESULT_SHOULD_STAY_INLINE" in captured[-1]

    event = result.command.update["_summarization_event"]
    state_messages = [
        *messages,
        AIMessage(content="ok"),
        HumanMessage(content="继续使用新工具"),
        AIMessage(content="", tool_calls=[{"id": "call-new", "name": "query_kb", "args": {}}]),
        ToolMessage(content=new_result, tool_call_id="call-new", name="query_kb"),
    ]
    middleware._lc_helper._trigger_conditions = [("messages", 999)]
    later_request = ModelRequest(
        model=_DummyModel(),
        messages=state_messages,
        system_message=None,
        tools=[],
        runtime=SimpleNamespace(context={}, config={}),
        state={"messages": state_messages, "_summarization_event": event},
    )

    later_result = middleware.wrap_model_call(later_request, handler)

    assert isinstance(later_result, ModelResponse)
    assert "[Tool result saved]" not in captured[-1]
    assert "PRESERVED_TOOL_RESULT_SHOULD_STAY_INLINE" in captured[-1]
    assert new_result in captured[-1]


@pytest.mark.unit
def test_create_summary_uses_sanitized_messages() -> None:
    model = _RecordingModel()
    middleware = create_summary_middleware(
        model=model,
        trigger=("messages", 3),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
    )

    assert middleware._create_summary(_tool_messages()) == "summary"

    prompt = model.prompts[0]
    assert "Tool calls omitted from summary input" not in prompt
    assert "[Tool result saved]" in prompt
    assert "最终答案保留" in prompt


@pytest.mark.unit
def test_offload_history_uses_tool_messages_with_replaced_content() -> None:
    backend = _MemoryBackend()
    middleware = create_summary_middleware(
        model=_DummyModel(),
        trigger=("messages", 3),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
    )

    path = middleware._offload_to_backend(backend, _tool_messages())

    assert path is not None
    assert backend.writes
    tool_result_path = _expected_tool_result_path("TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED")
    assert (tool_result_path, "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED") in backend.writes
    history_content = next(content for write_path, content in backend.writes if write_path != tool_result_path)
    assert "Tool calls omitted from summary input" not in history_content
    assert "[Tool result saved]" in history_content
    assert "最终答案保留" in history_content
    assert "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED" in history_content
