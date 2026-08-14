from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, get_buffer_string
from langchain_core.exceptions import ContextOverflowError

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
        self.files: dict[str, str] = {}

    def download_files(self, paths: list[str]) -> list[SimpleNamespace]:
        responses = []
        for path in paths:
            if path in self.files:
                responses.append(SimpleNamespace(content=self.files[path].encode("utf-8"), error=None))
            else:
                responses.append(SimpleNamespace(content=None, error="file_not_found"))
        return responses

    def write(self, path: str, content: str) -> SimpleNamespace:
        self.writes.append((path, content))
        self.files[path] = content
        return SimpleNamespace(error=None)

    def edit(self, path: str, old_string: str, new_string: str) -> SimpleNamespace:
        self.writes.append((path, new_string))
        self.files[path] = new_string
        return SimpleNamespace(error=None)

    async def adownload_files(self, paths: list[str]) -> list[SimpleNamespace]:
        return self.download_files(paths)

    async def awrite(self, path: str, content: str) -> SimpleNamespace:
        return self.write(path, content)

    async def aedit(self, path: str, old_string: str, new_string: str) -> SimpleNamespace:
        return self.edit(path, old_string, new_string)


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


@pytest.fixture
def compression_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """捕获 YuxiSummarizationMiddleware 通过 stream writer 推送的压缩事件。"""
    emitted: list[dict] = []
    monkeypatch.setattr(
        "yuxi.agents.middlewares.summary.get_stream_writer",
        lambda: lambda payload: emitted.append(payload),
    )
    return emitted


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
    assert middleware.tool_result_offload_token_limit == 300


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
def test_wrap_model_call_ignores_provider_reported_usage_for_token_trigger() -> None:
    backend = _MemoryBackend()
    model = _RecordingModel()
    messages = [
        HumanMessage(content="short user turn"),
        AIMessage(
            content="short answer",
            usage_metadata={"input_tokens": 200_000, "output_tokens": 100, "total_tokens": 200_100},
            response_metadata={"model_provider": "openai"},
        ),
        HumanMessage(content="next short turn"),
    ]
    middleware = create_summary_middleware(
        model=model,
        trigger=("tokens", 1_000),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
    )
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware._backend_for_request = lambda _request: backend
    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert not isinstance(result, ExtendedModelResponse)
    assert captured_messages == messages
    assert model.prompts == []
    assert backend.writes == []


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
@pytest.mark.parametrize(
    ("tool_result_offload_token_limit", "tool_content", "expect_preview"),
    [
        pytest.param(10, "BEGIN\n" + ("middle\n" * 2000) + "END", True, id="limits_preview"),
        pytest.param(0, "SECRET_RESULT_SHOULD_NOT_BE_IN_PROMPT", False, id="omits_preview"),
    ],
)
def test_sanitize_messages_for_summary_writes_large_tool_result_and_limits_preview(
    tool_result_offload_token_limit: int,
    tool_content: str,
    expect_preview: bool,
) -> None:
    backend = _MemoryBackend()
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=tool_content, tool_call_id="call-1", name="query_kb"),
    ]

    sanitized = sanitize_messages_for_summary(
        messages,
        backend=backend,
        tool_result_offload_token_limit=tool_result_offload_token_limit,
    )
    formatted = get_buffer_string(sanitized)

    assert backend.writes == [(_expected_tool_result_path(tool_content), tool_content)]
    assert sanitized[1] is messages[1]
    assert isinstance(sanitized[2], ToolMessage)
    assert "[Tool result saved]" in formatted
    assert f"Full output path: {_expected_tool_result_path(tool_content)}" in formatted
    assert "Truncated" in formatted
    assert ("Output preview:" in formatted) is expect_preview
    if expect_preview:
        assert "BEGIN" in formatted
        assert "END" not in formatted
        assert len(sanitized[2].content) < len(tool_content)
    else:
        assert tool_content not in formatted


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            {
                "l1_l2_trigger_ratio": 100.0,
                "tool_result_offload_token_limit": 1,
                "keep": 3,
                "extra_turns": 0,
                "expect_extended": False,
                "expect_summary_count": 0,
                "expect_offload_marker_in_captured": True,
                "expect_truncated_in_captured": True,
                "expect_full_content_in_summary": False,
                "expect_history_write": False,
            },
            id="l1_only_without_state_mutation",
        ),
        pytest.param(
            {
                "l1_l2_trigger_ratio": 0.01,
                "tool_result_offload_token_limit": 1,
                "keep": 2,
                "extra_turns": 1,
                "expect_extended": True,
                "expect_summary_count": 1,
                "expect_offload_marker_in_captured": False,
                "expect_truncated_in_captured": False,
                "expect_full_content_in_summary": False,
                "expect_history_write": True,
            },
            id="l2_summary_offloads_tool_results_outside_keep_window",
        ),
        pytest.param(
            {
                "l1_l2_trigger_ratio": 0.01,
                "tool_result_offload_token_limit": None,
                "keep": 2,
                "extra_turns": 0,
                "expect_extended": True,
                "expect_summary_count": 1,
                "expect_offload_marker_in_captured": False,
                "expect_truncated_in_captured": False,
                "expect_full_content_in_summary": True,
                "expect_history_write": True,
            },
            id="l2_summary_uses_full_tool_result_preview",
        ),
    ],
)
def test_wrap_model_call_offloads_large_tool_results(scenario: dict) -> None:
    """大 tool result 在 L1 视图中落盘并裁剪：L1-only 不改写原始消息，L2 触发时决定摘要输入内容。"""
    backend = _MemoryBackend()
    model = _RecordingModel()
    large_result = "BEGIN\n" + ("raw result payload\n" * 200)
    if scenario["expect_full_content_in_summary"]:
        large_result += "END"
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=large_result, tool_call_id="call-1", name="query_kb"),
        AIMessage(content="资料已整理"),
        HumanMessage(content="继续"),
    ]
    for index in range(scenario["extra_turns"]):
        messages.extend([AIMessage(content=f"可以继续{index}"), HumanMessage(content=f"新问题{index}")])
    middleware = YuxiSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", scenario["keep"]),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=scenario["tool_result_offload_token_limit"],
        l1_l2_trigger_ratio=scenario["l1_l2_trigger_ratio"],
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse) is scenario["expect_extended"]
    assert len(model.prompts) == scenario["expect_summary_count"]
    assert messages[2].content == large_result
    assert (_expected_tool_result_path(large_result), large_result) in backend.writes
    history_writes = [
        write_path
        for write_path, _content in backend.writes
        if write_path.startswith(VIRTUAL_PATH_CONVERSATION_HISTORY)
    ]
    assert bool(history_writes) is scenario["expect_history_write"]

    assert captured_messages is not None
    formatted = get_buffer_string(captured_messages)
    assert ("[Tool result saved]" in formatted) is scenario["expect_offload_marker_in_captured"]
    assert ("Truncated" in formatted) is scenario["expect_truncated_in_captured"]
    assert "raw result payload" not in formatted

    if scenario["expect_summary_count"]:
        assert "[Tool result saved]" in model.prompts[0]
        assert ("END" in model.prompts[0]) is scenario["expect_full_content_in_summary"]


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
async def test_awrap_model_call_emits_completed_for_l1_without_summary(
    compression_events: list[dict],
) -> None:
    backend = _MemoryBackend()
    large_result = "BEGIN\n" + ("raw result payload\n" * 200)
    messages = [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=large_result, tool_call_id="call-1", name="query_kb"),
        HumanMessage(content="继续"),
    ]
    middleware = YuxiSummarizationMiddleware(
        model=_RecordingModel(),
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", 2),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        l1_l2_trigger_ratio=100.0,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured_messages: list | None = None

    async def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    result = await middleware.awrap_model_call(_model_request(messages), handler)

    assert not isinstance(result, ExtendedModelResponse)
    assert [event["status"] for event in compression_events] == ["started", "completed"]
    assert captured_messages is not None
    formatted = get_buffer_string(captured_messages)
    assert "[Tool result saved]" in formatted
    assert "Truncated" in formatted
    assert messages[2].content == large_result


@pytest.mark.unit
def test_wrap_model_call_truncates_large_write_file_args_only_in_l1_view() -> None:
    backend = _MemoryBackend()
    large_content = "x" * 5000
    raw_arguments = '{"file_path": "/tmp/a.txt", "content": "' + large_content + '"}'
    messages = [
        HumanMessage(content="写文件" + ("y" * 1000)),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "write_file",
                    "args": {"file_path": "/tmp/a.txt", "content": large_content},
                }
            ],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": raw_arguments},
                    }
                ]
            },
        ),
        ToolMessage(content="ok", tool_call_id="call-1", name="write_file"),
        HumanMessage(content="继续"),
    ]
    middleware = YuxiSummarizationMiddleware(
        model=_RecordingModel(),
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", 2),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        l1_l2_trigger_ratio=100.0,
        tool_arg_max_length=100,
    )
    captured_messages: list | None = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal captured_messages
        captured_messages = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert not isinstance(result, ExtendedModelResponse)
    assert captured_messages is not None
    compact_ai = captured_messages[1]
    assert isinstance(compact_ai, AIMessage)
    assert compact_ai is not messages[1]
    assert compact_ai.tool_calls[0]["args"]["content"].endswith("...(argument truncated for context view)")
    provider_arguments = compact_ai.additional_kwargs["tool_calls"][0]["function"]["arguments"]
    assert provider_arguments.endswith("...(argument truncated for context view)")
    assert messages[1].tool_calls[0]["args"]["content"] == large_content
    assert messages[1].additional_kwargs["tool_calls"][0]["function"]["arguments"] == raw_arguments


