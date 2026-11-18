from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.models import BackgroundJobRecord

TERMINAL_JOB_STATUSES = {"success", "failed", "cancelled"}
_UNSET = object()


class BackgroundJobRepository:
    async def get_by_id(self, job_id: str) -> BackgroundJobRecord | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.get(BackgroundJobRecord, job_id)

    async def list(self, status: str | None = None, limit: int = 100) -> list[BackgroundJobRecord]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(BackgroundJobRecord)
            if status:
                stmt = stmt.where(BackgroundJobRecord.status == status)
            active_first = case((BackgroundJobRecord.status.in_({"pending", "running"}), 0), else_=1)
            stmt = stmt.order_by(active_first, BackgroundJobRecord.created_at.desc()).limit(max(limit, 0))
            return list((await session.execute(stmt)).scalars().all())

    async def summarize(self, *, status: str | None = None) -> dict[str, Any]:
        """从完整 Job 表计算列表摘要，不受返回 limit 影响。"""
        async with pg_manager.get_async_session_context() as session:
            status_rows = (
                await session.execute(select(BackgroundJobRecord.status, func.count()).group_by(BackgroundJobRecord.status))
            ).all()
            type_rows = (await session.execute(select(BackgroundJobRecord.type, func.count()).group_by(BackgroundJobRecord.type))).all()
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

    async def find_latest_by_payload(
        self,
        *,
        job_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> BackgroundJobRecord | None:
        filters = [BackgroundJobRecord.type == job_type]
        if statuses is not None:
            filters.append(BackgroundJobRecord.status.in_(statuses))
        filters.extend(BackgroundJobRecord.payload[key].as_string() == str(value) for key, value in payload_match.items())
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(BackgroundJobRecord)
                .where(*filters)
                .order_by(BackgroundJobRecord.created_at.desc(), BackgroundJobRecord.id.desc())
                .limit(1)
            )

    async def list_by_payload_values(
        self,
        *,
        job_type: str,
        payload_key: str,
        payload_values: set[str],
    ) -> list[BackgroundJobRecord]:
        if not payload_values:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(BackgroundJobRecord)
                .where(
                    BackgroundJobRecord.type == job_type,
                    BackgroundJobRecord.payload[payload_key].as_string().in_(payload_values),
                )
                .order_by(BackgroundJobRecord.created_at.desc(), BackgroundJobRecord.id.desc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def create_or_get_in_session(session, job_id: str, data: dict[str, Any]) -> tuple[BackgroundJobRecord, bool]:
        """在调用方事务中按 active dedupe 原子创建或返回现有 Job。"""
        try:
            async with session.begin_nested():
                record = BackgroundJobRecord(id=job_id, **data)
                session.add(record)
                await session.flush()
            return record, True
        except IntegrityError:
            dedupe_key = data.get("dedupe_key")
            if not dedupe_key:
                raise
            existing = await session.scalar(
                select(BackgroundJobRecord).where(
                    BackgroundJobRecord.type == data["type"],
                    BackgroundJobRecord.dedupe_key == dedupe_key,
                    BackgroundJobRecord.status.notin_(TERMINAL_JOB_STATUSES),
                )
            )
            if existing is None:
                raise
            return existing, False

    async def create(self, job_id: str, data: dict[str, Any]) -> tuple[BackgroundJobRecord, bool]:
        """创建持久任务；活跃 dedupe 冲突时返回现有任务。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.create_or_get_in_session(session, job_id, data)

    async def fail_pending(
        self,
        job_id: str,
        *,
        error: str,
        before_fail: Callable[[Any, BackgroundJobRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """把无法重建的 pending Job 明确收敛为失败。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
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
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> BackgroundJobRecord | None:
        """只记录取消请求，由执行方确认最终状态。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.request_cancel_in_session(session, job_id, now=now)

    async def request_cancel_in_session(
        self,
        session,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> BackgroundJobRecord | None:
        """锁定活跃作业，在调用方事务中登记取消意图。"""
        record = await self._lock_job(session, job_id)
        if record is None or record.status in TERMINAL_JOB_STATUSES:
            return None
        record.cancel_requested = 1
        record.updated_at = await self._current_time(session, now)
        await session.flush()
        return record

    async def confirm_pending_cancel(
        self,
        job_id: str,
        *,
        before_cancel: Callable[[Any, BackgroundJobRecord], Awaitable[None]] | None = None,
    ) -> bool:
        """执行方确认尚未开始的取消，并原子提交领域结果。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
            if record is None or record.status != "pending" or not record.cancel_requested:
                return False
            if before_cancel is not None:
                await before_cancel(session, record)
            current_time = await self._current_time(session, None)
            record.status = "cancelled"
            record.message = "任务已取消"
            record.completed_at = current_time
            record.updated_at = current_time
            record.dedupe_key = None
            await session.flush()
            return True

    async def claim(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
        max_running: int | None = None,
    ) -> tuple[BackgroundJobRecord | None, bool]:
        """由一个 attempt 原子取得 pending Job 的执行权。"""
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        async with pg_manager.get_async_session_context() as session:
            if max_running is not None:
                if max_running <= 0:
                    raise ValueError("max_running 必须大于 0")
                await session.execute(select(func.pg_advisory_xact_lock(func.hashtext("durable-job-capacity"))))
                running_count = int(
                    await session.scalar(select(func.count(BackgroundJobRecord.id)).where(BackgroundJobRecord.status == "running")) or 0
                )
                if running_count >= max_running:
                    return await session.get(BackgroundJobRecord, job_id), False
            record = await self._lock_job(session, job_id)
            current_time = await self._current_time(session, now)
            if record is None or record.status != "pending":
                return record, False
            if record.cancel_requested:
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

    async def check_control(self, job_id: str, *, worker_id: str) -> tuple[bool, bool]:
        """使用 PostgreSQL 时钟检查当前 owner 与取消意图，不延长 lease。"""
        async with pg_manager.get_async_session_context() as session:
            row = (
                await session.execute(
                    select(BackgroundJobRecord.cancel_requested).where(
                        BackgroundJobRecord.id == job_id,
                        BackgroundJobRecord.status == "running",
                        BackgroundJobRecord.worker_id == worker_id,
                        BackgroundJobRecord.lease_expires_at > func.clock_timestamp(),
                    )
                )
            ).one_or_none()
            if row is None:
                return False, False
            return True, bool(row.cancel_requested)

    async def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> tuple[bool, bool]:
        """仅允许当前且未过期的 owner 续租，并返回取消意图。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
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
        job_id: str,
        *,
        worker_id: str,
        operation: Callable[[Any, BackgroundJobRecord], Awaitable[None]],
    ) -> bool:
        """在当前 owner 的行锁事务中执行领域 checkpoint 写。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
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
        job_id: str,
        *,
        worker_id: str,
        data: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """只有持有有效 lease 的 owner 可以更新进度、结果和消息。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
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
        job_id: str,
        *,
        worker_id: str,
        status: str,
        message: str,
        result: Any = _UNSET,
        error: str | None = None,
        before_finish: Callable[[Any, BackgroundJobRecord], Awaitable[None]] | None = None,
        before_cancel: Callable[[Any, BackgroundJobRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """由当前 owner 提交终态并释放执行权。"""
        if status not in TERMINAL_JOB_STATUSES:
            raise ValueError(f"非法 Job 终态: {status}")
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
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
        job_id: str,
        *,
        worker_id: str,
        error: str,
        before_fail: Callable[[Any, BackgroundJobRecord], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """优雅中断时明确失败并释放执行权。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_job(session, job_id)
            current_time = await self._current_time(session, now)
            if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                return None
            if before_fail is not None:
                await before_fail(session, record)
                current_time = await self._current_time(session, now)
                if not self._is_live_owner(record, worker_id=worker_id, now=current_time):
                    await session.rollback()
                    return None
            next_status = self._fail_interrupted_job(record, error=error, now=current_time)
            await session.flush()
            return next_status

    async def reconcile_expired_leases(
        self,
        *,
        before_fail: Callable[[Any, BackgroundJobRecord, str], Awaitable[None]] | None = None,
        now: datetime | None = None,
    ) -> list[tuple[str, str, int]]:
        """收敛失联 Job；返回 job_id、目标状态和 attempt。"""
        async with pg_manager.get_async_session_context() as session:
            current_time = await self._current_time(session, now)
            filters = [
                BackgroundJobRecord.status == "running",
                or_(BackgroundJobRecord.lease_expires_at.is_(None), BackgroundJobRecord.lease_expires_at <= current_time),
            ]
            result = await session.execute(select(BackgroundJobRecord).where(*filters).with_for_update(skip_locked=True))
            reconciled: list[tuple[str, str, int]] = []
            for record in result.scalars().all():
                error = "worker_lease_expired: 执行 worker 的 lease 已过期，任务副作用结果未知"
                if before_fail is not None:
                    await before_fail(session, record, error)
                next_status = self._fail_interrupted_job(
                    record,
                    error=error,
                    now=current_time,
                )
                reconciled.append((record.id, next_status, int(record.attempt_count or 0)))
            if reconciled:
                await session.flush()
            return reconciled

    async def list_pending(self, *, limit: int = 200) -> list[BackgroundJobRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(BackgroundJobRecord)
                .where(BackgroundJobRecord.status == "pending")
                .order_by(BackgroundJobRecord.created_at.asc())
                .limit(max(limit, 1))
            )
            return list(result.scalars().all())

    async def prune_terminal(self, *, keep: int = 200) -> list[str]:
        """保留最近终态任务，并删除更旧摘要。"""
        async with pg_manager.get_async_session_context() as session:
            stale_ids = list(
                (
                    await session.execute(
                        select(BackgroundJobRecord.id)
                        .where(BackgroundJobRecord.status.in_(TERMINAL_JOB_STATUSES))
                        .order_by(BackgroundJobRecord.created_at.desc(), BackgroundJobRecord.id.desc())
                        .offset(max(keep, 0))
                    )
                ).scalars()
            )
            if stale_ids:
                await session.execute(delete(BackgroundJobRecord).where(BackgroundJobRecord.id.in_(stale_ids)))
            return stale_ids

    async def delete_terminal(self, job_id: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(BackgroundJobRecord).where(
                    BackgroundJobRecord.id == job_id,
                    BackgroundJobRecord.status.in_(TERMINAL_JOB_STATUSES),
                )
            )
            return bool(result.rowcount)

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(BackgroundJobRecord))

    @staticmethod
    async def _current_time(session, explicit: datetime | None) -> datetime:
        if explicit is not None:
            return explicit
        return await session.scalar(select(func.clock_timestamp()))

    @staticmethod
    async def _lock_job(session, job_id: str) -> BackgroundJobRecord | None:
        return await session.scalar(select(BackgroundJobRecord).where(BackgroundJobRecord.id == job_id).with_for_update())

    @staticmethod
    def _is_live_owner(record: BackgroundJobRecord | None, *, worker_id: str, now: datetime) -> bool:
        return bool(
            record
            and record.status == "running"
            and record.worker_id == worker_id
            and record.lease_expires_at is not None
            and record.lease_expires_at > now
        )

    @staticmethod
    def _clear_execution(record: BackgroundJobRecord, *, clear_dedupe: bool) -> None:
        record.worker_id = None
        record.heartbeat_at = None
        record.lease_expires_at = None
        if clear_dedupe:
            record.dedupe_key = None

    def _fail_interrupted_job(self, record: BackgroundJobRecord, *, error: str, now: datetime) -> str:
        record.status = "failed"
        record.progress = 100.0
        record.message = "执行中断，无法安全自动恢复"
        record.error = error
        record.completed_at = now
        self._clear_execution(record, clear_dedupe=True)
        record.updated_at = now
        return record.status
