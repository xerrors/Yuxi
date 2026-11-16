from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from yuxi.modules.agents.runtime.middlewares.authorization import AgentExecutionRevoked
from yuxi.modules.agents.tool_results import SAVED_MEDIA_KEY, unwrap_tool_result

logger = logging.getLogger(__name__)

_RETHROW_EXCEPTIONS = (AgentExecutionRevoked, GraphBubbleUp, asyncio.CancelledError, KeyboardInterrupt, SystemExit)

_ERROR_HINT = (
    "请分析报错原因后继续：参数问题请修正后重试；该工具不支持当前输入"
    "（如路径域不匹配、文件不可达）时改用其它合适的工具或方法；"
    "同一调用连续两次同因失败后停止重试，如实向用户说明情况。"
)


class ToolErrorGuardMiddleware(AgentMiddleware):
    """在逻辑调用边界发布工具生命周期，并把普通异常隔离为错误结果。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        observation = self._observe_tool(request)
        try:
            result = handler(request)
        except _RETHROW_EXCEPTIONS as exc:
            if observation.started:
                self._emit_tool_event(request, "tool-error", message=type(exc).__name__)
            raise
        except Exception as exc:
            result = self._error_tool_message(request, exc)
        self._publish_tool_result(request, result, observation)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        observation = self._observe_tool(request)
        try:
            result = await handler(request)
        except _RETHROW_EXCEPTIONS as exc:
            if observation.started:
                self._emit_tool_event(request, "tool-error", message=type(exc).__name__)
            raise
        except Exception as exc:
            result = self._error_tool_message(request, exc)
        self._publish_tool_result(request, result, observation)
        return result

    def _publish_tool_result(self, request: ToolCallRequest, result: Any, observation: _ToolObservation) -> None:
        """最终结果决定失败与恢复；成功时保留压缩前正文。"""
        call_id = request.tool_call["id"]
        output = unwrap_tool_result(result, tool_call_id=call_id)
        # 失败优先使用最终错误，避免再次解析已被中间件处理的无效原始 Command。
        if isinstance(output, ToolMessage) and output.status == "success":
            # 图片落盘只追加保存注记。带路径的结果必须原样发布，不能换回压缩前正文。
            if not output.additional_kwargs.get(SAVED_MEDIA_KEY):
                original = unwrap_tool_result(observation.output, tool_call_id=call_id)
                if isinstance(original, ToolMessage) and original.status == "success":
                    output = original
        self._emit_tool_event(request, "tool-finished" if observation.started else "tool-result", output=output)

    @staticmethod
    def _observe_tool(request: ToolCallRequest) -> _ToolObservation:
        """回调副本只服务本次调用，保留结果压缩前的原文。"""
        observation = _ToolObservation()
        callbacks = request.runtime.config.get("callbacks")
        if callbacks is None or isinstance(callbacks, list):
            callbacks = [*(callbacks or []), observation]
        else:
            callbacks = callbacks.copy()
            callbacks.add_handler(observation, inherit=False)
        request.runtime.config["callbacks"] = callbacks
        return observation

    @staticmethod
    def _emit_tool_event(request: ToolCallRequest, event: str, **fields: Any) -> None:
        """发布包住全部中间件的调用事实，工具内部回调只提供执行进度。"""
        call = request.tool_call
        data = {"event": event, "tool_call_id": call["id"], **fields}
        request.runtime.stream_writer({"type": "yuxi.tool_lifecycle", "data": data})

    @staticmethod
    def _error_tool_message(request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_call = request.tool_call or {}
        name = str(tool_call.get("name") or "unknown")
        tool_call_id = str(tool_call.get("id") or "")
        # 工具异常文本可能包含凭据或私有路径，不能进入模型上下文或日志。
        error_type = type(exc).__name__
        logger.warning("[tool-error-guard] 工具 %s 执行异常：%s", name, error_type)
        content = f"工具 {name} 执行失败：{error_type}\n{_ERROR_HINT}"
        return ToolMessage(content=content, name=name, tool_call_id=tool_call_id, status="error")


class _ToolObservation(BaseCallbackHandler):
    """在调用局部观察真实执行，嵌套工具不能覆盖外层原始结果。"""

    run_inline = True

    def __init__(self):
        """每次逻辑调用使用独立观察对象。"""
        self.run_id = None
        self.output = None

    @property
    def started(self) -> bool:
        """只有收到 BaseTool 开始回调才认为已执行。"""
        return self.run_id is not None

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        """锁定第一次实际执行的身份。"""
        if self.run_id is None:
            self.run_id = run_id

    def on_tool_end(self, output, *, run_id, **kwargs):
        """保留实际工具返回值，外层中间件可另行压缩模型上下文。"""
        if run_id == self.run_id:
            self.output = output