@pytest.mark.unit
def test_summary_event_reuses_original_preserved_window_on_later_calls() -> None:
    backend = _MemoryBackend()
    old_result = "SAFE\n" + ("PRESERVED_TOOL_RESULT_SHOULD_STAY_INLINE\n" * 200)
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
        l1_l2_trigger_ratio=0.01,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    captured: list[str] = []

    def handler(request: ModelRequest) -> ModelResponse:
        captured.append(get_buffer_string(request.messages))
        return ModelResponse(result=[AIMessage(content="ok")])

    result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    assert "[Tool result saved]" in captured[-1]
    assert "Truncated" in captured[-1]

    event = result.command.update["_summarization_event"]
    state_messages = [
        *messages,
        AIMessage(content="ok"),
        HumanMessage(content="继续使用新工具"),
        AIMessage(content="", tool_calls=[{"id": "call-new", "name": "query_kb", "args": {}}]),
        ToolMessage(content=new_result, tool_call_id="call-new", name="query_kb"),
    ]
    middleware._lc_helper._trigger_clauses = [{"messages": 999}]
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
    backend = _MemoryBackend()
    model = _RecordingModel()
    middleware = YuxiSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("messages", 3),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=0,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS

    l1_messages = middleware._sanitize_messages_for_l1(_tool_messages(), backend=backend)

    assert middleware._create_summary(l1_messages) == "summary"

    prompt = model.prompts[0]
    assert "Tool calls omitted from summary input" not in prompt
    assert "[Tool result saved]" in prompt
    assert "最终答案保留" in prompt


