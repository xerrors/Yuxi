"""Attachment prompt injection middleware.

Read uploaded file metadata from LangGraph state and inject readable paths
into the system prompt so the model can use `read_file` on demand.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from yuxi.utils import logger

ATTACHMENT_PROMPT_MARKER = "<!-- attachment_context -->"


class AttachmentState(AgentState):
    """Extended state schema with uploaded files."""

    uploads: NotRequired[list[dict]]


def _build_attachment_prompt(uploads: Sequence[dict]) -> str | None:
    """Render uploads into a concise prompt block."""
    if not uploads:
        return None

    valid_uploads: list[tuple[str, str]] = []
    for upload in uploads:
        path = upload.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        file_name = upload.get("file_name", "未知文件")
        valid_uploads.append((str(file_name), path))

    if not valid_uploads:
        return None

    upload_infos = [f"- {file_name}: {path}" for file_name, path in valid_uploads]
    lines = [
        "用户上传了以下文件：",
        "",
        *upload_infos,
        "",
        "请优先使用 `read_file` 工具读取这些路径中的文件内容，再回答用户问题。",
    ]
    return "\n".join(lines)


class AttachmentMiddleware(AgentMiddleware[AttachmentState]):
    """Inject upload context from state.uploads into system prompt."""

    state_schema = AttachmentState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        uploads = request.state.get("uploads", [])
        logger.info(f"AttachmentMiddleware: found {len(uploads)} uploads in state")

        if uploads:
            attachment_prompt = _build_attachment_prompt(uploads)
            if attachment_prompt:
                logger.info("AttachmentMiddleware: injecting attachment prompt")
                existing_blocks = list(request.system_message.content_blocks) if request.system_message else []
                existing_text = "\n".join(
                    block.get("text", "")
                    for block in existing_blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                )

                if ATTACHMENT_PROMPT_MARKER in existing_text:
                    logger.info("AttachmentMiddleware: attachment prompt already injected, skip")
                    return await handler(request)

                merged_blocks = existing_blocks + [
                    {"type": "text", "text": f"{ATTACHMENT_PROMPT_MARKER}\n{attachment_prompt}"}
                ]
                request = request.override(system_message=SystemMessage(content=merged_blocks))

        return await handler(request)


save_attachments_to_fs = AttachmentMiddleware()
inject_attachment_context = save_attachments_to_fs
