from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord

TERMINAL_TASK_STATUSES = {"success", "failed", "cancelled"}
_UNSET = object()


class TaskRepository:
    async def get_by_id(self, task_id: str) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.get(TaskRecord, task_id)

    async def list(self, status: str | None = None, limit: int = 100) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(TaskRecord)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            active_first = case((TaskRecord.status.in_({"pending", "running"}), 0), else_=1)
            stmt = stmt.order_by(active_first, TaskRecord.created_at.desc()).limit(max(limit, 0))
            return list((await session.execute(stmt)).scalars().all())

    async def summarize(self, *, status: str | None = None) -> dict[str, Any]:
        """从完整 Task 表计算列表摘要，不受返回 limit 影响。"""
        async with pg_manager.get_async_session_context() as session:
            status_rows = (
                await session.execute(select(TaskRecord.status, func.count()).group_by(TaskRecord.status))
            ).all()
            type_rows = (await session.execute(select(TaskRecord.type, func.count()).group_by(TaskRecord.type))).all()
            total = sum(int(count) for _value, count in status_rows)
            filtered_total = total
            if status is not None:
                filtered_total = next(
                    (int(count) for value, count in status_rows if value == status),
                    0,
                )
            return {
                "total": total,
                "filtered_total": filtered_total,
                "status_counts": {str(value): int(count) for value, count in status_rows},
                "type_counts": {str(value): int(count) for value, count in type_rows},
            }

    async def list_all(self) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).order_by(TaskRecord.created_at.desc()))
            return list(result.scalars().all())

    async def find_latest_by_payload(
        self,
        *,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> TaskRecord | None:
        filters = [TaskRecord.type == task_type]
        if statuses is not None:
            filters.append(TaskRecord.status.in_(statuses))
        filters.extend(TaskRecord.payload[key].as_string() == str(value) for key, value in payload_match.items())
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(TaskRecord).where(*filters).order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()).limit(1)
            )

    async def list_by_payload_values(
        self,
        *,
        task_type: str,
        payload_key: str,
        payload_values: set[str],
    ) -> list[TaskRecord]:
        if not payload_values:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(TaskRecord)
                .where(
                    TaskRecord.type == task_type,
                    TaskRecord.payload[payload_key].as_string().in_(payload_values),
                )
                .order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def create_in_session(session, task_id: str, data: dict[str, Any]) -> TaskRecord:
        """在调用方拥有的事务中创建尚未发布的 Task intent。"""
        record = TaskRecord(id=task_id, **data)
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def create_or_get_in_session(session, task_id: str, data: dict[str, Any]) -> tuple[TaskRecord, bool]:
        """在调用方事务中按 active dedupe 原子创建或返回现有 Task。"""
        try:
            async with session.begin_nested():
                record = TaskRecord(id=task_id, **data)
                session.add(record)
                await session.flush()
            return record, True
        except IntegrityError:
            dedupe_key = data.get("dedupe_key")
            if not dedupe_key:
                raise
            existing = await session.scalar(
                select(TaskRecord).where(
                    TaskRecord.type == data["type"],
                    TaskRecord.dedupe_key == dedupe_key,
                    TaskRecord.status.notin_(TERMINAL_TASK_STATUSES),
                )
            )
            if existing is None:
                raise
            return existing, False

    async def create(self, task_id: str, data: dict[str, Any]) -> tuple[TaskRecord, bool]:
        """创建持久任务；活跃 dedupe 冲突时返回现有任务。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.create_or_get_in_session(session, task_id, data)

    async def fail_pending(
        self,
        task_id: str,
        *,
        error: str,
        before_fail: Callable[[Any, TaskRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """把无法重建的 pending Task 明确收敛为失败。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if record is None or record.status != "pending":
                return False
            if before_fail is not None:
                await before_fail(session, record)
            record.status = "failed"
            record.progress = 100.0
            record.message = "任务 Handler 无法重建"
            record.error = error
            record.completed_at = current_time
            record.updated_at = current_time
            record.dedupe_key = None
            await session.flush()
            return True

    async def request_cancel(
        self,
        task_id: str,
        *,
        before_cancel: Callable[[Any, TaskRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> TaskRecord | None:
        """持久化取消意图；未执行任务直接收敛为 cancelled。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if record is None or record.status in TERMINAL_TASK_STATUSES:
                return None
            record.cancel_requested = 1
            record.updated_at = current_time
            if record.status == "pending":
                if before_cancel is not None:
                    await before_cancel(session, record)
                record.status = "cancelled"
                record.message = "任务已取消"
                record.completed_at = current_time
                record.dedupe_key = None
            await session.flush()
            return record

    async def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
        max_running: int | None = None,
    ) -> tuple[TaskRecord | None, bool]:
        """由一个 attempt 原子取得 pending Task 的执行权。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        async with pg_manager.get_async_session_context() as session:
            if max_running is not None:
                if max_running <= 0:
                    raise ValueError("max_running 必须大于 0")
                await session.execute(select(func.pg_advisory_xact_lock(func.hashtext("durable-task-capacity"))))
                running_count = int(
                    await session.scalar(select(func.count(TaskRecord.id)).where(TaskRecord.status == "running")) or 0
                )
                if running_count >= max_running:
                    return await session.get(TaskRecord, task_id), False
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if record is None or record.status != "pending":
                return record, False
            if record.cancel_requested:
                record.status = "cancelled"
                record.message = "任务在执行前已取消"
                record.completed_at = current_time
                record.updated_at = current_time
                record.dedupe_key = None
                await session.flush()
                return record, False

            record.status = "running"
            record.worker_id = worker_id
            record.heartbeat_at = current_time
            record.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.started_at = record.started_at or current_time
            record.updated_at = current_time
            record.message = "任务开始执行"
            await session.flush()
            return record, True

    async def check_control(self, task_id: str, *, worker_id: str) -> tuple[bool, bool]:
        """使用 PostgreSQL 时钟检查当前 owner 与取消意图，不延长 lease。"""
        async with pg_manager.get_async_session_context() as session:
            row = (
                await session.execute(
                    select(TaskRecord.cancel_requested).where(
                        TaskRecord.id == task_id,
                        TaskRecord.status == "running",
                        TaskRecord.worker_id == worker_id,
                        TaskRecord.lease_expires_at > func.timezone("UTC", func.clock_timestamp()),
                    )
                )
            ).one_or_none()
            if row is None:
                return False, False
            return True, bool(row.cancel_requested)

    async def renew_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> tuple[bool, bool]:
        """仅允许当前且未过期的 owner 续租，并返回取消意图。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return False, False
            record.heartbeat_at = current_time
            record.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
            record.updated_at = current_time
            await session.flush()
            return True, bool(record.cancel_requested)

    async def run_owned_transaction(
        self,
        task_id: str,
        *,
        worker_id: str,
        operation: Callable[[Any, TaskRecord], Awaitable[None]],
    ) -> bool:
        """在当前 owner 的行锁事务中执行领域 checkpoint 写。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, None)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return False
            await operation(session, record)
            current_time = await self._current_time(session, None)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                await session.rollback()
                return False
            return True

    async def update_owned(
        self,
        task_id: str,
        *,
        worker_id: str,
        data: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """只有持有有效 lease 的 owner 可以更新进度、结果和消息。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return False
            for key, value in data.items():
                setattr(record, key, value)
            record.updated_at = current_time
            await session.flush()
            return True

    async def finish_owned(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: str,
        message: str,
        result: Any = _UNSET,
        error: str | None = None,
        before_finish: Callable[[Any, TaskRecord], Awaitable[None]] | None = None,
        before_cancel: Callable[[Any, TaskRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """由当前 owner 提交终态并释放执行权。"""
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError(f"非法 Task 终态: {status}")
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return False
            if record.cancel_requested and status != "cancelled":
                status = "cancelled"
                message = "任务已取消"
                result = _UNSET
                cancel_hook = before_cancel or before_finish
                if cancel_hook is not None:
                    await cancel_hook(session, record)
            elif before_finish is not None:
                await before_finish(session, record)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                await session.rollback()
                return False
            record.status = status
            record.progress = 100.0
            record.message = message
            if result is not _UNSET:
                record.result = result
            record.error = error
            record.completed_at = current_time
            record.updated_at = current_time
            self._clear_execution(record, clear_dedupe=True)
            await session.flush()
            return True

    async def release_interrupted_owner(
        self,
        task_id: str,
        *,
        worker_id: str,
        error: str,
        before_fail: Callable[[Any, TaskRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """优雅中断时按恢复策略重新排队或明确失败。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_task(session, task_id)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return None
            if record.recovery_strategy != "restart" and before_fail is not None:
                await before_fail(session, record)
                current_time = await self._current_time(session, now)
                if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                    await session.rollback()
                    return None
            next_status = self._apply_interrupted_transition(record, error=error, now=current_time)
            await session.flush()
            return next_status

    async def reconcile_expired_leases(
        self,
        *,
        before_fail: Callable[[Any, TaskRecord, str], Awaitable[None]] | None = None,
        task_types: set[str] | None = None,
        now: datetime | None = None,
    ) -> list[tuple[str, str, int]]:
        """收敛当前能力允许的失联 Task；返回 task_id、目标状态和 attempt。"""
        if task_types is not None and not task_types:
            return []
        async with pg_manager.get_async_session_context() as session:
            current_time = await self._current_time(session, now)
            filters = [
                TaskRecord.status == "running",
                or_(TaskRecord.lease_expires_at.is_(None), TaskRecord.lease_expires_at <= current_time),
            ]
            if task_types is not None:
                filters.append(TaskRecord.type.in_(task_types))
            result = await session.execute(select(TaskRecord).where(*filters).with_for_update(skip_locked=True))
            reconciled: list[tuple[str, str, int]] = []
            for record in result.scalars().all():
                error = "worker_lease_expired: 执行 worker 的 lease 已过期，任务副作用结果未知"
                if record.recovery_strategy != "restart" and before_fail is not None:
                    await before_fail(session, record, error)
                next_status = self._apply_interrupted_transition(
                    record,
                    error=error,
                    now=current_time,
                )
                reconciled.append((record.id, next_status, int(record.attempt_count or 0)))
            if reconciled:
                await session.flush()
            return reconciled

    async def list_pending(self, *, limit: int = 200) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.status == "pending")
                .order_by(TaskRecord.created_at.asc())
                .limit(max(limit, 1))
            )
            return list(result.scalars().all())

    async def prune_terminal(self, *, keep: int = 200, task_types: set[str] | None = None) -> list[str]:
        """保留当前能力允许的最近终态任务，并删除更旧摘要。"""
        if task_types is not None and not task_types:
            return []
        filters = [TaskRecord.status.in_(TERMINAL_TASK_STATUSES)]
        if task_types is not None:
            filters.append(TaskRecord.type.in_(task_types))
        async with pg_manager.get_async_session_context() as session:
            stale_ids = list(
                (
                    await session.execute(
                        select(TaskRecord.id)
                        .where(*filters)
                        .order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())
                        .offset(max(keep, 0))
                    )
                ).scalars()
            )
            if stale_ids:
                await session.execute(delete(TaskRecord).where(TaskRecord.id.in_(stale_ids)))
            return stale_ids

    async def delete_terminal(self, task_id: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(TaskRecord).where(
                    TaskRecord.id == task_id,
                    TaskRecord.status.in_(TERMINAL_TASK_STATUSES),
                )
            )
            return bool(result.rowcount)

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord))

    @staticmethod
    async def _current_time(session, explicit: datetime | None) -> datetime:
        if explicit is not None:
            return explicit
        return await session.scalar(select(func.timezone("UTC", func.clock_timestamp())))

    @staticmethod
    async def _lock_task(session, task_id: str) -> TaskRecord | None:
        return await session.scalar(select(TaskRecord).where(TaskRecord.id == task_id).with_for_update())

    @staticmethod
    def _is_live_owner(record: TaskRecord | None, *, worker_id: str, now: datetime) -> bool:
        return bool(
            record
            and record.status == "running"
            and record.worker_id == worker_id
            and record.lease_expires_at is not None
            and record.lease_expires_at > now
        )

    @staticmethod
    def _clear_execution(record: TaskRecord, *, clear_dedupe: bool) -> None:
        record.worker_id = None
        record.heartbeat_at = None
        record.lease_expires_at = None
        if clear_dedupe:
            record.dedupe_key = None

    def _apply_interrupted_transition(self, record: TaskRecord, *, error: str, now: datetime) -> str:
        if record.recovery_strategy == "restart" and not record.cancel_requested:
            record.status = "pending"
            record.message = "执行中断，等待重新投递"
            record.error = error
            self._clear_execution(record, clear_dedupe=False)
        else:
            record.status = "failed"
            record.progress = 100.0
            record.message = "执行中断，无法安全自动恢复"
            record.error = error
            record.completed_at = now
            self._clear_execution(record, clear_dedupe=True)
        record.updated_at = now
        return record.status
