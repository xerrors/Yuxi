"""Agent run repository."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.messages import AUDIT_MESSAGE_TYPES, TOOL_AUDIT_MESSAGE_TYPE, Message
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES, AgentRun, AgentRunAttempt
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.shared.datetime import utc_now

TERMINAL_RUN_STATUSES = set(AGENT_RUN_TERMINAL_STATUSES)
LEASED_RUN_STATUSES = {"running", "cancel_requested"}
RUN_STATUS_TO_DELIVERY_STATUS = {
    "completed": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
    "yielded": "complete",
}

TOP_LEVEL_RUN_TYPES = ("chat", "resume")


class AgentRunRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one_or_none()

    async def list_agent_runs_for_trace(self, session_record_id: int, *, limit: int) -> tuple[list[AgentRun], bool]:
        """按创建顺序返回有界 AgentRun 调试事实。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.session_record_id == session_record_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit + 1)
        )
        runs = list(result.scalars().all())
        truncated = len(runs) > limit
        return list(reversed(runs[:limit])), truncated

    async def get_for_scope(self, *, run_id: str, thread_id: str, turn_id: str, uid: str, app_id: str | None) -> AgentRun | None:
        """按完整执行绑定读取 Run，不从相邻执行推断。"""
        return await self.db.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.thread_id == thread_id,
                AgentRun.turn_id == turn_id,
                AgentRun.uid == uid,
                AgentRun.app_id == app_id,
            )
        )

    async def get_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid))))
        return result.scalar_one_or_none()

    async def list_thread_runs_after_sequence(
        self, *, thread_id: str, uid: str, app_id: str | None, after_sequence: int, limit: int = 100
    ) -> list[AgentRun]:
        """按当前 Thread 的执行序号读取 Run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.uid == uid,
                AgentRun.app_id == app_id,
                AgentRun.execution_seq > after_sequence,
            )
            .order_by(AgentRun.execution_seq)
            .limit(limit)
        )
        return list(result.scalars())

    async def lock_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        """锁定用户 Run，串行化状态转换。"""

        result = await self.db.execute(
            select(AgentRun)
            .where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid)))
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str) -> AgentRun | None:
        """读取线程最近一次 run，用于恢复查询 checkpoint 时的运行时模型。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type.in_(["chat", "resume"]),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_top_level_runs_for_threads(self, uid: str, thread_ids: list[str]) -> dict[str, tuple[str, str]]:
        """批量读取各线程最新顶层 chat/resume run，返回 thread_id -> (run_id, status)。

        使用窗口函数一次查询完成，避免对每个线程执行 N+1 查询。
        """
        if not thread_ids:
            return {}

        ranked = (
            select(
                AgentRun.id,
                AgentRun.status,
                AgentRun.thread_id,
                func.row_number()
                .over(
                    partition_by=AgentRun.thread_id,
                    order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
                )
                .label("rn"),
            )
            .where(
                AgentRun.uid == str(uid),
                AgentRun.thread_id.in_(thread_ids),
                AgentRun.run_type.in_(TOP_LEVEL_RUN_TYPES),
            )
            .subquery()
        )
        result = await self.db.execute(select(ranked).where(ranked.c.rn == 1))
        return {row.thread_id: (row.id, row.status) for row in result.all()}

    async def get_active_run_by_thread_for_user(
        self,
        *,
        agent_slug: str,
        thread_id: str,
        uid: str,
    ) -> AgentRun | None:
        """检查同一用户、智能体、线程上是否已有未结束 run，避免并发写同一线程。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.agent_slug == agent_slug,
                AgentRun.uid == str(uid),
                AgentRun.thread_id == thread_id,
                or_(
                    AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
                    AgentRun.runtime_cleanup_pending.is_(True),
                ),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        runtime_scope_id: str | None = None,
        agent_slug: str,
        uid: str,
        turn_id: str,
        input_payload: dict,
        source: str = "chat",
        channel: str = "web",
        external_id: str | None = None,
        origin_metadata: dict | None = None,
        session_record_id: int | None = None,
        created_by_run_id: str | None = None,
        resume_from_run_id: str | None = None,
        run_type: str = "chat",
        input_message_id: int | None = None,
        app_id: str | None = None,
        api_key_id: int | None = None,
    ) -> AgentRun:
        """登记直接绑定 Turn 的执行段。"""
        turn = await self.db.get(AgentTurn, turn_id)
        if turn is None or turn.uid != str(uid) or turn.app_id != app_id:
            raise ValueError("Run 缺少同作用域 Turn")
        if turn.thread_id != thread_id:
            raise ValueError("顶层 Run 必须属于 Turn 的 Thread")
        runtime_scope = str(thread_id) if runtime_scope_id is None else str(runtime_scope_id).strip()
        run = AgentRun(
            id=run_id,
            thread_id=thread_id,
            runtime_scope_id=runtime_scope,
            agent_slug=agent_slug,
            uid=str(uid),
            turn_id=turn_id,
            app_id=app_id,
            api_key_id=api_key_id,
            source=source,
            channel=channel,
            external_id=external_id,
            origin_metadata=origin_metadata or {},
            session_record_id=session_record_id,
            created_by_run_id=created_by_run_id,
            resume_from_run_id=resume_from_run_id,
            run_type=run_type,
            input_message_id=input_message_id,
            input_payload=input_payload or {},
            status="pending",
            runtime_cleanup_pending=False,
        )
        self.db.add(run)
        await self.db.flush()
        await self.db.refresh(run, ["execution_seq"])
        return run

    async def set_langfuse_trace_id(
        self,
        run_id: str,
        trace_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """由当前 attempt 在执行前幂等固化 Run 的 Langfuse trace。"""
        normalized_trace_id = trace_id.strip()
        if not normalized_trace_id:
            raise ValueError("trace_id 不能为空")
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")

        run = await self._lock_run(run_id)
        if run is None:
            return None

        current_time = now or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="固化 Langfuse trace")
        if run.langfuse_trace_id and run.langfuse_trace_id != normalized_trace_id:
            raise ValueError("AgentRun 已绑定不同的 Langfuse trace")

        run.langfuse_trace_id = normalized_trace_id
        run.updated_at = current_time
        await self.db.flush()
        return run

    async def set_langfuse_observation_id(
        self,
        run_id: str,
        observation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """由当前 owner 一次性固定 Run observation 身份。"""
        normalized_id = observation_id.strip()
        if not normalized_id or len(normalized_id) > 16:
            raise ValueError("observation_id 长度无效")
        run = await self._lock_run(run_id)
        if run is None:
            return None
        current_time = now or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="固化 Langfuse observation")
        if run.langfuse_observation_id not in (None, normalized_id):
            raise ValueError("Run 已绑定不同的 Langfuse observation")
        run.langfuse_observation_id = normalized_id
        run.updated_at = current_time
        await self.db.flush()
        return run

    async def set_input_message(self, run_id: str, message_id: int) -> AgentRun | None:
        """绑定已派发且属于同一 Turn/Run 的输入消息。"""
        run = await self._lock_run(run_id)
        if run is None:
            return None
        message = await self.db.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.run_id == run.id,
                Message.turn_id == run.turn_id,
                Message.role == "user",
            )
        )
        if message is None:
            raise ValueError("输入消息必须属于同一 Run 和 Turn")
        run.input_message_id = message_id
        await self.db.flush()
        return run

    async def set_output_message(
        self,
        run_id: str,
        message_id: int,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """仅允许当前 attempt 绑定属于本 Run 的 assistant 输出。"""

        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")

        run = await self._lock_run(run_id)
        if not run:
            return None

        current_time = now or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="持久化输出消息")

        message = await self._get_matching_output_message(run, message_id)
        if message is None:
            raise ValueError("输出消息必须属于同一 agent_session、Turn 和 Run，且角色为 assistant")

        run.output_message_id = message_id
        run.updated_at = current_time
        await self.db.flush()
        return run

    async def lock_output_persistence(
        self,
        run_id: str,
        *,
        worker_id: str,
        thread_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """在任何输出写入前锁定并验证当前 attempt 的完整因果边界。"""

        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        run = await self._lock_run(run_id)
        if run is None:
            return None

        self._require_lease_owner(run, worker_id=worker_id, now=now or utc_now(), action="持久化输出消息")
        if run.thread_id != thread_id:
            raise ValueError("AgentRun 输出必须属于同一 thread")
        if run.session_record_id is None:
            raise ValueError("AgentRun 输出缺少 agent_session 归属")
        return run

    async def lock_cancel_snapshot(self, run_id: str, *, worker_id: str, thread_id: str) -> AgentRun:
        """仅允许仍持有效 lease 的取消执行保存已经公开的部分正文。"""
        run = await self._lock_run(run_id)
        if (
            run is None
            or run.status != "cancel_requested"
            or run.worker_id != worker_id
            or not worker_id.strip()
            or run.lease_expires_at is None
            or run.lease_expires_at <= utc_now()
            or run.thread_id != thread_id
            or run.session_record_id is None
        ):
            raise ValueError("取消快照必须由相同 Thread 的当前有效 lease owner 保存")
        return run

    async def lock_memory_write(
        self,
        run_id: str,
        *,
        uid: str,
        worker_id: str,
        thread_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """锁定并验证允许写入用户 Memory 的当前顶层 attempt。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        run = await self._lock_run(run_id)
        if run is None:
            return None

        self._require_lease_owner(run, worker_id=worker_id, now=now or utc_now(), action="写入 Memory")
        if run.uid != str(uid) or run.thread_id != thread_id or run.run_type not in TOP_LEVEL_RUN_TYPES:
            raise ValueError("Memory 写入必须属于当前用户的同一顶层 Run 和 thread")
        return run

    async def mark_running(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        """由一个 worker 原子取得或续接尚未过期的 Run ownership。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        run = await self._lock_run(run_id)
        if not run:
            return None, False
        if run.status in TERMINAL_RUN_STATUSES:
            return run, False
        if run.runtime_cleanup_pending:
            return run, False

        current_time = now or utc_now()
        initial_claim = run.status == "pending" or (run.status == "cancel_requested" and run.worker_id is None)
        same_live_owner = (
            run.status in LEASED_RUN_STATUSES
            and run.worker_id == worker_id
            and run.lease_expires_at is not None
            and run.lease_expires_at > current_time
        )
        if not initial_claim and not same_live_owner:
            return run, False

        if initial_claim:
            # Steer 接续可被提前补投；旧 Run 的环境必须先确认清理。
            if await self.has_thread_cleanup(run.thread_id):
                return run, False
            from yuxi.modules.agents.repositories.cooperation import CooperationRepository

            if not await CooperationRepository(self.db).claim_execution_slot(run):
                return run, False

        if run.status == "pending":
            run.status = "running"
        run.worker_id = worker_id
        run.heartbeat_at = current_time
        run.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
        run.started_at = run.started_at or current_time
        run.updated_at = current_time
        if initial_claim:
            await self._close_open_attempts(
                run.id,
                outcome="lease_expired",
                error_type="worker_lease_expired",
                error_message="执行占有人在取得新所有权前已失联。",
                now=current_time,
            )
            max_attempt_no = await self.db.scalar(
                select(func.coalesce(func.max(AgentRunAttempt.attempt_no), 0)).where(AgentRunAttempt.run_id == run.id)
            )
            self.db.add(
                AgentRunAttempt(
                    run_id=run.id,
                    attempt_no=int(max_attempt_no or 0) + 1,
                    worker_id=worker_id,
                    started_at=current_time,
                    heartbeat_at=current_time,
                    lease_expires_at=run.lease_expires_at,
                )
            )
        else:
            attempt = await self._get_open_attempt(run_id, worker_id=worker_id)
            if attempt is not None:
                attempt.heartbeat_at = current_time
                attempt.lease_expires_at = run.lease_expires_at
                attempt.updated_at = current_time
        await self.db.flush()
        return run, True

    async def renew_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        """仅允许当前且尚未过期的 owner 续租。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        run = await self._lock_run(run_id)
        current_time = now or utc_now()
        if (
            not run
            or run.status not in LEASED_RUN_STATUSES
            or run.worker_id != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= current_time
        ):
            return False

        run.heartbeat_at = current_time
        run.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
        run.updated_at = current_time
        attempt = await self._get_open_attempt(run_id, worker_id=worker_id)
        if attempt is not None:
            attempt.heartbeat_at = current_time
            attempt.lease_expires_at = run.lease_expires_at
            attempt.updated_at = current_time
        await self.db.flush()
        return True

    async def release_lease_for_retry(
        self,
        run_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        """仅由 lease 尚有效的当前 attempt 释放 retry ownership。"""
        run = await self._lock_run(run_id)
        current_time = now or utc_now()
        if (
            not run
            or run.status != "running"
            or run.worker_id != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= current_time
        ):
            return False

        run.status = "pending"
        run.worker_id = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.runtime_cleanup_pending = True
        run.updated_at = current_time
        await self._finish_open_attempt(
            run_id,
            worker_id=worker_id,
            outcome="retry_released",
            now=current_time,
        )
        await self.db.flush()
        return True

    async def list_expired_lease_candidates(self, *, now: datetime | None = None) -> list[tuple[str, str, str, str | None]]:
        """只读失联候选，供 worker 按 Thread→Turn→Run 锁顺序处理。"""
        current_time = now or utc_now()
        result = await self.db.execute(
            select(AgentRun.id, AgentRun.thread_id, AgentRun.uid, AgentRun.app_id)
            .where(self._expired_lease_condition(current_time))
            .order_by(
                AgentRun.runtime_scope_id,
                AgentRun.created_at,
                AgentRun.id,
            )
        )
        return [(str(run_id), str(root_thread_id), str(uid), app_id) for run_id, root_thread_id, uid, app_id in result]

    async def reconcile_expired_lease(self, run_id: str, *, now: datetime | None = None) -> AgentRun | None:
        """在调用方已锁 Thread 和 Turn 后，锁单 Run 并收敛失联事实。"""
        current_time = now or utc_now()
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.id == run_id, self._expired_lease_condition(current_time))
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None

        run.status = "failed"
        run.error_type = "worker_lease_expired"
        run.error_message = "执行 worker 的 lease 已过期；本次运行结果未知，需按 at-least-once 语义检查副作用。"
        run.finished_at = current_time
        run.updated_at = current_time
        run.worker_id = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.runtime_cleanup_pending = True
        await self._project_input_delivery_status(run)
        await self._close_running_audits(run.id, execution_status="abandoned", now=current_time)
        await self._close_open_attempts(
            run.id,
            outcome="lease_expired",
            error_type="worker_lease_expired",
            error_message="执行 worker 的 lease 已过期；本次运行结果未知。",
            now=current_time,
        )
        await self.db.flush()
        return run

    async def request_cancel_run(self, *, run_id: str, uid: str) -> tuple[AgentRun | None, list[str]]:
        """精确请求取消当前 Run，不隐式影响其他 Session。"""
        run = await self.lock_run_for_user(run_id, str(uid))
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return run, []
        await self._request_cancel_locked(run)
        return run, [run.id]

    async def _request_cancel_locked(self, run: AgentRun) -> None:
        """转换一条已由当前事务锁定的 Run。"""
        if run.status in TERMINAL_RUN_STATUSES:
            return
        current_time = utc_now()
        if run.status == "pending" and run.worker_id is None:
            run.status = "cancelled"
            run.error_type = "cancelled"
            run.error_message = "对话已在等待执行时取消"
            run.finished_at = current_time
            run.updated_at = current_time
            await self._project_input_delivery_status(run)
            await self.db.flush()
            return
        run.status = "cancel_requested"
        run.updated_at = current_time
        await self.db.flush()

    async def set_terminal_status(
        self,
        run_id: str,
        *,
        status: str,
        error_type: str | None = None,
        error_message: str | None = None,
        token_usage: dict | None = None,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"不支持的 AgentRun 终态：{status}")

        run = await self._lock_run(run_id)
        if not run:
            return None, False
        if run.status in TERMINAL_RUN_STATUSES:
            if run.worker_id is not None or run.heartbeat_at is not None or run.lease_expires_at is not None:
                run.worker_id = None
                run.heartbeat_at = None
                run.lease_expires_at = None
                await self.db.flush()
            return run, False

        current_time = now or utc_now()
        if run.status == "pending":
            if worker_id is not None or status not in {"failed", "cancelled"}:
                return run, False
        elif run.status in LEASED_RUN_STATUSES:
            if run.worker_id != worker_id or run.lease_expires_at is None or run.lease_expires_at <= current_time:
                return run, False
            if run.status == "cancel_requested" and status != "cancelled":
                return run, False
            if run.status == "running" and status == "cancelled":
                return run, False
        else:
            return run, False

        if status == "completed":
            if run.output_message_id is None or not await self._get_matching_output_message(
                run,
                run.output_message_id,
            ):
                raise ValueError("AgentRun 完成前必须绑定同一 Run 的有效 assistant 输出消息")

        run.status = status
        run.error_type = error_type
        run.error_message = error_message
        run.token_usage = token_usage or {}
        run.finished_at = current_time
        run.updated_at = run.finished_at
        run.worker_id = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.runtime_cleanup_pending = True
        await self._project_input_delivery_status(run)
        audit_status = {
            "completed": "abandoned",
            "failed": "failed",
            "cancelled": "interrupted",
            "interrupted": "interrupted",
            "yielded": "interrupted",
        }[status]
        await self._close_running_audits(
            run.id,
            execution_status=audit_status,
            now=current_time,
        )
        await self._finish_open_attempt(
            run.id,
            worker_id=worker_id,
            # 调用点已校验 status 属于终态集合，attempt outcome 与 Run 终态同词表。
            outcome=status,
            error_type=error_type,
            error_message=error_message,
            now=current_time,
        )
        await self.db.flush()
        return run, True

    async def has_thread_cleanup(self, thread_id: str) -> bool:
        """检查该 Thread 尚未解除的 runtime 清理责任。"""
        return (
            await self.db.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.thread_id == thread_id,
                    AgentRun.runtime_cleanup_pending.is_(True),
                )
                .limit(1)
            )
        ) is not None

    async def list_pending_runtime_cleanups(self, *, limit: int = 100) -> list[AgentRun]:
        """列出仍由 PostgreSQL 持久拥有的根 runtime 清理任务。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.runtime_cleanup_pending.is_(True),
            )
            .order_by(AgentRun.finished_at.asc(), AgentRun.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _close_running_audits(
        self,
        run_id: str,
        *,
        execution_status: str,
        now: datetime,
    ) -> None:
        """在 Run owning transaction 内关闭尚无 terminal 事实的 Model/Tool 审计行。"""
        messages = await self.db.scalars(
            select(Message).where(
                Message.run_id == run_id,
                Message.message_type.in_(AUDIT_MESSAGE_TYPES),
                Message.execution_status == "running",
            )
        )
        for message in messages:
            message.execution_status = execution_status
            message.finished_at = message.finished_at or now
            if message.message_type == TOOL_AUDIT_MESSAGE_TYPE:
                metadata = dict(message.extra_metadata or {})
                metadata["error_message"] = metadata.get("error_message") or f"工具执行由 Run 终态收敛为 {execution_status}"
                message.extra_metadata = metadata

    async def _project_input_delivery_status(self, run: AgentRun) -> None:
        """在 owning transaction 内同步输入消息的终态投影。"""
        delivery_status = RUN_STATUS_TO_DELIVERY_STATUS.get(run.status)
        if delivery_status is None:
            return
        await self.db.execute(
            update(Message).where(Message.run_id == run.id, Message.role == "user").values(delivery_status=delivery_status)
        )

    async def record_run_manifest(
        self,
        run_id: str,
        *,
        manifest: dict,
        fingerprint: str,
        worker_id: str,
        now: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        """由当前 lease owner 在首次执行前 write-once 固化运行清单。

        已固化的 manifest 不可改写：重复投递幂等跳过，保证配置后续变化
        不会改写历史 Run 的事实。写入者必须是仍持有有效 lease 的 owner。
        """
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if not fingerprint.strip():
            raise ValueError("fingerprint 不能为空")

        run = await self._lock_run(run_id)
        if not run:
            return None, False
        current_time = now or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="固化运行清单")

        if run.manifest_fingerprint is not None:
            return run, False

        run.manifest = manifest
        run.manifest_fingerprint = fingerprint
        run.manifest_recorded_at = current_time
        run.updated_at = current_time
        await self.db.flush()
        return run, True

    async def record_prepared(
        self,
        run_id: str,
        *,
        worker_id: str,
        observed_at: datetime | None = None,
        checked_at: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        """由当前 lease owner write-once 记录运行准备完成时间。"""
        run = await self._lock_run(run_id)
        if run is None:
            return None, False
        if run.prepared_at is not None:
            return run, False

        lease_check_time = checked_at or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=lease_check_time, action="记录运行准备时间")
        event_time = observed_at or lease_check_time
        if run.started_at is None or event_time < run.started_at:
            raise ValueError("AgentRun 准备时间不能早于开始时间")

        run.prepared_at = event_time
        run.updated_at = lease_check_time
        await self.db.flush()
        return run, True

    async def record_first_model_request(
        self,
        run_id: str,
        *,
        worker_id: str,
        observed_at: datetime | None = None,
        checked_at: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        """由当前 lease owner write-once 记录首次进入模型请求边界的时间。"""
        run = await self._lock_run(run_id)
        if run is None:
            return None, False
        if run.first_model_request_at is not None:
            return run, False

        lease_check_time = checked_at or utc_now()
        # 取消请求不抹去已经发生的调用；仅此观测允许仍持有效 lease 的取消中 Run 补写。
        if (
            run.status not in LEASED_RUN_STATUSES
            or run.worker_id != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= lease_check_time
        ):
            raise ValueError("只有当前有效 AgentRun lease owner 可以记录首次模型请求时间")
        event_time = observed_at or lease_check_time
        if run.created_at is not None and event_time < run.created_at:
            raise ValueError("AgentRun 首次模型请求时间不能早于创建时间")

        run.first_model_request_at = event_time
        run.updated_at = lease_check_time
        await self.db.flush()
        return run, True

    async def record_first_output(
        self,
        run_id: str,
        *,
        worker_id: str,
        observed_at: datetime | None = None,
        checked_at: datetime | None = None,
    ) -> tuple[AgentRun | None, bool]:
        """由当前 lease owner write-once 记录首个模型语义输出时间。"""
        run = await self._lock_run(run_id)
        if run is None:
            return None, False
        if run.first_output_at is not None:
            return run, False

        lease_check_time = checked_at or utc_now()
        self._require_lease_owner(run, worker_id=worker_id, now=lease_check_time, action="记录首次模型输出时间")
        event_time = observed_at or lease_check_time
        if run.prepared_at is None or event_time < run.prepared_at:
            raise ValueError("AgentRun 首次输出时间不能早于准备完成时间")

        run.first_output_at = event_time
        run.updated_at = lease_check_time
        await self.db.flush()
        return run, True

    async def list_run_attempts(self, run_id: str) -> list[AgentRunAttempt]:
        """按执行序号读取一个 Run 的完整 attempt 历史。"""
        result = await self.db.execute(
            select(AgentRunAttempt)
            .where(AgentRunAttempt.run_id == run_id)
            .order_by(AgentRunAttempt.attempt_no.asc(), AgentRunAttempt.id.asc())
        )
        return list(result.scalars().all())

    async def _get_open_attempt(self, run_id: str, *, worker_id: str | None = None) -> AgentRunAttempt | None:
        """读取该 Run 仍开放（未终结）的 attempt；指定 worker 时限定为当前 owner。"""
        conditions = [AgentRunAttempt.run_id == run_id, AgentRunAttempt.finished_at.is_(None)]
        if worker_id is not None:
            conditions.append(AgentRunAttempt.worker_id == worker_id)
        result = await self.db.execute(
            select(AgentRunAttempt).where(and_(*conditions)).order_by(AgentRunAttempt.attempt_no.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _finish_open_attempt(
        self,
        run_id: str,
        *,
        worker_id: str | None,
        outcome: str,
        now: datetime,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """终结当前开放 attempt；已终结的 attempt 事实不会被改写。"""
        attempt = await self._get_open_attempt(run_id, worker_id=worker_id)
        if attempt is None:
            return
        attempt.outcome = outcome
        attempt.error_type = error_type
        attempt.error_message = error_message
        attempt.finished_at = now
        attempt.updated_at = now

    async def _close_open_attempts(
        self,
        run_id: str,
        *,
        outcome: str,
        now: datetime,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """收敛该 Run 全部仍开放的 attempt；用于失联 Run 被接管或收敛时。"""
        result = await self.db.execute(
            select(AgentRunAttempt).where(and_(AgentRunAttempt.run_id == run_id, AgentRunAttempt.finished_at.is_(None)))
        )
        for attempt in result.scalars().all():
            attempt.outcome = outcome
            attempt.error_type = error_type
            attempt.error_message = error_message
            attempt.finished_at = now
            attempt.updated_at = now

    async def _get_matching_output_message(self, run: AgentRun, message_id: int) -> Message | None:
        """读取满足 AgentRun 因果归属后置条件的输出消息。"""

        result = await self.db.execute(
            select(Message).where(
                Message.id == message_id,
                Message.session_record_id == run.session_record_id,
                Message.run_id == run.id,
                Message.turn_id == run.turn_id,
                Message.role == "assistant",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _expired_lease_condition(now: datetime):
        """候选读取与锁内复核共用同一失联判定。"""
        lease_expired = or_(AgentRun.lease_expires_at.is_(None), AgentRun.lease_expires_at <= now)
        return or_(
            and_(AgentRun.status == "running", lease_expired),
            and_(AgentRun.status == "cancel_requested", AgentRun.worker_id.is_not(None), lease_expired),
            and_(
                AgentRun.status == "cancel_requested",
                AgentRun.worker_id.is_(None),
                AgentRun.started_at.is_not(None),
            ),
        )

    @staticmethod
    def _require_lease_owner(run: AgentRun, *, worker_id: str, now: datetime, action: str) -> None:
        if run.status != "running" or run.worker_id != worker_id or run.lease_expires_at is None or run.lease_expires_at <= now:
            raise ValueError(f"只有当前有效 AgentRun lease owner 可以{action}")

    async def _lock_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update(key_share=True).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
