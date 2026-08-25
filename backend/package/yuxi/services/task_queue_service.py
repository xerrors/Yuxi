from __future__ import annotations

from functools import partial

from yuxi.config.runtime import lite_mode_enabled
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.services.task_registry import get_task_definition, list_task_definitions
from yuxi.utils.logging_config import logger

TASK_LEASE_SECONDS = 30.0
TASK_HEARTBEAT_SECONDS = 10.0
TASK_RECONCILIATION_SECONDS = 30.0
TASK_RECONCILIATION_HEALTH_KEY = "yuxi:worker:health:durable-task-reconciliation-v1"
TASK_RECONCILIATION_HEALTH_TTL_SECONDS = int(TASK_RECONCILIATION_SECONDS * 2 + 5)


async def publish_task(task_id: str) -> None:
    """把已提交的 PG Task 发布为 ARQ 唤醒消息；重复消息由数据库 claim 拒绝。"""
    pool = await get_arq_pool()
    await pool.enqueue_job("process_task", task_id)


async def finalize_task_failure(session, record, error: str) -> None:
    """在 Task 失败事务内执行已注册的领域收敛。"""
    handler_version = 1 if record.handler_version is None else int(record.handler_version)
    try:
        definition = get_task_definition(record.type, handler_version)
    except ValueError:
        definition = get_task_definition(record.type)
    if lite_mode_enabled() and definition.requires_knowledge:
        return
    handler = definition.load_failure_handler()
    if handler is not None:
        await handler(session, record, error)


async def reconcile_and_publish_tasks() -> list[tuple[str, str, int]]:
    """收敛失联 owner，并发布所有当前 pending Task。"""
    repository = TaskRepository()
    task_types = None
    if lite_mode_enabled():
        task_types = {
            definition.task_type for definition in list_task_definitions() if not definition.requires_knowledge
        }
    reconciled = await repository.reconcile_expired_leases(
        before_fail=finalize_task_failure,
        task_types=task_types,
    )
    await publish_pending_tasks()
    await repository.prune_terminal(task_types=task_types)
    return reconciled


async def publish_pending_tasks(*, limit: int = 200) -> list[str]:
    """重发 PG 中待执行的任务；重复 ARQ 消息由 task_id/attempt 去重。"""
    published: list[str] = []
    repository = TaskRepository()
    for record in await repository.list_pending(limit=limit):
        try:
            current_definition = get_task_definition(record.type)
        except ValueError as exc:
            if lite_mode_enabled():
                continue
            logger.error("Cannot publish unknown durable task: task_id=%s, type=%s", record.id, record.type)
            await repository.fail_pending(record.id, error=str(exc))
            continue
        if lite_mode_enabled() and current_definition.requires_knowledge:
            continue
        if record.cancel_requested:
            failure_handler = current_definition.load_failure_handler()
            before_cancel = partial(failure_handler, error="任务已取消") if failure_handler is not None else None
            await repository.request_cancel(record.id, before_cancel=before_cancel)
            continue
        handler_version = 1 if record.handler_version is None else int(record.handler_version)
        try:
            get_task_definition(record.type, handler_version)
        except ValueError as exc:
            error = str(exc)
            logger.error("Cannot rebuild durable task: task_id=%s, type=%s", record.id, record.type)
            failure_handler = current_definition.load_failure_handler()
            before_fail = partial(failure_handler, error=error) if failure_handler is not None else None
            await repository.fail_pending(record.id, error=error, before_fail=before_fail)
            continue
        await publish_task(record.id)
        published.append(record.id)
    return published
