"""从工具原始结果派生审计文本、公开内容和失败语义。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

TOOL_FAILURE_MESSAGE = "工具执行失败"

# 工具图片落盘的协议键与模型注记前缀由公开投影 Owner 统一持有，避免双定义漂移。
SAVED_MEDIA_KEY = "yuxi_saved_media"
MEDIA_NOTE_PREFIX = "[Tool result images saved to sandbox]"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """两种读模型共享执行结局，公开内容遵循 input content 协议。"""

    audit_content: str
    public_content: Any
    error: str | None


def tool_result_text(value: Any) -> str:
    """工具执行审计与 checkpoint 对账共用稳定 JSON 文本规则。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _image_public_block(block: dict, saved: dict | None, artifact_url_factory: Callable[[str], str] | None) -> dict | None:
    """图片优先引用已落盘 artifact 的预览地址，退回内嵌 data URL。"""
    if saved and artifact_url_factory is not None:
        return {"type": "input_image", "image_url": artifact_url_factory(saved["path"])}
    source = block.get("source")
    source = source if isinstance(source, dict) else {}
    data_b64 = block.get("base64") or source.get("data")
    mime = block.get("mime_type") or block.get("mimeType") or source.get("media_type")
    if isinstance(data_b64, str) and data_b64 and isinstance(mime, str) and mime:
        return {"type": "input_image", "image_url": f"data:{mime};base64,{data_b64}"}
    return None


def normalize_tool_result(
    output: Any, *, message: str | None = None, artifact_url_factory: Callable[[str], str] | None = None
) -> ToolResult:
    """保留合法结构化内容，只在这里确定文本和失败原因。"""
    value = output.get("content") if isinstance(output, dict) else output
    text = tool_result_text(value)
    public = value
    if isinstance(value, list):
        if all(isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str) for part in value):
            public = [{"type": "input_text", "text": part["text"]} for part in value]
        elif not all(isinstance(part, dict) and part.get("type") in {"input_text", "input_image"} for part in value):
            public = _project_content_blocks(value, output, text, artifact_url_factory)
    elif value is not None and not isinstance(value, str):
        public = text
    failed = isinstance(output, dict) and output.get("status") == "error"
    return ToolResult(audit_content=text, public_content=public, error=str(message or text or TOOL_FAILURE_MESSAGE) if failed else None)


def _project_content_blocks(
    value: list,
    output: Any,
    text: str,
    artifact_url_factory: Callable[[str], str] | None,
) -> Any:
    """text 与 image 块投影为公开 input 协议；无法投影的整体退回 JSON 文本。"""
    saved_media = {}
    if isinstance(output, dict):
        for record in output.get("additional_kwargs", {}).get(SAVED_MEDIA_KEY, []) or []:
            if isinstance(record, dict) and isinstance(record.get("index"), int):
                saved_media[record["index"]] = record
    projected = []
    for index, part in enumerate(value):
        if not isinstance(part, dict):
            return text
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            if _is_media_note(part["text"], saved_media):
                continue
            projected.append({"type": "input_text", "text": part["text"]})
        elif part.get("type") == "image":
            block = _image_public_block(part, saved_media.get(index), artifact_url_factory)
            if block is None:
                return text
            projected.append(block)
        else:
            return text
    return projected


def _is_media_note(note_text: str, saved_media: dict[int, dict]) -> bool:
    """只识别完整注记（前缀行 + 与保存记录一致的路径集合），不误伤业务回显文本。"""
    lines = note_text.splitlines()
    saved_paths = {record.get("path") for record in saved_media.values()}
    return len(lines) > 1 and lines[0] == MEDIA_NOTE_PREFIX and set(lines[1:]) == saved_paths


def unwrap_tool_result(output: Any, *, tool_call_id: str | None) -> Any:
    """从 Command 提取本次调用的 ToolMessage，其他结果保持原样。"""
    if not isinstance(output, Command):
        return output
    update = output.update if isinstance(output.update, dict) else {}
    for message in update.get("messages", []):
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message
    raise ValueError("工具 Command 缺少相同 call_id 的 ToolMessage")
