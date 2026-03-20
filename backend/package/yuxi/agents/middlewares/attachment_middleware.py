"""附件注入中间件 - 使用 LangChain 标准中间件实现

从 State 中读取附件信息，注入提示词让模型使用 read_file 工具读取附件内容。
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
    """扩展 AgentState 以支持附件"""

    attachments: NotRequired[list[dict]]


def _build_attachment_prompt(attachments: Sequence[dict]) -> str | None:
    """Render attachments into a system prompt block with file paths.

    提示模型使用 read_file 工具读取附件内容。
    """
    if not attachments:
        return None

    valid_attachments = [a for a in attachments if a.get("status") == "parsed"]

    if not valid_attachments:
        return None

    attachment_infos: list[str] = []
    for attachment in valid_attachments:
        file_name = attachment.get("file_name", "未知文件")
        file_path = attachment.get("file_path", "")
        truncated = "（已截断）" if attachment.get("truncated") else ""

        if file_path:
            attachment_infos.append(f"- {file_name}{truncated}: {file_path}")
        else:
            attachment_infos.append(f"- {file_name}{truncated}")

    lines = [
        "用户上传了以下附件：",
        "",
        *attachment_infos,
        "",
        "请使用 read_file 工具读取附件内容后，再回答用户的问题。",
    ]

    return "\n".join(lines)


class AttachmentMiddleware(AgentMiddleware[AttachmentState]):
    """
    LangChain 标准中间件：从 State 中读取附件并注入提示词。

    LangGraph 会自动从 checkpointer 恢复 state，包括 attachments。
    从 request.state 中读取附件，将其转换为上下文块 并注入到系统提示词中。
    """

    state_schema = AttachmentState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        # 从 state 获取附件（LangGraph 自动从 checkpointer 恢复）
        attachments = request.state.get("attachments", [])
        logger.info(f"AttachmentMiddleware: found {len(attachments)} attachments in state")

        if attachments:
            # 构建附件提示
            attachment_prompt = _build_attachment_prompt(attachments)

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


# 创建中间件实例，供其他模块使用
save_attachments_to_fs = AttachmentMiddleware()

# 保留旧名称以保持向后兼容（已废弃）
inject_attachment_context = save_attachments_to_fs
