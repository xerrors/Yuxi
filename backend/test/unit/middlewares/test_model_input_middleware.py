from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from yuxi.agents.middlewares.model_input import ImageInputCompatibilityMiddleware


def _request(model, messages) -> ModelRequest:
    return ModelRequest(model=model, messages=messages)


def _openai_model() -> ChatOpenAI:
    return ChatOpenAI(model="test-model", api_key="test-key", base_url="https://example.com/v1")


def _read_file_image_message(path: str = "/home/gem/user-data/uploads/image.png") -> ToolMessage:
    return ToolMessage(
        content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
        tool_call_id="call_image",
        additional_kwargs={"read_file_path": path, "read_file_media_type": "image/png"},
    )


def test_bridges_openai_tool_images_after_parallel_tool_results_without_mutating_state() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    original_messages = [
        HumanMessage("读图并列目录"),
        ToolMessage(
            content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
            name="read_file",
            tool_call_id="call_image",
        ),
        ToolMessage(content="['a.png']", name="ls", tool_call_id="call_ls"),
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(_openai_model(), original_messages), handler)

    messages = seen["messages"]
    assert original_messages[1].content_blocks[0]["type"] == "image"
    assert [message.type for message in messages] == ["human", "tool", "tool", "human"]
    assert messages[1].tool_call_id == "call_image"
    assert isinstance(messages[1].content, str)
    assert messages[2].tool_call_id == "call_ls"
    assert messages[3].content_blocks[1] == {
        "type": "image",
        "base64": "abc",
        "mime_type": "image/png",
    }


def test_keeps_non_openai_tool_images_unchanged() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    messages = [
        ToolMessage(
            content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
            tool_call_id="call_image",
        )
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(SimpleNamespace(), messages), handler)

    assert seen["messages"] is messages


@pytest.mark.asyncio
async def test_translates_explicit_provider_image_rejection() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [_read_file_image_message()],
    )

    async def handler(_request):
        error = RuntimeError("This model does not support image input")
        error.status_code = 400
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert response.result[0].content == "当前模型不支持图片输入，正在改用 OCR 工具提取图片文字。"
    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"
    assert response.result[0].tool_calls[0]["args"] == {"file_path": "/home/gem/user-data/uploads/image.png"}


@pytest.mark.asyncio
async def test_does_not_mask_unrelated_provider_errors_when_image_is_present() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}])],
    )

    async def handler(_request):
        error = RuntimeError("invalid tool schema")
        error.status_code = 400
        raise error

    with pytest.raises(RuntimeError, match="invalid tool schema"):
        await middleware.awrap_model_call(request, handler)


@pytest.mark.asyncio
async def test_translates_openrouter_missing_vision_endpoint() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [_read_file_image_message()],
    )

    async def handler(_request):
        error = RuntimeError("No endpoints found that support image input")
        error.status_code = 404
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"


@pytest.mark.asyncio
async def test_translates_siliconflow_non_vlm_error_without_retrying() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(SimpleNamespace(), [_read_file_image_message()])
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        error = RuntimeError(
            "Error code: 400 - {'code': 20041, 'message': "
            "'The model is not a VLM (Vision Language Model). Please use text-only prompts.'}"
        )
        error.status_code = 400
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert calls == 1
    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"


def test_omits_historical_tool_image_after_ocr_fallback() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    path = "/home/gem/user-data/uploads/image.png"
    messages = [
        _read_file_image_message(path),
        AIMessage(
            content="正在改用 OCR。",
            tool_calls=[
                {
                    "name": "ocr_parse_file",
                    "args": {"file_path": path},
                    "id": "call_ocr",
                }
            ],
        ),
        ToolMessage(content="OCR result", tool_call_id="call_ocr"),
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(_openai_model(), messages), handler)

    assert [message.type for message in seen["messages"]] == ["tool", "ai", "tool"]
    assert "OCR fallback was requested" in seen["messages"][0].content


def test_registers_ocr_tool_for_automatic_fallback() -> None:
    assert [tool.name for tool in ImageInputCompatibilityMiddleware().tools] == ["ocr_parse_file"]


@pytest.mark.asyncio
async def test_does_not_report_malformed_image_as_unsupported_model() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "broken"}}])],
    )

    async def handler(_request):
        error = RuntimeError("image_url provided is not a valid image")
        error.status_code = 400
        raise error

    with pytest.raises(RuntimeError, match="not a valid image"):
        await middleware.awrap_model_call(request, handler)
