"""Utilities for normalizing user input across DB and LangChain messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from langchain.messages import HumanMessage


@dataclass(frozen=True)
class AgentRunInputMessage:
    content: str
    message_type: str
    image_content: str | None
    langchain_message: HumanMessage | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def raw_message(self) -> dict[str, Any] | None:
        return self.langchain_message.model_dump() if self.langchain_message else None

    def require_langchain_message(self) -> HumanMessage:
        if not self.langchain_message:
            raise ValueError("chat input message must include a LangChain HumanMessage")
        return self.langchain_message

    def with_metadata(self, metadata: dict[str, Any]) -> AgentRunInputMessage:
        normalized_metadata = dict(metadata)
        langchain_message = _with_stable_message_id(
            self.langchain_message,
            normalized_metadata.get("request_id"),
        )
        return replace(
            self,
            langchain_message=langchain_message,
            extra_metadata=normalized_metadata,
        )


def build_chat_input_message(query: str, image_content: str | None = None) -> AgentRunInputMessage:
    if image_content:
        langchain_message = HumanMessage(
            content=[
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
            ]
        )
        message_type = "multimodal_image"
    else:
        langchain_message = HumanMessage(content=query)
        message_type = "text"

    return AgentRunInputMessage(
        content=query,
        message_type=message_type,
        image_content=image_content,
        langchain_message=langchain_message,
    )


def build_chat_input_message_from_openai_content(content: str | list[dict[str, Any]]) -> AgentRunInputMessage:
    if isinstance(content, str):
        if not content:
            raise ValueError("user message content 必须是非空字符串或多模态数组")
        return build_chat_input_message(content)

    if not isinstance(content, list) or not content:
        raise ValueError("user message content 必须是非空字符串或多模态数组")

    parts: list[dict[str, Any]] = []
    text_segments: list[str] = []
    first_image_content: str | None = None
    has_image = False

    for part in content:
        if not isinstance(part, dict):
            raise ValueError("user message content 多模态数组元素必须是对象")

        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ValueError("text content part 必须包含字符串 text")
            if text:
                text_segments.append(text)
                parts.append({"type": "text", "text": text})
            continue

        if part_type == "image_url":
            image_url = _normalize_openai_image_url_part(part.get("image_url"))
            has_image = True
            if first_image_content is None:
                first_image_content = _extract_data_url_base64(image_url["url"])
            parts.append({"type": "image_url", "image_url": image_url})
            continue

        raise ValueError(f"不支持的多模态 content part 类型: {part_type}")

    if not text_segments and not has_image:
        raise ValueError("user message content 必须包含非空文本或图片")

    query = "\n".join(text_segments)
    if not has_image:
        return build_chat_input_message(query)

    return AgentRunInputMessage(
        content=query,
        message_type="multimodal_image",
        image_content=first_image_content,
        langchain_message=HumanMessage(content=parts),
    )


def _normalize_openai_image_url_part(image_url: object) -> dict[str, Any]:
    if isinstance(image_url, str):
        url = image_url
        normalized: dict[str, Any] = {"url": url}
    elif isinstance(image_url, dict):
        url = image_url.get("url")
        normalized = dict(image_url)
    else:
        raise ValueError("image_url content part 必须包含 image_url.url")

    if not isinstance(url, str) or not url:
        raise ValueError("image_url content part 必须包含 image_url.url")
    normalized["url"] = url
    return normalized


def _extract_data_url_base64(url: str) -> str | None:
    marker = ";base64,"
    if not url.startswith("data:image/") or marker not in url:
        return None
    return url.split(marker, 1)[1]


def build_resume_input_message(resume: object) -> AgentRunInputMessage:
    return AgentRunInputMessage(
        content=json.dumps(resume, ensure_ascii=False),
        message_type="resume",
        image_content=None,
    )


def restore_chat_input_message(*, content: str, image_content: str | None, metadata: dict) -> AgentRunInputMessage:
    raw_message = metadata.get("raw_message")
    if isinstance(raw_message, dict):
        try:
            langchain_message = HumanMessage.model_validate(raw_message)
        except Exception as exc:
            raise ValueError("invalid raw_message for chat input message") from exc
        langchain_message = _with_stable_message_id(langchain_message, metadata.get("request_id"))
        raw_content = raw_message.get("content")
        message_type = "multimodal_image" if image_content or _has_image_url_content_part(raw_content) else "text"
        return AgentRunInputMessage(
            content=content,
            message_type=message_type,
            image_content=image_content,
            langchain_message=langchain_message,
            extra_metadata=dict(metadata),
        )

    return build_chat_input_message(content, image_content).with_metadata(metadata)


def _with_stable_message_id(message: HumanMessage | None, request_id: object) -> HumanMessage | None:
    """为可重试的 Graph 输入绑定稳定 ID，避免 checkpoint 重复追加同一用户消息。"""
    if message is None or message.id or not isinstance(request_id, str) or not request_id:
        return message
    return message.model_copy(update={"id": f"request:{request_id}"})


def _has_image_url_content_part(content: object) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
