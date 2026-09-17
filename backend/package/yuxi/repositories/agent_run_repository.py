"""Agent run repository."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    AGENT_RUN_TERMINAL_STATUSES,
    AUDIT_MESSAGE_TYPES,
    TOOL_AUDIT_MESSAGE_TYPE,
    AgentRun,
    AgentRunAttempt,
    Message,
    SubagentThread,
    ToolCall,
)
from yuxi.utils.datetime_utils import utc_now_naive

TERMINAL_RUN_STATUSES = set(AGENT_RUN_TERMINAL_STATUSES)
LEASED_RUN_STATUSES = {"running", "cancel_requested"}
RUN_STATUS_TO_DELIVERY_STATUS = {
    "completed": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
}

TOP_LEVEL_RUN_TYPES = ("chat", "resume")


class AgentRunRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one_or_none()

    async def get_run_by_request_id(self, request_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid))))
        return result.scalar_one_or_none()

    async def lock_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        """锁定用户 Run，串行化 execution tree 创建与父 Run 终态提交。"""

        result = await self.db.execute(
            select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid))).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_subagent_run_with_creator(
        self,
        *,
        uid: str,
        created_by_run_id: str,
        run_id: str,
    ) -> tuple[AgentRun, AgentRun] | None:
        """读取父子 Run，并校验当前执行树的线程关系一致性。"""
        creator_run = await self.get_run_for_user(created_by_run_id, uid)
        if not creator_run:
            return None

        run = await self.get_run_for_user(run_id, uid)
        if not run or run.run_type != "subagent":
            return None
        if run.created_by_run_id != creator_run.id:
            return None

        relation_id = run.subagent_thread_relation_id
        if not relation_id:
            return None
        result = await self.db.execute(
            select(SubagentThread).where(
                SubagentThread.id == relation_id,
                SubagentThread.uid == str(uid),
            )
        )
        relation = result.scalar_one_or_none()
        if not relation or relation.parent_conversation_id != creator_run.conversation_id:
            return None
        if (
            relation.child_conversation_id != run.conversation_id
            or relation.child_thread_id != run.conversation_thread_id
        ):
            return None
        return creator_run, run

    async def get_latest_subagent_run_by_thread_for_user(
        self, conversation_thread_id: str, uid: str
    ) -> AgentRun | None:
        """读取某个子线程最近一次子智能体 run，用于状态页和继续线程校验。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type == "subagent",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_run_by_thread_for_user(self, conversation_thread_id: str, uid: str) -> AgentRun | None:
        """读取线程最近一次 run，用于恢复查询 checkpoint 时的运行时模型。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type.in_(["chat", "resume", "subagent"]),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_chat_or_resume_run(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> AgentRun | None:
        """读取队列作用域内最新的顶层 chat/resume run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.uid == str(uid),
                AgentRun.agent_slug == agent_slug,
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.run_type.in_(TOP_LEVEL_RUN_TYPES),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_top_level_runs_for_threads(
        self, uid: str, conversation_thread_ids: list[str]
    ) -> dict[str, tuple[str, str]]:
        """批量读取各线程最新顶层 chat/resume run，返回 thread_id -> (run_id, status)。

        使用窗口函数一次查询完成，避免对每个线程执行 N+1 查询。
        """
        if not conversation_thread_ids:
            return {}

        ranked = (
            select(
                AgentRun.id,
                AgentRun.status,
                AgentRun.conversation_thread_id,
                func.row_number()
                .over(
                    partition_by=AgentRun.conversation_thread_id,
                    order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
                )
                .label("rn"),
            )
            .where(
                AgentRun.uid == str(uid),
                AgentRun.conversation_thread_id.in_(conversation_thread_ids),
                AgentRun.run_type.in_(TOP_LEVEL_RUN_TYPES),
            )
            .subquery()
        )
        result = await self.db.execute(select(ranked).where(ranked.c.rn == 1))
        return {row.conversation_thread_id: (row.id, row.status) for row in result.all()}

    async def list_child_runs_for_user(self, created_by_run_id: str, uid: str) -> list[AgentRun]:
        """列出由指定 run 创建的所有子 run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.created_by_run_id == created_by_run_id,
                AgentRun.uid == str(uid),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def list_subagent_runs_for_conversation(self, conversation_id: int, uid: str) -> list[AgentRun]:
        """按持久父子关系读取子 Run，补齐尚未进入父 checkpoint 的派发记录。"""
        result = await self.db.execute(
            select(AgentRun)
            .join(SubagentThread, AgentRun.subagent_thread_relation_id == SubagentThread.id)
            .where(
                SubagentThread.parent_conversation_id == conversation_id,
                SubagentThread.uid == str(uid),
                AgentRun.uid == str(uid),
                AgentRun.run_type == "subagent",
                AgentRun.conversation_id == SubagentThread.child_conversation_id,
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def get_active_run_by_thread_for_user(
        self,
        *,
        agent_slug: str,
        conversation_thread_id: str,
        uid: str,
    ) -> AgentRun | None:
        """检查同一用户、智能体、线程上是否已有未结束 run，避免并发写同一线程。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.agent_slug == agent_slug,
                AgentRun.uid == str(uid),
                AgentRun.conversation_thread_id == conversation_thread_id,
                or_(
                    AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
                    AgentRun.runtime_cleanup_pending.is_(True),
                ),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_run_by_runtime_scope_for_user(
        self,
        *,
        runtime_scope_id: str,
        uid: str,
    ) -> AgentRun | None:
        """读取共享同一 runtime 的任意未终态 Run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.runtime_scope_id == str(runtime_scope_id),
                AgentRun.uid == str(uid),
                or_(
                    AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
                    AgentRun.runtime_cleanup_pending.is_(True),
                ),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        run_id: str,
        conversation_thread_id: str,
        runtime_scope_id: str | None = None,
        agent_slug: str,
        uid: str,
        request_id: str,
        input_payload: dict,
        source: str = "chat",
        channel: str = "web",
        external_id: str | None = None,
        origin_metadata: dict | None = None,
        conversation_id: int | None = None,
        created_by_run_id: str | None = None,
        subagent_thread_relation_id: int | None = None,
        run_type: str = "chat",
        input_message_id: int | None = None,
    ) -> AgentRun:
        """登记一条 run 记录；输入正文和图片应通过 input_message_id 指向 Message。"""
        runtime_scope = str(conversation_thread_id) if runtime_scope_id is None else str(runtime_scope_id).strip()
        run = AgentRun(
            id=run_id,
            conversation_thread_id=conversation_thread_id,
            runtime_scope_id=runtime_scope,
            agent_slug=agent_slug,
            uid=str(uid),
            request_id=request_id,
            source=source,
            channel=channel,
            external_id=external_id,
            origin_metadata=origin_metadata or {},
            conversation_id=conversation_id,
            created_by_run_id=created_by_run_id,
            subagent_thread_relation_id=subagent_thread_relation_id,
            run_type=run_type,
            input_message_id=input_message_id,
            input_payload=input_payload or {},
            status="pending",
            runtime_cleanup_pending=False,
        )
        self.db.add(run)
        await self.db.flush()
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

        current_time = now or utc_now_naive()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="固化 Langfuse trace")
        if run.langfuse_trace_id and run.langfuse_trace_id != normalized_trace_id:
            raise ValueError("AgentRun 已绑定不同的 Langfuse trace")

        run.langfuse_trace_id = normalized_trace_id
        run.updated_at = current_time
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

        current_time = now or utc_now_naive()
        self._require_lease_owner(run, worker_id=worker_id, now=current_time, action="持久化输出消息")

        message = await self._get_matching_output_message(run, message_id)
        if message is None:
            raise ValueError("输出消息必须属于同一 conversation、Run 和 request，且角色为 assistant")

        run.output_message_id = message_id
        run.updated_at = current_time
        await self.db.flush()
        return run

    async def lock_output_persistence(
        self,
        run_id: str,
        *,
        worker_id: str,
        conversation_thread_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """在任何输出写入前锁定并验证当前 attempt 的完整因果边界。"""

        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        run = await self._lock_run(run_id)
        if run is None:
            return None

        self._require_lease_owner(run, worker_id=worker_id, now=now or utc_now_naive(), action="持久化输出消息")
        if run.conversation_thread_id != conversation_thread_id or run.request_id != request_id:
            raise ValueError("AgentRun 输出必须属于同一 thread 和 request")
        if run.conversation_id is None:
            raise ValueError("AgentRun 输出缺少 conversation 归属")
        return run

    async def lock_memory_write(
        self,
        run_id: str,
        *,
        uid: str,
        worker_id: str,
        conversation_thread_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> AgentRun | None:
        """锁定并验证允许写入用户 Memory 的当前顶层 attempt。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        run = await self._lock_run(run_id)
        if run is None:
            return None

        self._require_lease_owner(run, worker_id=worker_id, now=now or utc_now_naive(), action="写入 Memory")
        if (
            run.uid != str(uid)
            or run.conversation_thread_id != conversation_thread_id
            or run.request_id != request_id
            or run.run_type not in TOP_LEVEL_RUN_TYPES
        ):
            raise ValueError("Memory 写入必须属于当前用户的同一顶层 Run、thread 和 request")
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

        current_time = now or utc_now_naive()
        initial_claim = run.status == "pending" or (run.status == "cancel_requested" and run.worker_id is None)
        same_live_owner = (
            run.status in LEASED_RUN_STATUSES
            and run.worker_id == worker_id
            and run.lease_expires_at is not None
            and run.lease_expires_at > current_time
        )
        if not initial_claim and not same_live_owner:
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
        current_time = now or utc_now_naive()
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
        current_time = now or utc_now_naive()
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
        run.runtime_cleanup_pending = run.run_type != "subagent"
        run.updated_at = current_time
        await self._finish_open_attempt(
            run_id,
            worker_id=worker_id,
            outcome="retry_released",
            now=current_time,
        )
        await self.db.flush()
        return True

    async def reconcile_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[list[AgentRun], list[tuple[str, str]]]:
        """把失去 owner 的活跃 Run 原子收敛为失败事实。"""
        current_time = now or utc_now_naive()
        lease_missing_or_expired = or_(
            AgentRun.lease_expires_at.is_(None),
            AgentRun.lease_expires_at <= current_time,
        )
        result = await self.db.execute(
            select(AgentRun)
            .where(
                or_(
                    and_(AgentRun.status == "running", lease_missing_or_expired),
                    and_(
                        AgentRun.status == "cancel_requested",
                        AgentRun.worker_id.is_not(None),
                        lease_missing_or_expired,
                    ),
                    and_(
                        AgentRun.status == "cancel_requested",
                        AgentRun.worker_id.is_(None),
                        AgentRun.started_at.is_not(None),
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        )
        runs = list(result.scalars().all())
        runs.sort(key=lambda run: run.created_by_run_id is not None)
        reconciled_runs: list[AgentRun] = []
        cancelled_descendants: list[tuple[str, str]] = []
        for run in runs:
            if run.status in TERMINAL_RUN_STATUSES:
                continue
            run.status = "failed"
            run.error_type = "worker_lease_expired"
            run.error_message = "执行 worker 的 lease 已过期；本次运行结果未知，需按 at-least-once 语义检查副作用。"
            run.finished_at = current_time
            run.updated_at = current_time
            run.worker_id = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            run.runtime_cleanup_pending = run.run_type != "subagent"
            await self._project_input_delivery_status(run)
            await self._close_running_audits(run.id, execution_status="abandoned", now=current_time)
            await self._close_open_attempts(
                run.id,
                outcome="lease_expired",
                error_type="worker_lease_expired",
                error_message="执行 worker 的 lease 已过期；本次运行结果未知。",
                now=current_time,
            )
            reconciled_runs.append(run)
            cancelled_descendants.extend(await self.cancel_active_execution_tree_descendants(run))
        if reconciled_runs:
            await self.db.flush()
        return reconciled_runs, cancelled_descendants

    async def fail_nonterminal_for_storage_migration(self) -> list[str]:
        """停机迁移时把已失去运行环境的 Run 收敛为可观察失败事实。"""
        current_time = utc_now_naive()
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.status.notin_(TERMINAL_RUN_STATUSES))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            .with_for_update()
        )
        run_ids: list[str] = []
        for run in result.scalars().all():
            run.status = "failed"
            run.error_type = "storage_migration"
            run.error_message = "存储升级已停止旧运行环境；本次运行未完成"
            run.finished_at = current_time
            run.updated_at = current_time
            run.worker_id = None
            run.heartbeat_at = None
            run.lease_expires_at = None
            # quiescence proof 已证明旧 runtime 不存在，无需再创建异步清理任务。
            run.runtime_cleanup_pending = False
            await self._project_input_delivery_status(run)
            await self._close_running_audits(run.id, execution_status="abandoned", now=current_time)
            await self._close_open_attempts(
                run.id,
                outcome="failed",
                error_type=run.error_type,
                error_message=run.error_message,
                now=current_time,
            )
            run_ids.append(run.id)
        if run_ids:
            await self.db.flush()
        return run_ids

    async def request_cancel_execution_tree(
        self,
        *,
        run_id: str,
        uid: str,
        cascade_descendants: bool,
    ) -> tuple[AgentRun | None, list[str]]:
        """按 root 到 descendants 的固定锁顺序取消一棵执行树。"""
        run = await self.lock_run_for_user(run_id, str(uid))
        if run is None:
            return None, []
        await self._request_cancel_locked(run)
        cancelled_ids = [run.id]
        if cascade_descendants:
            cancelled_ids.extend(
                child_id for child_id, _thread_id in await self.cancel_active_execution_tree_descendants(run)
            )
        return run, cancelled_ids

    async def _request_cancel_locked(self, run: AgentRun) -> None:
        """转换一条已由当前事务锁定的 Run。"""
        if run.status in TERMINAL_RUN_STATUSES:
            return
        current_time = utc_now_naive()
        if run.status == "pending" and run.worker_id is None and run.started_at is None:
            run.status = "cancelled"
            run.error_type = "cancelled"
            run.error_message = "对话已在执行前取消"
            run.finished_at = current_time
            run.updated_at = current_time
            run.runtime_cleanup_pending = False
            await self._project_input_delivery_status(run)
            await self.db.flush()
            return
        run.status = "cancel_requested"
        run.updated_at = current_time
        await self.db.flush()

    async def cancel_active_execution_tree_descendants(self, root_run: AgentRun) -> list[tuple[str, str]]:
        """在父 Run 状态事务内请求仍活跃的 execution tree 后代停止。"""

        if root_run.run_type == "subagent":
            return []

        current_time = utc_now_naive()
        cancelled: list[tuple[str, str]] = []
        pending_parent_ids = [root_run.id]
        seen_ids: set[str] = set()
        while pending_parent_ids:
            parent_ids = pending_parent_ids
            pending_parent_ids = []
            result = await self.db.execute(
                select(AgentRun)
                .where(
                    AgentRun.created_by_run_id.in_(parent_ids),
                    AgentRun.uid == str(root_run.uid),
                    AgentRun.runtime_scope_id == str(root_run.runtime_scope_id),
                    AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
                )
                .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
                .with_for_update()
            )
            for child in result.scalars().all():
                if child.id in seen_ids:
                    continue
                seen_ids.add(child.id)
                pending_parent_ids.append(child.id)
                child.error_type = "execution_tree_closed"
                child.error_message = "父运行已结束，请停止共享执行树"
                if child.status == "pending" and child.worker_id is None and child.started_at is None:
                    child.status = "cancelled"
                    child.finished_at = current_time
                    child.worker_id = None
                    child.heartbeat_at = None
                    child.lease_expires_at = None
                    await self._project_input_delivery_status(child)
                    await self._close_open_attempts(
                        child.id,
                        outcome="cancelled",
                        error_type=child.error_type,
                        error_message=child.error_message,
                        now=current_time,
                    )
                else:
                    child.status = "cancel_requested"
                child.updated_at = current_time
                cancelled.append((child.id, child.conversation_thread_id))

        if cancelled:
            await self.db.flush()
        return cancelled

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

        current_time = now or utc_now_naive()
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
        run.runtime_cleanup_pending = run.run_type != "subagent"
        await self._project_input_delivery_status(run)
        audit_status = {
            "completed": "abandoned",
            "failed": "failed",
            "cancelled": "interrupted",
            "interrupted": "interrupted",
        }[status]
        await self._close_running_audits(
            run.id,
            execution_status=audit_status,
            now=current_time,
            preserve_pending_tool_calls=status == "interrupted",
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

    async def list_pending_runtime_cleanups(self, *, limit: int = 100) -> list[AgentRun]:
        """列出仍由 PostgreSQL 持久拥有的根 runtime 清理任务。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.runtime_cleanup_pending.is_(True),
                AgentRun.run_type != "subagent",
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
        preserve_pending_tool_calls: bool = False,
    ) -> None:
        """在 Run owning transaction 内关闭尚无 terminal 事实的 Model/Tool 审计行。"""
        running_tools = await self.db.execute(
            select(Message.extra_metadata).where(
                Message.run_id == run_id,
                Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                Message.execution_status == "running",
            )
        )
        tool_metadata = {
            metadata["compatibility_tool_call_id"]: metadata
            for metadata in running_tools.scalars().all()
            if isinstance(metadata, dict) and isinstance(metadata.get("compatibility_tool_call_id"), int)
        }
        if tool_metadata and not preserve_pending_tool_calls:
            tool_calls = await self.db.scalars(select(ToolCall).where(ToolCall.id.in_(tool_metadata)))
            for tool_call in tool_calls:
                metadata = tool_metadata[tool_call.id]
                tool_call.status = "error"
                tool_call.error_message = metadata.get("error_message") or (
                    f"Tool 审计由 Run 终态收敛为 {execution_status}"
                )
        await self.db.execute(
            update(Message)
            .where(
                Message.run_id == run_id,
                Message.message_type.in_(AUDIT_MESSAGE_TYPES),
                Message.execution_status == "running",
            )
            .values(
                execution_status=execution_status,
                finished_at=func.coalesce(Message.finished_at, now),
            )
        )

    async def _project_input_delivery_status(self, run: AgentRun) -> None:
        """在 owning transaction 内同步输入消息的终态投影。"""
        delivery_status = RUN_STATUS_TO_DELIVERY_STATUS.get(run.status)
        if run.input_message_id is None or delivery_status is None:
            return
        await self.db.execute(
            update(Message).where(Message.id == run.input_message_id).values(delivery_status=delivery_status)
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
        current_time = now or utc_now_naive()
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

        lease_check_time = checked_at or utc_now_naive()
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

        lease_check_time = checked_at or utc_now_naive()
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

        lease_check_time = checked_at or utc_now_naive()
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
                Message.conversation_id == run.conversation_id,
                Message.run_id == run.id,
                Message.request_id == run.request_id,
                Message.role == "assistant",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _require_lease_owner(run: AgentRun, *, worker_id: str, now: datetime, action: str) -> None:
        if (
            run.status != "running"
            or run.worker_id != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise ValueError(f"只有当前有效 AgentRun lease owner 可以{action}")

    async def _lock_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        return result.scalar_one_or_none()
