"""Utilities for normalizing user input across DB and LangChain messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from langchain.messages import HumanMessage

# 单条消息可携带的图片张数与总字节上限。总字节按 base64 长度计，是真正会打到
# wire 上的体积；nginx 对 /api/agent/runs 放宽到 100M，这里留出余量以便在网关
# 拒绝之前就以 422 明确失败（10 张 5MB 压缩图 base64 后约 67MB）。
MAX_CHAT_IMAGES = 10
MAX_CHAT_IMAGE_TOTAL_BYTES = 80 * 1024 * 1024


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
        return replace(self, extra_metadata=dict(metadata))


def normalize_image_contents(raw: object) -> list[str]:
    """把请求里的 `image_content` 归一成图片列表，并在这里闭合张数与总量校验。

    接受 None / 字符串 / 字符串数组：旧的单值客户端（CLI 与已文档化的 API-key 用户）
    仍然照常工作。张数与元素类型只在这一点判定，两条路由共用，避免同一个字段名在
    不同 endpoint 上语义不同。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates: list[object] = [raw]
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise ValueError("image_content 必须是 base64 字符串或其数组")

    images: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            raise ValueError("image_content 数组的元素必须是 base64 字符串")
        if item.strip():
            images.append(item)

    if len(images) > MAX_CHAT_IMAGES:
        raise ValueError(f"单条消息最多携带 {MAX_CHAT_IMAGES} 张图片，当前 {len(images)} 张")
    total_bytes = sum(len(image) for image in images)
    if total_bytes > MAX_CHAT_IMAGE_TOTAL_BYTES:
        raise ValueError(f"图片总大小超出限制（{MAX_CHAT_IMAGE_TOTAL_BYTES // (1024 * 1024)}MB）")
    return images


def build_chat_input_message(query: str, image_content: str | list[str] | None = None) -> AgentRunInputMessage:
    """按文本 + 图片构造模型输入；`image_content` 接受单值或数组，归一在本函数内闭合。

    归一放在这里而不是让每个调用方各自处理：`image_content` 的现存调用方既有单值
    （CLI、API-key 用户、只落了单值列的历史行），也有数组（Web 多图）。参数名与 wire
    字段同名，避免出现「同一个值在两层各归一一次」的第二个真值来源。

    `AgentRunInputMessage.image_content` 保留为首图：它仍有仓库外消费者与旧历史行需要
    兜底；多图事实由 `langchain_message` 承载。
    """
    images = normalize_image_contents(image_content)
    if images:
        langchain_message = HumanMessage(
            content=[
                {"type": "text", "text": query},
                *({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}} for image in images),
            ]
        )
        message_type = "multimodal_image"
    else:
        langchain_message = HumanMessage(content=query)
        message_type = "text"

    return AgentRunInputMessage(
        content=query,
        message_type=message_type,
        image_content=images[0] if images else None,
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


def extract_image_contents(raw_message: dict[str, Any] | None) -> list[str]:
    """从 `raw_message` 的 content parts 取出内联图片的 base64，供历史回显用。

    只收内联 data URL；外部 http(s) 图与纯文本消息都返回空列表——历史 DTO 是 wire
    契约，不该把 LangChain 的 `HumanMessage` 形状透传给浏览器，所以投影在服务端完成。
    """
    if not isinstance(raw_message, dict):
        return []
    content = raw_message.get("content")
    if not isinstance(content, list):
        return []

    images: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        image_url = part.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if not isinstance(url, str):
            continue
        base64_content = _extract_data_url_base64(url)
        if base64_content:
            images.append(base64_content)
    return images


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
        raw_content = raw_message.get("content")
        message_type = "multimodal_image" if image_content or _has_image_url_content_part(raw_content) else "text"
        return AgentRunInputMessage(
            content=content,
            message_type=message_type,
            image_content=image_content,
            langchain_message=langchain_message,
            extra_metadata=dict(metadata),
        )

    # 更早的历史行只有单值 image_content（raw_message 是后来的 refactor 才引入的），
    # 单值分支由 build_chat_input_message 的归一承接，旧行仍能显示那一张图。
    return build_chat_input_message(content, image_content)


def _has_image_url_content_part(content: object) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
