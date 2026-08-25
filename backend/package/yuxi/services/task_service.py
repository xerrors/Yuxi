from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from yuxi.config.runtime import lite_mode_enabled
from yuxi.repositories.task_repository import TERMINAL_TASK_STATUSES, TaskRepository
from yuxi.services.task_queue_service import TASK_HEARTBEAT_SECONDS, TASK_LEASE_SECONDS, publish_task
from yuxi.services.task_registry import get_task_definition
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive
from yuxi.utils.logging_config import logger

TERMINAL_STATUSES = TERMINAL_TASK_STATUSES
PROGRESS_PERSIST_DELTA = 2.0
TASKER_DEFAULT_TIMEOUT_SECONDS = float(os.getenv("TASKER_DEFAULT_TIMEOUT_SECONDS", 6 * 60 * 60))
TASKER_MAX_TIMEOUT_SECONDS = 24 * 60 * 60
DURABLE_TASK_MAX_RUNNING = 4


@dataclass
class Task:
    id: str
    name: str
    type: str
    status: str = "pending"
    progress: float = 0.0
    message: str = ""
    created_at: str = field(default_factory=utc_isoformat)
    updated_at: str = field(default_factory=utc_isoformat)
    started_at: str | None = None
    completed_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    cancel_requested: bool = False
    recovery_strategy: str = "fail"
    handler_version: int = 1
    dedupe_key: str | None = None
    attempt_count: int = 0
    worker_id: str | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    timeout_seconds: float = TASKER_DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in data.items() if key in fields}
        values["cancel_requested"] = bool(values.get("cancel_requested", False))
        return cls(**values)


class TaskContext:
    """向领域 Handler 提供受当前 attempt lease 保护的进度与取消边界。"""

    def __init__(self, task_id: str, worker_id: str, payload: dict[str, Any] | None = None):
        self.task_id = task_id
        self.worker_id = worker_id
        self.payload = payload or {}
        self.cancellation_reason: str | None = None
        self._cancel_requested = False
        self._last_persisted_progress: float | None = None
        self._repo = TaskRepository()

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        normalized = max(0.0, min(float(progress), 100.0))
        if (
            self._last_persisted_progress is not None
            and abs(normalized - self._last_persisted_progress) < PROGRESS_PERSIST_DELTA
        ):
            return
        data: dict[str, Any] = {"progress": normalized}
        if message is not None:
            data["message"] = message
        await self._update(data)
        self._last_persisted_progress = normalized

    async def set_message(self, message: str) -> None:
        await self._update({"message": message})

    async def set_result(self, result: Any) -> None:
        await self._update({"result": result})

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    async def run_owned_transaction(self, operation) -> None:
        """在 attempt lease 行锁内提交领域 checkpoint。"""
        if not await self._repo.run_owned_transaction(
            self.task_id,
            worker_id=self.worker_id,
            operation=operation,
        ):
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("Task lease was lost")

    async def raise_if_cancelled(self) -> None:
        owns_lease, cancel_requested = await self._repo.check_control(
            self.task_id,
            worker_id=self.worker_id,
        )
        if not owns_lease:
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("Task lease was lost")
        if cancel_requested:
            self._request_cancel("cancelled")
            raise asyncio.CancelledError("Task was cancelled")

    def _request_cancel(self, reason: str) -> None:
        self._cancel_requested = reason == "cancelled"
        self.cancellation_reason = reason

    async def _update(self, data: dict[str, Any]) -> None:
        if not await self._repo.update_owned(self.task_id, worker_id=self.worker_id, data=data):
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("Task lease was lost")


