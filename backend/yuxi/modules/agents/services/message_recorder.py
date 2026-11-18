"""将 LangGraph 原生模型和工具生命周期记录为 PostgreSQL 消息事实。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from urllib.parse import quote

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.repositories.model_audit import ModelMessageAuditRepository
from yuxi.modules.agents.repositories.tool_audit import ToolMessageAuditRepository
from yuxi.modules.agents.runtime.base import json_safe
from yuxi.modules.agents.tool_results import TOOL_FAILURE_MESSAGE, normalize_tool_result, unwrap_tool_result


@dataclass(slots=True)
class _ModelOperation:
    """保存单次模型生命周期的增量内容与计时。"""

    operation_id: str
    monotonic_started_at: float | None
    content_parts: list[str] = field(default_factory=list)
    content_blocks: list[dict[str, Any]] = field(default_factory=list)


class RunMessageRecorder:
    """在当前 Run owner 下串行记录模型和工具消息，提交后交给公开投影。"""

    def __init__(self, *, run_id: str, thread_id: str, worker_id: str):
        """绑定执行身份，并分别保存模型聚合与工具计时状态。"""
        self.run_id = run_id
        self.thread_id = thread_id
        self.worker_id = worker_id
        self._models: dict[tuple[str, str], _ModelOperation] = {}
        self._tools: dict[str, float | None] = {}

    async def consume(self, event: dict[str, Any]) -> dict[str, Any]:
        """记录原生事件，返回供公开适配使用的同结构事件且不修改源 payload。"""
        method = event.get("method")
        if method == "messages":
            message, metadata = event["params"]["data"]
            if (metadata or {}).get("thread_id", self.thread_id) != self.thread_id:
                return event
            if isinstance(message, dict) and isinstance(message.get("event"), str):
                await self._consume_model(event, message, dict(metadata or {}))
        elif method == "tools":
            data = dict(event["params"]["data"])
            if data.get("event") in {"tool-finished", "tool-result"}:
                data["output"] = tool_event_output(data)
                data["result"] = normalize_tool_result(
                    data["output"], message=data.get("message"), artifact_url_factory=self._artifact_preview_url
                )
                event = {**event, "params": {**event["params"], "data": data}}
            if isinstance(data.get("event"), str):
                await self._consume_tool(event, data)
        return event

    def _artifact_preview_url(self, path: str) -> str:
        """把已落盘工具图片映射为当前线程的 artifact 预览地址。"""
        quoted = "/".join(quote(segment) for segment in path.split("/") if segment)
        return f"/api/v1/agents/sessions/{self.thread_id}/artifacts/{quoted}?preview=true"

    async def _consume_model(self, event: dict[str, Any], message: dict[str, Any], metadata: dict[str, Any]) -> None:
        """按模型来源聚合内容，仅在生命周期边界落库。"""
        namespace = self._namespace(event)
        key = str(metadata.get("run_id") or metadata.get("langgraph_node") or ""), "/".join(namespace)
        event_name = message["event"]
        if event_name == "message-start":
            await self._start_model(event, message, metadata, key, namespace)
            return

        operation = self._models.get(key)
        if operation is None:
            return
        if event_name == "content-block-delta":
            delta = message.get("delta") if isinstance(message.get("delta"), dict) else {}
            if delta.get("type") == "text-delta" and isinstance(delta.get("text"), str):
                operation.content_parts.append(delta["text"])
            else:
                fields = delta.get("fields")
                if isinstance(fields, dict) and fields.get("type") == "text-delta" and isinstance(fields.get("text"), str):
                    operation.content_parts.append(fields["text"])
        elif event_name == "content-block-finish":
            content = message.get("content")
            if isinstance(content, dict):
                operation.content_blocks.append(dict(content))
        elif event_name == "message-finish":
            await self._finish_model(event, message, key, operation, namespace)

    async def _start_model(
        self,
        event: dict[str, Any],
        message: dict[str, Any],
        metadata: dict[str, Any],
        key: tuple[str, str],
        namespace: list[str],
    ) -> None:
        """提交模型开始事实后初始化增量聚合。"""
        operation_id = str(message.get("id") or metadata.get("run_id") or "").strip()
        if not operation_id:
            raise ValueError("message-start 缺少稳定 Model operation id")
        current_operation = self._models.get(key)
        if current_operation is not None and current_operation.operation_id != operation_id:
            raise ValueError("同一 Model lifecycle 不能更换 operation id")
        sequence = self._sequence(event)
        started_at = self._wall_clock(event)
        monotonic_started_at = monotonic()

        async with pg_manager.get_async_session_context() as db:
            _message, created = await ModelMessageAuditRepository(db).start(
                run_id=self.run_id,
                thread_id=self.thread_id,
                worker_id=self.worker_id,
                operation_id=operation_id,
                sequence=sequence,
                started_at=started_at,
                metadata={
                    "id": str(message.get("id") or operation_id),
                    "audit_kind": "model",
                    "namespace": namespace,
                    "model_run_id": metadata.get("run_id"),
                    "start_metadata": dict(message.get("metadata") or {}),
                },
            )
        if current_operation is None:
            self._models[key] = _ModelOperation(
                operation_id=operation_id,
                monotonic_started_at=monotonic_started_at if created else None,
            )

    async def _finish_model(
        self,
        event: dict[str, Any],
        message: dict[str, Any],
        key: tuple[str, str],
        operation: _ModelOperation,
        namespace: list[str],
    ) -> None:
        """用聚合内容和 provider usage 完成同一模型消息。"""
        finished_at = self._wall_clock(event)
        duration_ms = self._duration_ms(operation.monotonic_started_at)
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        tool_calls = [block for block in operation.content_blocks if block.get("type") == "tool_call"]

        async with pg_manager.get_async_session_context() as db:
            await ModelMessageAuditRepository(db).finish(
                run_id=self.run_id,
                thread_id=self.thread_id,
                worker_id=self.worker_id,
                operation_id=operation.operation_id,
                content="".join(operation.content_parts),
                finished_at=finished_at,
                duration_ms=duration_ms,
                usage=usage,
                metadata={
                    "namespace": namespace,
                    "content": operation.content_blocks,
                    "tool_calls": tool_calls,
                    "finished_sequence": self._sequence(event),
                    "finish_metadata": dict(message.get("metadata") or {}),
                },
            )
        self._models.pop(key, None)

    async def _consume_tool(self, event: dict[str, Any], data: dict[str, Any]) -> None:
        """记录逻辑工具调用；控制异常等待 Run 终态裁决。"""
        event_name = data["event"]
        if event_name == "tool-started":
            await self._start_tool(event, data)
        elif event_name == "tool-result":
            async with pg_manager.get_async_session_context() as db:
                await ToolMessageAuditRepository(db).record_unstarted_result(
                    run_id=self.run_id,
                    thread_id=self.thread_id,
                    worker_id=self.worker_id,
                    tool_call_id=self._tool_call_id(data),
                    output=data["output"],
                    error_message=data["result"].error,
                    finished_at=self._wall_clock(event),
                    sequence=self._sequence(event),
                )
        elif event_name == "tool-finished":
            output = data.get("output")
            result = data["result"]
            await self._close_tool(
                event=event,
                tool_call_id=self._tool_call_id(data),
                output=output,
                error_message=result.error,
            )
        elif event_name == "tool-error":
            await self._close_tool(
                event=event,
                tool_call_id=self._tool_call_id(data),
                output=None,
                error_message=str(data.get("message") or TOOL_FAILURE_MESSAGE),
                wait_for_run_terminal=True,
            )

    async def _start_tool(self, event: dict[str, Any], data: dict[str, Any]) -> None:
        """提交工具开始事实，并保留首次观察的单调时钟起点。"""
        tool_call_id = self._tool_call_id(data)
        tool_name = str(data.get("tool_name") or "").strip()
        tool_input = data.get("input")
        if tool_input is None:
            tool_input = {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool-started input 必须是对象")
        sequence = self._sequence(event)
        started_at = self._wall_clock(event)
        monotonic_started_at = monotonic()

        async with pg_manager.get_async_session_context() as db:
            _message, created = await ToolMessageAuditRepository(db).start(
                run_id=self.run_id,
                thread_id=self.thread_id,
                worker_id=self.worker_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_input=dict(tool_input),
                sequence=sequence,
                started_at=started_at,
                metadata={"namespace": self._namespace(event)},
            )
        if created:
            self._tools[tool_call_id] = monotonic_started_at
        else:
            self._tools.setdefault(tool_call_id, None)

    async def _close_tool(
        self,
        *,
        event: dict[str, Any],
        tool_call_id: str,
        output: Any,
        error_message: str | None,
        wait_for_run_terminal: bool = False,
    ) -> None:
        """在独立短事务中保存工具结果或等待终态的错误观察。"""
        duration_ms = self._duration_ms(self._tools.get(tool_call_id))
        kwargs = {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "worker_id": self.worker_id,
            "tool_call_id": tool_call_id,
            "output": output,
            "finished_at": self._wall_clock(event),
            "duration_ms": duration_ms,
            "finished_sequence": self._sequence(event),
        }
        async with pg_manager.get_async_session_context() as db:
            repository = ToolMessageAuditRepository(db)
            if wait_for_run_terminal:
                await repository.observe_error(
                    run_id=self.run_id,
                    thread_id=self.thread_id,
                    worker_id=self.worker_id,
                    tool_call_id=tool_call_id,
                    error_message=error_message or TOOL_FAILURE_MESSAGE,
                    finished_at=kwargs["finished_at"],
                    duration_ms=duration_ms,
                    finished_sequence=kwargs["finished_sequence"],
                )
            elif error_message is None:
                await repository.complete(**kwargs)
            else:
                await repository.fail(error_message=error_message, **kwargs)
        self._tools.pop(tool_call_id, None)

    @staticmethod
    def _tool_call_id(data: dict[str, Any]) -> str:
        """读取用于绑定声明与结果的稳定工具调用身份。"""
        tool_call_id = str(data.get("tool_call_id") or "").strip()
        if not tool_call_id:
            raise ValueError("Tool lifecycle 缺少稳定 tool_call_id")
        return tool_call_id

    @staticmethod
    def _sequence(event: dict[str, Any]) -> int:
        """校验消息生命周期的原生执行序号。"""
        sequence = event.get("seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("Message lifecycle 缺少有效 ProtocolEvent seq")
        return sequence

    @staticmethod
    def _wall_clock(event: dict[str, Any]) -> datetime:
        """将原生毫秒时间戳转为 PostgreSQL UTC 时间。"""
        timestamp = event["params"].get("timestamp")
        if not isinstance(timestamp, int | float) or isinstance(timestamp, bool):
            raise ValueError("Message lifecycle 缺少有效 params.timestamp")
        return datetime.fromtimestamp(timestamp / 1000, UTC)

    @staticmethod
    def _namespace(event: dict[str, Any]) -> list[str]:
        """保留原生执行来源。"""
        namespace = event["params"].get("namespace")
        return [str(item) for item in namespace] if isinstance(namespace, list) else []

    @staticmethod
    def _duration_ms(started_at: float | None) -> int | None:
        """仅从本进程的单调时钟计算耗时。"""
        return max(0, round((monotonic() - started_at) * 1000)) if started_at is not None else None


def tool_event_output(data: dict) -> Any:
    """从工具 Command 取出相同 call_id 的结果，保留执行身份。"""
    return json_safe(unwrap_tool_result(data.get("output"), tool_call_id=data.get("tool_call_id")))
