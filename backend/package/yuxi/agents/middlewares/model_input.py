from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from yuxi.agents.toolkits.buildin.tools import ocr_parse_file

_TOOL_IMAGE_USER_TEXT = "Images returned by read_file are attached below. Inspect them when answering."
_IMAGE_ERROR_TERMS = ("image", "vision", "multimodal", "multi-modal")
_REJECTION_TERMS = (
    "does not support",
    "no endpoints found that support",
    "not allowed",
    "not a vlm",
    "not supported",
    "text-only prompts",
    "unsupported",
)


class ImageInputCompatibilityMiddleware(AgentMiddleware[Any, Any, Any]):
    """Bridge OpenAI tool images and translate explicit image capability errors."""

    tools = [ocr_parse_file]

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        image_paths = _read_file_image_paths(request.messages)
        request = _bridge_openai_tool_images(request)
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            if _has_image(request.messages) and _is_image_input_rejection(exc):
                return _ocr_fallback_response(image_paths)
            raise

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        image_paths = _read_file_image_paths(request.messages)
        request = _bridge_openai_tool_images(request)
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            if _has_image(request.messages) and _is_image_input_rejection(exc):
                return _ocr_fallback_response(image_paths)
            raise


def _bridge_openai_tool_images(request: ModelRequest) -> ModelRequest:
    if not isinstance(request.model, ChatOpenAI):
        return request

    bridged_messages = []
    pending_images: list[dict[str, Any]] = []
    latest_ocr_call_by_path: dict[str, int] = {}
    for index, message in enumerate(request.messages):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            if tool_call.get("name") != "ocr_parse_file":
                continue
            file_path = tool_call.get("args", {}).get("file_path")
            if isinstance(file_path, str) and file_path:
                latest_ocr_call_by_path[file_path] = index

    def flush_pending_images() -> None:
        if not pending_images:
            return
        bridged_messages.append(
            HumanMessage(content_blocks=[{"type": "text", "text": _TOOL_IMAGE_USER_TEXT}, *pending_images])
        )
        pending_images.clear()

    for index, message in enumerate(request.messages):
        if not isinstance(message, ToolMessage):
            flush_pending_images()
            bridged_messages.append(message)
            continue

        image_blocks = [block for block in message.content_blocks if block.get("type") == "image"]
        if not image_blocks:
            bridged_messages.append(message)
            continue

        image_path = message.additional_kwargs.get("read_file_path")
        ocr_fallback_requested = isinstance(image_path, str) and latest_ocr_call_by_path.get(image_path, -1) > index
        if not ocr_fallback_requested:
            pending_images.extend(image_blocks)
        text = "\n".join(
            block["text"]
            for block in message.content_blocks
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        bridged_messages.append(
            message.model_copy(
                update={
                    "content": text
                    or (
                        f"read_file returned {len(image_blocks)} image(s). "
                        + (
                            "OCR fallback was requested for this image."
                            if ocr_fallback_requested
                            else "The image content is attached in the following user message for visual inspection."
                        )
                    )
                }
            )
        )

    flush_pending_images()
    if bridged_messages == request.messages:
        return request
    return request.override(messages=bridged_messages)


def _read_file_image_paths(messages: list[Any]) -> list[str]:
    paths: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if not any(block.get("type") == "image" for block in message.content_blocks):
            continue
        path = message.additional_kwargs.get("read_file_path")
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def _ocr_fallback_response(image_paths: list[str]) -> ModelResponse:
    if not image_paths:
        return ModelResponse(result=[AIMessage(content="当前模型无法读取图片，且没有可供 OCR 工具解析的文件路径。")])

    tool_calls = [
        {
            "name": "ocr_parse_file",
            "args": {"file_path": path},
            "id": f"call_ocr_{uuid4().hex}",
        }
        for path in image_paths
    ]
    return ModelResponse(
        result=[
            AIMessage(
                content="当前模型不支持图片输入，正在改用 OCR 工具提取图片文字。",
                tool_calls=tool_calls,
            )
        ]
    )


def _has_image(messages: list[Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") in {"image", "image_url", "input_image"}
        for message in messages
        for block in getattr(message, "content_blocks", [])
    )


def _is_image_input_rejection(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code not in {400, 404, 415, 422} and not isinstance(exc, ValueError):
        return False

    detail = str(exc).lower()
    return any(term in detail for term in _IMAGE_ERROR_TERMS) and any(term in detail for term in _REJECTION_TERMS)