class Tasker:
    """持久 Task Service 门面；执行只发生在独立 ARQ worker。"""

    def __init__(self, default_timeout_seconds: float = TASKER_DEFAULT_TIMEOUT_SECONDS):
        self.default_timeout_seconds = self._validate_timeout_seconds(default_timeout_seconds)
        self._repo = TaskRepository()

    async def enqueue(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> Task:
        task, _ = await self._enqueue_task(
            name=name,
            task_type=task_type,
            payload=payload or {},
            timeout_seconds=timeout_seconds,
            dedupe_key=None,
        )
        return task

    async def enqueue_unique_by_payload(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        payload_match: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> tuple[Task, bool]:
        return await self._enqueue_task(
            name=name,
            task_type=task_type,
            payload=payload or {},
            timeout_seconds=timeout_seconds,
            dedupe_key=self._dedupe_key(task_type, payload_match),
        )

    async def create_in_session(
        self,
        session,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        payload_match: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> Task:
        """在领域 service 事务中创建 Task；调用方提交后必须显式 publish。"""
        task_id = uuid.uuid4().hex
        record = await self._repo.create_in_session(
            session,
            task_id,
            self._build_task_data(
                name=name,
                task_type=task_type,
                payload=payload,
                timeout_seconds=timeout_seconds,
                dedupe_key=self._dedupe_key(task_type, payload_match) if payload_match is not None else None,
            ),
        )
        return Task.from_dict(record.to_dict())

    async def create_unique_in_session(
        self,
        session,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        payload_match: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> tuple[Task, bool]:
        """在领域 service 事务中按数据库 dedupe 创建 Task。"""
        task_id = uuid.uuid4().hex
        record, created = await self._repo.create_or_get_in_session(
            session,
            task_id,
            self._build_task_data(
                name=name,
                task_type=task_type,
                payload=payload,
                timeout_seconds=timeout_seconds,
                dedupe_key=self._dedupe_key(task_type, payload_match),
            ),
        )
        return Task.from_dict(record.to_dict()), created

    async def publish(self, task: Task) -> None:
        """发布已经由 owning transaction 提交的 Task。"""
        await self._publish_created_task(task)

    async def find_task_by_payload(
        self,
        *,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> Task | None:
        record = await self._repo.find_latest_by_payload(
            task_type=task_type,
            payload_match=payload_match,
            statuses=statuses,
        )
        return Task.from_dict(record.to_dict()) if record else None

    async def list_tasks(self, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        records = await self._repo.list(status=status, limit=limit)
        return {
            "tasks": [record.to_summary_dict() for record in records],
            "summary": await self._repo.summarize(status=status),
        }

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        record = await self._repo.get_by_id(task_id)
        return record.to_dict() if record else None

    async def cancel_task(self, task_id: str) -> Task | None:
        current = await self._repo.get_by_id(task_id)
        before_cancel = None
        if current is not None:
            handler_version = 1 if current.handler_version is None else int(current.handler_version)
            try:
                definition = get_task_definition(current.type, handler_version)
            except ValueError:
                definition = get_task_definition(current.type)
            if lite_mode_enabled() and definition.requires_knowledge:
                return None
            failure_handler = definition.load_failure_handler()
            if failure_handler is not None:
                before_cancel = partial(failure_handler, error="任务已取消")

        record = await self._repo.request_cancel(task_id, before_cancel=before_cancel)
        return Task.from_dict(record.to_dict()) if record else None

    async def delete_task(self, task_id: str) -> bool:
        current = await self._repo.get_by_id(task_id)
        if current is None:
            return False
        if lite_mode_enabled():
            try:
                definition = get_task_definition(current.type)
            except ValueError:
                return False
            if definition.requires_knowledge:
                return False
        return await self._repo.delete_terminal(task_id)

    @staticmethod
    async def _publish_created_task(task: Task) -> None:
        try:
            await publish_task(task.id)
        except Exception:
            logger.error("Task publication failed; pending intent will be retried: task_id=%s", task.id, exc_info=True)

    async def _enqueue_task(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        timeout_seconds: float | None,
        dedupe_key: str | None,
    ) -> tuple[Task, bool]:
        record, created = await self._repo.create(
            uuid.uuid4().hex,
            self._build_task_data(
                name=name,
                task_type=task_type,
                payload=payload,
                timeout_seconds=timeout_seconds,
                dedupe_key=dedupe_key,
            ),
        )
        task = Task.from_dict(record.to_dict())
        if created:
            await self._publish_created_task(task)
        return task, created

    def _build_task_data(
        self,
        *,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        timeout_seconds: float | None,
        dedupe_key: str | None,
    ) -> dict[str, Any]:
        definition = get_task_definition(task_type)
        now = utc_now_naive()
        return {
            "name": name,
            "type": task_type,
            "status": "pending",
            "progress": 0.0,
            "message": "任务等待 worker 执行",
            "payload": payload,
            "result": None,
            "error": None,
            "cancel_requested": 0,
            "recovery_strategy": definition.recovery_strategy,
            "handler_version": definition.version,
            "dedupe_key": dedupe_key,
            "attempt_count": 0,
            "timeout_seconds": self._resolve_timeout_seconds(timeout_seconds),
            "created_at": now,
            "updated_at": now,
        }

    def _resolve_timeout_seconds(self, timeout_seconds: float | None) -> float:
        if timeout_seconds is None:
            return self.default_timeout_seconds
        return self._validate_timeout_seconds(timeout_seconds)

    @staticmethod
    def _validate_timeout_seconds(timeout_seconds: float) -> float:
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= TASKER_MAX_TIMEOUT_SECONDS:
            raise ValueError(f"Task timeout must be between 0 and {TASKER_MAX_TIMEOUT_SECONDS} seconds")
        return timeout_seconds

    @staticmethod
    def _dedupe_key(task_type: str, payload_match: dict[str, Any]) -> str:
        serialized = json.dumps(payload_match, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{task_type}:{serialized}".encode()).hexdigest()


async def _heartbeat_task(context: TaskContext, execution: asyncio.Task[Any]) -> None:
    repo = TaskRepository()
    while not execution.done():
        await asyncio.sleep(TASK_HEARTBEAT_SECONDS)
        if execution.done():
            return
        try:
            renewed, cancel_requested = await repo.renew_lease(
                context.task_id,
                worker_id=context.worker_id,
                lease_seconds=TASK_LEASE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Task heartbeat failed; cancelling owner: task_id=%s", context.task_id, exc_info=True)
            context._request_cancel("lease_lost")
            execution.cancel()
            return
        if not renewed:
            context._request_cancel("lease_lost")
            execution.cancel()
            return
        if cancel_requested:
            context._request_cancel("cancelled")
            execution.cancel()
            return


async def _finish_task_failure(
    repo: TaskRepository,
    *,
    task_id: str,
    owner: str,
    status: str,
    message: str,
    error: str,
    failure_handler,
) -> None:
    before_finish = partial(failure_handler, error=error) if failure_handler is not None else None
    await repo.finish_owned(
        task_id,
        worker_id=owner,
        status=status,
        message=message,
        error=error,
        before_finish=before_finish,
    )


async def process_task(ctx: dict[str, Any], task_id: str) -> None:
    """从 PG Task 意图重建并执行一个注册 Handler。"""
    repo = TaskRepository()
    record = await repo.get_by_id(task_id)
    if record is None or record.status in TERMINAL_STATUSES:
        return

    handler_version = 1 if record.handler_version is None else int(record.handler_version)
    try:
        definition = get_task_definition(record.type, handler_version)
    except ValueError:
        logger.error("Durable Task has unknown Handler metadata: task_id=%s, type=%s", task_id, record.type)
        return
    if lite_mode_enabled() and definition.requires_knowledge:
        return
    if record.cancel_requested:
        failure_handler = definition.load_failure_handler()
        before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
        await repo.request_cancel(task_id, before_cancel=before_cancel)
        return

    process_identity = str(ctx.get("worker_id") or "task-worker")
    owner = f"{process_identity}:{uuid.uuid4().hex}"
    record, claimed = await repo.claim(
        task_id,
        worker_id=owner,
        lease_seconds=TASK_LEASE_SECONDS,
        max_running=DURABLE_TASK_MAX_RUNNING,
    )
    if not claimed or record is None:
        return

    failure_handler = None
    try:
        success_handler = definition.load_success_handler()
        failure_handler = definition.load_failure_handler()
        handler = definition.load_handler()
    except Exception as exc:
        await _finish_task_failure(
            repo,
            task_id=task_id,
            owner=owner,
            status="failed",
            message="任务 Handler 无法加载",
            error=str(exc),
            failure_handler=failure_handler,
        )
        return

    context = TaskContext(task_id, owner, record.payload or {})
    execution = asyncio.create_task(handler(context), name=f"durable-task:{task_id}")
    heartbeat = asyncio.create_task(_heartbeat_task(context, execution), name=f"durable-task-heartbeat:{task_id}")
    try:
        timeout_seconds = float(record.timeout_seconds or TASKER_DEFAULT_TIMEOUT_SECONDS)
        done, _ = await asyncio.wait({execution}, timeout=timeout_seconds)
        if execution not in done:
            context._request_cancel("timeout")
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            await _finish_task_failure(
                repo,
                task_id=task_id,
                owner=owner,
                status="failed",
                message="任务执行超时",
                error=f"Task exceeded the {timeout_seconds:g}-second execution timeout",
                failure_handler=failure_handler,
            )
            return
        result = await execution
        before_finish = partial(success_handler, result=result) if success_handler is not None else None
        before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
        await repo.finish_owned(
            task_id,
            worker_id=owner,
            status="success",
            message="任务已完成",
            result=result,
            before_finish=before_finish,
            before_cancel=before_cancel,
        )
    except asyncio.CancelledError:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        if context.cancellation_reason == "cancelled":
            await _finish_task_failure(
                repo,
                task_id=task_id,
                owner=owner,
                status="cancelled",
                message="任务已取消",
                error="任务已取消",
                failure_handler=failure_handler,
            )
        elif context.cancellation_reason != "lease_lost":
            shutdown_error = "worker_shutdown: worker 停止时任务中断"
            before_fail = partial(failure_handler, error=shutdown_error) if failure_handler is not None else None
            await repo.release_interrupted_owner(
                task_id,
                worker_id=owner,
                error=shutdown_error,
                before_fail=before_fail,
            )
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            raise
    except Exception as exc:
        logger.exception("Durable task failed: task_id=%s, type=%s", task_id, record.type)
        await _finish_task_failure(
            repo,
            task_id=task_id,
            owner=owner,
            status="failed",
            message="任务执行失败",
            error=str(exc),
            failure_handler=failure_handler,
        )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


tasker = Tasker()


__all__ = ["process_task", "tasker", "Task", "TaskContext", "Tasker"]
