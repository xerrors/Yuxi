"""ToolMessage 生命周期审计 Repository。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.messages import MODEL_AUDIT_MESSAGE_TYPE, TOOL_AUDIT_MESSAGE_TYPE, Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.workspace.models import Project


class ToolMessageAuditRepository:
    """以当前 Run lease 为边界持久化唯一工具执行事实。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.run_repo = AgentRunRepository(db_session)

    @asynccontextmanager
    async def recent_presentations(self, uid: str):
        """按交付时间读取当前产品用户可见会话中成功的展示调用。"""
        stmt = (
            select(
                Session.thread_id,
                Session.title,
                Project.workdir_path,
                Message.extra_metadata["input"]["filepaths"].label("paths"),
                Message.finished_at,
            )
            .select_from(Session)
            .join(Project, (Project.id == Session.project_id) & (Project.uid == Session.uid))
            .join(Message, Message.session_record_id == Session.id)
            .where(
                Session.uid == uid,
                Session.app_id.is_(None),
                Session.status.in_(("active", "archived")),
                Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                Message.execution_status == "completed",
                Message.extra_metadata["tool_name"].as_string() == "present_artifacts",
                Message.finished_at.is_not(None),
            )
            .order_by(Message.finished_at.desc(), Message.id.desc())
        )
        # 删除或撤权的文件不占名额；按需读取更早的成功调用，凑齐后即释放游标。
        rows = await self.db.stream(stmt.execution_options(yield_per=20))
        try:
            yield rows
        finally:
            await rows.close()

    async def start(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        sequence: int,
        started_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Message, bool]:
        """幂等创建属于当前 Run 的工具执行事实。"""
        operation_id = tool_call_id.strip()
        normalized_name = tool_name.strip()
        if not operation_id:
            raise ValueError("Tool tool_call_id 不能为空")
        if not normalized_name:
            raise ValueError("Tool tool_name 不能为空")
        if sequence < 0:
            raise ValueError("Tool sequence 不能为负数")

        run = await self._lock_run(
            run_id=run_id,
            thread_id=thread_id,
            worker_id=worker_id,
        )
        existing = await self._get(run_id, operation_id)
        if existing is not None:
            self._require_same_owner(existing, session_record_id=run.session_record_id, turn_id=run.turn_id)
            self._require_same_start(
                existing,
                tool_name=normalized_name,
                tool_input=tool_input,
                sequence=sequence,
            )
            return existing, False

        source_message = await self._find_source_model_message(run, operation_id)
        if source_message is None:
            raise ValueError("Tool start 无法关联声明该 tool_call_id 的 Model Message")
        audit_metadata = {
            **dict(metadata or {}),
            "audit_kind": "tool",
            "tool_name": normalized_name,
            "input": dict(tool_input),
            "source_model_operation_id": source_message.operation_id,
            "source_model_message_id": source_message.id,
        }
        message = Message(
            session_record_id=run.session_record_id,
            role="tool",
            content="",
            message_type=TOOL_AUDIT_MESSAGE_TYPE,
            extra_metadata=audit_metadata,
            run_id=run.id,
            turn_id=run.turn_id,
            delivery_status="complete",
            operation_id=operation_id,
            started_at=started_at,
            sequence=sequence,
            execution_status="running",
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)

        return message, True

    async def complete(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        output: Any,
        finished_at: datetime,
        duration_ms: int | None,
        finished_sequence: int | None,
    ) -> Message:
        """完成同一工具执行事实。"""
        return await self._finish(
            run_id=run_id,
            thread_id=thread_id,
            worker_id=worker_id,
            tool_call_id=tool_call_id,
            execution_status="completed",
            output=output,
            error_message=None,
            finished_at=finished_at,
            duration_ms=duration_ms,
            finished_sequence=finished_sequence,
        )

    async def record_approval_rejection(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        reason: str,
    ) -> Message:
        """记录已接受审批的拒绝结果，不制造工具执行开始事实。"""
        from yuxi.shared.datetime import utc_now

        run = await self._lock_run(run_id=run_id, thread_id=thread_id, worker_id=worker_id)
        resume_input = await self.db.get(Message, run.input_message_id)
        if (
            resume_input is None
            or resume_input.run_id != run.id
            or resume_input.turn_id != run.turn_id
            or tool_call_id not in (resume_input.extra_metadata or {}).get("rejected_tool_calls", [])
        ):
            raise ValueError("工具拒绝结果缺少当前 Run 的已接受审批")
        source = await self._find_source_model_message(run, tool_call_id)
        if source is None:
            raise ValueError("工具拒绝结果缺少待处理的模型声明")
        declaration = next(call for call in source.extra_metadata["tool_calls"] if call["id"] == tool_call_id)
        message = Message(
            session_record_id=run.session_record_id,
            turn_id=run.turn_id,
            run_id=run.id,
            role="tool",
            message_type=TOOL_AUDIT_MESSAGE_TYPE,
            operation_id=tool_call_id,
            content="",
            delivery_status="complete",
            execution_status="failed",
            finished_at=utc_now(),
            extra_metadata={
                "audit_kind": "tool",
                "tool_name": declaration["name"],
                "input": declaration["args"],
                "output": reason,
                "error_message": reason,
                "source": "approval_rejection",
                "source_model_message_id": source.id,
                "source_model_operation_id": source.operation_id,
            },
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def fail(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        error_message: str,
        output: Any,
        finished_at: datetime,
        duration_ms: int | None,
        finished_sequence: int | None,
    ) -> Message:
        """关闭失败 ToolMessage；终态 State 可补全 stream error 缺少的 ToolMessage 内容。"""
        return await self._finish(
            run_id=run_id,
            thread_id=thread_id,
            worker_id=worker_id,
            tool_call_id=tool_call_id,
            execution_status="failed",
            output=output,
            error_message=error_message,
            finished_at=finished_at,
            duration_ms=duration_ms,
            finished_sequence=finished_sequence,
        )

    async def observe_error(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        error_message: str,
        finished_at: datetime,
        duration_ms: int | None,
        finished_sequence: int,
    ) -> Message:
        """保存裸 tool-error，最终状态由 Run failed/interrupted 事务裁决。"""
        operation_id = tool_call_id.strip()
        if not operation_id:
            raise ValueError("Tool tool_call_id 不能为空")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("Tool duration_ms 不能为负数")
        if finished_sequence < 0:
            raise ValueError("Tool finished_sequence 不能为负数")
        run = await self._lock_run(
            run_id=run_id,
            thread_id=thread_id,
            worker_id=worker_id,
        )
        message = await self._get(run_id, operation_id)
        if message is None:
            raise ValueError("Tool error 缺少对应的 start 事实")
        self._require_same_owner(message, session_record_id=run.session_record_id, turn_id=run.turn_id)
        if message.execution_status != "running":
            metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
            if metadata.get("error_message") != error_message:
                raise ValueError("已关闭 Tool 审计事实不能被不同错误覆盖")
            return message

        metadata = dict(message.extra_metadata or {})
        metadata["error_message"] = error_message
        metadata["finished_sequence"] = finished_sequence
        metadata["awaiting_run_terminal"] = True
        message.extra_metadata = metadata
        message.finished_at = finished_at
        message.duration_ms = duration_ms
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def record_unstarted_result(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        output: Any,
        error_message: str | None,
        finished_at: datetime,
        sequence: int,
    ) -> Message:
        """保存中间件直接返回的结果，不把未执行的工具计为已开始。"""
        run = await self._lock_run(run_id=run_id, thread_id=thread_id, worker_id=worker_id)
        source = await self._find_source_model_message(run, tool_call_id)
        if source is None:
            raise ValueError("工具结果缺少对应的模型声明")
        if await self._get(run_id, tool_call_id) is not None:
            raise ValueError("未执行结果不能覆盖已有工具执行")
        declaration = next(call for call in source.extra_metadata["tool_calls"] if call["id"] == tool_call_id)
        message = Message(
            session_record_id=run.session_record_id,
            turn_id=run.turn_id,
            run_id=run.id,
            role="tool",
            message_type=TOOL_AUDIT_MESSAGE_TYPE,
            operation_id=tool_call_id,
            content="",
            delivery_status="complete",
            execution_status="failed" if error_message is not None else "completed",
            finished_at=finished_at,
            sequence=sequence,
            extra_metadata={
                "audit_kind": "tool",
                "source": "middleware",
                "tool_name": declaration["name"],
                "input": declaration["args"],
                "output": output,
                "error_message": error_message,
                "source_model_operation_id": source.operation_id,
                "source_model_message_id": source.id,
                "finished_sequence": sequence,
            },
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def list_for_run(self, run_id: str) -> list[Message]:
        """按 ProtocolEvent sequence 返回 Run 的 ToolMessage。"""
        result = await self.db.execute(
            select(Message)
            .where(
                Message.run_id == run_id,
                Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                Message.role == "tool",
            )
            .order_by(Message.sequence.asc(), Message.id.asc())
        )
        return list(result.scalars().all())

    async def _finish(
        self,
        *,
        run_id: str,
        thread_id: str,
        worker_id: str,
        tool_call_id: str,
        execution_status: Literal["completed", "failed"],
        output: Any,
        error_message: str | None,
        finished_at: datetime,
        duration_ms: int | None,
        finished_sequence: int | None,
    ) -> Message:
        """原子关闭唯一工具执行事实。"""
        operation_id = tool_call_id.strip()
        if not operation_id:
            raise ValueError("Tool tool_call_id 不能为空")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("Tool duration_ms 不能为负数")
        if finished_sequence is not None and finished_sequence < 0:
            raise ValueError("Tool finished_sequence 不能为负数")

        run = await self._lock_run(
            run_id=run_id,
            thread_id=thread_id,
            worker_id=worker_id,
        )
        message = await self._get(run_id, operation_id)
        if message is None:
            raise ValueError("Tool terminal 缺少对应的 start 事实")
        self._require_same_owner(message, session_record_id=run.session_record_id, turn_id=run.turn_id)

        metadata = dict(message.extra_metadata or {})
        if message.execution_status == execution_status:
            if metadata.get("error_message") != error_message or _canonical_tool_output(metadata.get("output")) != _canonical_tool_output(
                output
            ):
                raise ValueError("已关闭 Tool 审计事实不能被不同结果覆盖")
            return message
        if message.execution_status != "running":
            raise ValueError(f"Tool 审计事实不能从 {message.execution_status} 转为 {execution_status}")

        message.finished_at = message.finished_at or finished_at
        if duration_ms is not None:
            message.duration_ms = duration_ms
        message.execution_status = execution_status
        metadata["output"] = output
        metadata["error_message"] = error_message
        if finished_sequence is not None:
            metadata["finished_sequence"] = finished_sequence
        message.extra_metadata = metadata

        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def _lock_run(self, *, run_id: str, thread_id: str, worker_id: str):
        agent_session = await SessionRepository(self.db).lock_session_by_thread_id(thread_id)
        if agent_session is None:
            raise ValueError(f"AgentRun agent_session 不存在: {thread_id}")
        run = await self.run_repo.lock_output_persistence(
            run_id,
            worker_id=worker_id,
            thread_id=thread_id,
        )
        if run is None:
            raise ValueError(f"AgentRun 不存在: {run_id}")
        return run

    async def _get(self, run_id: str, operation_id: str) -> Message | None:
        result = await self.db.execute(
            select(Message).where(
                Message.run_id == run_id,
                Message.operation_id == operation_id,
                Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
            )
        )
        return result.scalar_one_or_none()

    async def _find_source_model_message(self, run: AgentRun, tool_call_id: str) -> Message | None:
        """在当前 Run 及合法 resume 祖先中查找模型工具声明。"""
        source_run_ids = await self._source_run_ids(run)
        result = await self.db.execute(
            select(Message)
            .where(
                Message.run_id.in_(source_run_ids),
                Message.role == "assistant",
                Message.operation_id.is_not(None),
                Message.message_type == MODEL_AUDIT_MESSAGE_TYPE,
            )
            .order_by(Message.created_at.desc(), Message.sequence.desc(), Message.id.desc())
        )
        for message in result.scalars().all():
            metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
            tool_calls = metadata.get("tool_calls")
            if isinstance(tool_calls, list) and any(
                isinstance(item, dict) and str(item.get("id") or "") == tool_call_id for item in tool_calls
            ):
                return message
        return None

    async def _source_run_ids(self, run: AgentRun) -> list[str]:
        """返回同 Session 内无环的 resume 来源链。"""
        source_run_ids = [run.id]
        seen = {run.id}
        parent_id = run.resume_from_run_id
        while parent_id:
            if parent_id in seen:
                raise ValueError("Resume Run ancestry 存在循环")
            parent = await self.db.get(AgentRun, parent_id)
            if parent is None or (
                parent.session_record_id != run.session_record_id
                or parent.turn_id != run.turn_id
                or parent.uid != run.uid
                or parent.app_id != run.app_id
            ):
                raise ValueError("Resume Run ancestry 与当前 Turn 或 agent_session 不一致")
            source_run_ids.append(parent.id)
            seen.add(parent.id)
            parent_id = parent.resume_from_run_id
        return source_run_ids

    @staticmethod
    def _require_same_owner(message: Message, *, session_record_id: int, turn_id: str) -> None:
        if (
            message.session_record_id != session_record_id
            or message.turn_id != turn_id
            or message.role != "tool"
            or message.message_type != TOOL_AUDIT_MESSAGE_TYPE
        ):
            raise ValueError("Tool 审计消息必须属于同一 Turn 和 agent_session")

    @staticmethod
    def _require_same_start(
        message: Message,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        sequence: int,
    ) -> None:
        metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
        if metadata.get("tool_name") != tool_name or metadata.get("input") != tool_input or message.sequence != sequence:
            raise ValueError("重复 Tool start 与已持久化事实不一致")


def _canonical_tool_output(output: Any) -> Any:
    """忽略 LangGraph 写入 State 时补充的 ToolMessage id/name，其余原始输出必须一致。"""
    if not isinstance(output, dict):
        return output
    return {key: value for key, value in output.items() if key not in {"id", "name"}}
