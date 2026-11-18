"""把工具结果中的图片块持久化为 Workdir 媒体文件，供模型引用与用户回看。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import functools
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from yuxi.infrastructure.filesystem import await_io
from yuxi.modules.agents.tool_results import MEDIA_NOTE_PREFIX, SAVED_MEDIA_KEY
from yuxi.modules.workspace.runtime_files import RuntimeFiles
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path, workdir_tool_media_dir

logger = logging.getLogger(__name__)

# 只落盘可安全命名且浏览器可内联预览的图片类型。
_MEDIA_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class ToolMediaPersistMiddleware(AgentMiddleware):
    """在工具结果进入状态前把 image 块写入 outputs/tool_media 并附注路径。

    装配位置在 ToolErrorGuard 内层：Guard 发布审计与公开投影时能拿到
    带 SAVED_MEDIA_KEY 的结果，模型上下文与前端回看共享同一份路径事实。
    """

    def __init__(self, *, uid: str, media_dir: str):
        """身份与目录由运行上下文提供，写失败只降级不阻塞工具链路。"""
        self._files = RuntimeFiles(uid)
        self._media_dir = media_dir

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        result = handler(request)
        return self._persist_result(request, result, self._scan_and_write)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        result = await handler(request)

        async def scan_and_write(message: ToolMessage, tool_name: str) -> list[dict]:
            saved = []
            for index, block in enumerate(message.content):
                record = await self._amedia_record(tool_name, block, index)
                if record is not None:
                    saved.append(record)
            return saved

        return await self._apersist_result(request, result, scan_and_write)

    def _persist_result(self, request: ToolCallRequest, result: Any, scan_and_write: Callable[[ToolMessage, str], list[dict]]) -> Any:
        message, rebuild = _result_message(result, request.tool_call.get("id"))
        if message is None:
            return result
        try:
            saved = scan_and_write(message, str(request.tool_call.get("name") or ""))
        except Exception as exc:
            # 异常类型不含凭据；失败时保持原结果，公开投影退回既有行为。
            logger.warning("[tool-media] 保存工具图片失败：%s", type(exc).__name__)
            return result
        return _annotated_or_original(message, saved, result, rebuild)

    async def _apersist_result(
        self, request: ToolCallRequest, result: Any, scan_and_write: Callable[[ToolMessage, str], Awaitable[list[dict]]]
    ) -> Any:
        message, rebuild = _result_message(result, request.tool_call.get("id"))
        if message is None:
            return result
        try:
            saved = await scan_and_write(message, str(request.tool_call.get("name") or ""))
        except Exception as exc:
            logger.warning("[tool-media] 保存工具图片失败：%s", type(exc).__name__)
            return result
        return _annotated_or_original(message, saved, result, rebuild)

    def _scan_and_write(self, message: ToolMessage, tool_name: str) -> list[dict]:
        saved = []
        for index, block in enumerate(message.content):
            record = self._media_record(tool_name, block, index)
            if record is None:
                continue
            self._files.write(record["path"], record["payload"], overwrite=True)
            saved.append(record)
        return saved

    async def _amedia_record(self, tool_name: str, block: Any, index: int) -> dict | None:
        record = self._media_record(tool_name, block, index)
        if record is None:
            return None
        write = functools.partial(self._files.write, record["path"], record["payload"], overwrite=True)
        await await_io(asyncio.to_thread(write))
        return record

    def _media_record(self, tool_name: str, block: Any, index: int) -> dict | None:
        """内容寻址命名保证同图幂等；缺失数据或非白名单类型直接跳过。"""
        if not isinstance(block, dict) or block.get("type") != "image":
            return None
        data_b64 = block.get("base64")
        mime = block.get("mime_type") or block.get("mimeType")
        source = block.get("source")
        if isinstance(source, dict):
            data_b64 = data_b64 if isinstance(data_b64, str) else source.get("data")
            mime = mime if isinstance(mime, str) else source.get("media_type")
        ext = _MEDIA_EXTENSIONS.get(str(mime or "").lower())
        if not isinstance(data_b64, str) or not data_b64 or ext is None:
            return None
        try:
            payload = base64.b64decode(data_b64)
        except (binascii.Error, ValueError):
            logger.warning("[tool-media] 工具图片 base64 解码失败，已跳过")
            return None
        if not payload:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool_name.strip()).strip(".-") or "tool"
        path = f"{self._media_dir}/{safe_name}-{digest[:16]}{ext}"
        return {"index": index, "path": path, "mime_type": str(mime), "payload": payload}


def _annotated_or_original(message: ToolMessage, saved: list[dict], result: Any, rebuild: Callable[[ToolMessage], Any]) -> Any:
    """保留 image 块供模型视觉使用，只追加路径注记与投影元数据。"""
    if not saved:
        return result
    paths = "\n".join(record["path"] for record in saved)
    note = {"type": "text", "text": f"{MEDIA_NOTE_PREFIX}\n{paths}"}
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs[SAVED_MEDIA_KEY] = [
        {"index": record["index"], "path": record["path"], "mime_type": record["mime_type"]} for record in saved
    ]
    updated = message.model_copy(update={"content": [*message.content, note], "additional_kwargs": additional_kwargs})
    return rebuild(updated)


def _result_message(result: Any, tool_call_id: Any) -> tuple[ToolMessage | None, Callable[[ToolMessage], Any]]:
    """定位本次调用可落盘的 ToolMessage；Command 结果按位重建。"""
    if isinstance(result, ToolMessage):
        if result.tool_call_id != tool_call_id or result.status != "success":
            return None, result
        return result, lambda updated: updated
    if isinstance(result, Command):
        update = result.update if isinstance(result.update, dict) else {}
        messages = update.get("messages")
        if not isinstance(messages, list):
            return None, result
        for position, message in enumerate(messages):
            if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id and message.status == "success":
                return message, lambda updated, _update=update, _position=position: dataclasses.replace(
                    result,
                    update={**_update, "messages": [*_update["messages"][:_position], updated, *_update["messages"][_position + 1 :]]},
                )
    return None, result


def create_tool_media_middleware(context) -> ToolMediaPersistMiddleware | None:
    """没有用户或 Workdir 时不装配。不读取沙盒 backend。"""
    uid = str(getattr(context, "uid", "") or "").strip()
    workdir = str(getattr(context, "workdir_relative_path", "") or "").strip()
    if not uid or not workdir:
        return None
    media_dir = workdir_tool_media_dir(runtime_workdir_path(workdir))
    return ToolMediaPersistMiddleware(uid=uid, media_dir=media_dir)