@pytest.mark.unit
def test_offload_history_uses_tool_messages_with_replaced_content() -> None:
    backend = _MemoryBackend()
    middleware = YuxiSummarizationMiddleware(
        model=_DummyModel(),
        backend=backend,
        trigger=("messages", 3),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=0,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS

    l1_messages = middleware._sanitize_messages_for_l1(_tool_messages(), backend=backend)
    path = middleware._offload_to_backend(backend, l1_messages)

    assert path is not None
    assert backend.writes
    tool_result_path = _expected_tool_result_path("TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED")
    assert (tool_result_path, "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED") in backend.writes
    history_content = next(content for write_path, content in backend.writes if write_path != tool_result_path)
    assert "Tool calls omitted from summary input" not in history_content
    assert "[Tool result saved]" in history_content
    assert "最终答案保留" in history_content
    assert f"Full output path: {tool_result_path}" in history_content
    assert "TOOL_RESULT_SHOULD_NOT_BE_SUMMARIZED" not in history_content


def _make_compressing_middleware(backend: _MemoryBackend) -> tuple[YuxiSummarizationMiddleware, str]:
    large_result = "BEGIN\n" + ("raw result payload\n" * 200)
    middleware = YuxiSummarizationMiddleware(
        model=_RecordingModel(),
        backend=backend,
        trigger=("tokens", 500),
        keep=("messages", 3),
        token_counter=_content_char_counter,
        trim_tokens_to_summarize=None,
        tool_result_offload_token_limit=1,
        l1_l2_trigger_ratio=0.01,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    middleware._backend_for_request = lambda _request: backend
    return middleware, large_result


def _compressing_messages(large_result: str) -> list:
    return [
        HumanMessage(content="查资料"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "query_kb", "args": {}}]),
        ToolMessage(content=large_result, tool_call_id="call-1", name="query_kb"),
        AIMessage(content="资料已整理"),
        HumanMessage(content="继续"),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("async_call", [False, True], ids=["sync", "async"])
async def test_wrap_model_call_emits_started_and_completed(
    compression_events: list[dict],
    async_call: bool,
) -> None:
    backend = _MemoryBackend()
    middleware, large_result = _make_compressing_middleware(backend)
    messages = _compressing_messages(large_result)

    if async_call:
        async def handler(request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[AIMessage(content="ok")])

        result = await middleware.awrap_model_call(_model_request(messages), handler)
    else:
        def handler(request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[AIMessage(content="ok")])

        result = middleware.wrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    statuses = [event["status"] for event in compression_events]
    assert statuses == ["started", "completed"]
    assert all(event["type"] == "yuxi.context_compression" for event in compression_events)
    completed = compression_events[-1]
    assert isinstance(completed.get("cutoff_index"), int)
    assert completed.get("file_path") is not None


@pytest.mark.unit
async def test_awrap_model_call_emits_nothing_when_summary_not_triggered(compression_events: list[dict]) -> None:
    backend = _MemoryBackend()
    middleware = create_summary_middleware(
        model=_DummyModel(),
        trigger=("messages", 100),
        keep=("messages", 10),
        trim_tokens_to_summarize=None,
    )
    middleware._backend_for_request = lambda _request: backend
    messages = [*_tool_messages(), HumanMessage(content="新的问题")]

    async def handler(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="ok")])

    result = await middleware.awrap_model_call(_model_request(messages), handler)

    assert not isinstance(result, ExtendedModelResponse)
    assert compression_events == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("preconfigure", "overflow_message"),
    [
        pytest.param("disable_trigger", "context overflow", id="trigger_raised"),
        pytest.param("l1_only", "context overflow after l1", id="l1_only"),
    ],
)
async def test_awrap_model_call_falls_back_to_summary_on_overflow(
    compression_events: list[dict],
    preconfigure: str,
    overflow_message: str,
) -> None:
    backend = _MemoryBackend()
    middleware, large_result = _make_compressing_middleware(backend)
    if preconfigure == "disable_trigger":
        middleware._lc_helper.trigger = [("tokens", 100_000)]
        middleware._lc_helper._trigger_clauses = [{"tokens": 100_000}]
    else:
        middleware.l1_l2_trigger_ratio = 100.0
    messages = _compressing_messages(large_result)
    calls = 0

    async def handler(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ContextOverflowError(overflow_message)
        return ModelResponse(result=[AIMessage(content="ok")])

    result = await middleware.awrap_model_call(_model_request(messages), handler)

    assert isinstance(result, ExtendedModelResponse)
    assert calls == 2
    assert [event["status"] for event in compression_events] == ["started", "completed"]


@pytest.mark.unit
async def test_awrap_model_call_emits_failed_when_handler_raises_after_started(
    compression_events: list[dict],
) -> None:
    backend = _MemoryBackend()
    middleware, large_result = _make_compressing_middleware(backend)
    messages = _compressing_messages(large_result)

    async def handler(request: ModelRequest) -> ModelResponse:
        raise RuntimeError("model boom")

    with pytest.raises(RuntimeError, match="model boom"):
        await middleware.awrap_model_call(_model_request(messages), handler)

    statuses = [event["status"] for event in compression_events]
    assert statuses == ["started", "failed"]
    assert "model boom" in compression_events[-1]["error"]
