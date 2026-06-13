"""无状态 Agent 调用接口（RAG 评估 Agent 模式地基）。

提供一个脱离对话上下文、单 query 进、最终答案 + 工具调用 trace 出的 Agent 入口，
供评估器（以及其他需要程序化调用 Agent 的内部场景）直接 import 调用。

与生产对话链路（agent_run_service / chat_service）的区别：
- 不创建 conversation、不写 run / message 记录、不入 ARQ 队列、不做 SSE 流式消费。
- 使用一次性 thread_id，避免污染真实对话的 checkpointer。
- 以调用方传入的 uid 运行，Agent 按该用户可见的知识库 / Skills / 工具资源运行。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import build_agent_input_context, normalize_agent_context_config
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


@dataclass(frozen=True)
class AgentInvokeResult:
    """单次无状态 Agent 调用的结果。"""

    answer: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


def _message_to_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if isinstance(message, dict):
        return dict(message)
    return {"content": str(message)}


def _extract_answer(messages: list[Any]) -> str:
    """取最后一条 AIMessage 的文本内容作为最终答案。

    content 可能是字符串，也可能是内容块列表（Anthropic/Claude、多模态模型常见），
    后者需要拼接其中的 text 块，避免把列表的 repr 当作答案返回给评估器。
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return ""
    return ""


def _extract_tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    """从 trace 中提取工具调用及其结果。

    AIMessage.tool_calls 给出调用入参，ToolMessage 按 tool_call_id 给出对应输出。
    评估器据此还原 Agent 实际检索 / 调用过程（如知识库检索工具命中的 chunk）。
    """
    outputs_by_call_id: dict[str, Any] = {
        message.tool_call_id: message.content
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }

    tool_calls: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            call_id = call.get("id")
            tool_calls.append(
                {
                    "id": call_id,
                    "name": call.get("name"),
                    "args": call.get("args"),
                    "output": outputs_by_call_id.get(call_id),
                }
            )
    return tool_calls


async def invoke_agent_once(
    *,
    db: AsyncSession,
    user: User,
    agent_id: str,
    query: str,
    model_spec: str | None = None,
) -> AgentInvokeResult:
    """以调用方传入的用户身份，对指定智能体执行一次无状态调用。

    Args:
        db: 数据库会话，用于解析智能体配置与用户可见资源。
        user: 调用方用户，决定 Agent 可访问的知识库 / Skills / 工具范围。
        agent_id: 智能体 slug。
        query: 单条用户问题。
        model_spec: 可选的模型覆盖，留空时使用智能体配置或系统默认模型。

    Returns:
        AgentInvokeResult：最终答案、完整消息 trace、提取出的工具调用 trace。
    """
    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_id, user=user)
    if not agent_item:
        raise ValueError("智能体不存在或无权限访问")

    backend = agent_manager.get_agent(agent_item.backend_id)
    if not backend:
        raise ValueError(f"智能体后端 {agent_item.backend_id} 不存在")

    agent_config = await normalize_agent_context_config(
        (agent_item.config_json or {}).get("context", {}),
        db=db,
        user=user,
        context_schema=backend.context_schema,
    )

    thread_id = f"eval-{uuid.uuid4().hex}"
    uid = str(user.uid)
    input_context = await build_agent_input_context(agent_config, thread_id=thread_id, uid=uid)
    if model_spec:
        input_context["model"] = model_spec

    logger.debug(f"invoke_agent_once: agent={agent_id} uid={uid} thread_id={thread_id}")
    invoke_result = await backend.invoke_messages([HumanMessage(content=query)], input_context=input_context)

    messages = invoke_result.get("messages", []) if isinstance(invoke_result, dict) else []
    return AgentInvokeResult(
        answer=_extract_answer(messages),
        messages=[_message_to_dict(message) for message in messages],
        tool_calls=_extract_tool_calls(messages),
    )
