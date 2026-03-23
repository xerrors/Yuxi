from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from yuxi.utils.logging_config import logger


class ToolErrorBoundaryMiddleware(AgentMiddleware):
    """将工具异常转成错误 ToolMessage，避免中断整条 stream。"""

    def _build_error_message(self, request: ToolCallRequest, error: Exception) -> ToolMessage:
        tool_call = request.tool_call or {}
        tool_name = str(tool_call.get("name") or "unknown_tool")
        tool_call_id = str(tool_call.get("id") or f"{tool_name}_error")
        logger.exception("Tool call failed: %s", tool_name, exc_info=error)
        return ToolMessage(
            content=f"Tool `{tool_name}` failed: {error}",
            name=tool_name,
            tool_call_id=tool_call_id,
            status="error",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except Exception as error:
            return self._build_error_message(request, error)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except Exception as error:
            return self._build_error_message(request, error)
