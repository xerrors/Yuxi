"""AgentRun worker 职责。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.repositories.runs import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.modules.agents.runtime.sandbox import get_sandbox_provider
from yuxi.modules.agents.services.event_writer import publish_run_settlement
from yuxi.modules.agents.services.run_lifecycle import (
    complete_run_runtime_cleanup,
    finish_run_lifecycle,
    reconcile_cancelling_turns,
    reconcile_expired_run,
)
from yuxi.modules.agents.services.tracing import finish_turn_observation_if_terminal

RUN_LEASE_SECONDS = 120


WORKER_ID = f"worker-{uuid.uuid4().hex}"


async def maintain_user_sandboxes() -> None:
    """由所选后端对账执行资源，并维护自身的空闲或孤儿环境。"""
    await asyncio.to_thread(get_sandbox_provider().maintain)


async def mark_run_running(run_id: str, worker_id: str) -> bool:
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        _, acquired = await repo.mark_running(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )
        return acquired


async def renew_run_lease(run_id: str, worker_id: str) -> bool:
    """在独立事务中续租；owner 或 lease 已失效时返回 False。"""
    async with pg_manager.get_async_session_context() as db:
        return await AgentRunRepository(db).renew_lease(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )


async def run_attempt_finished(run_id: str, worker_id: str) -> bool:
    """确认终态由当前最后一次 attempt 提交，不能把其他 Owner 的终态当作成功收尾。"""
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        run = await repo.get_run(run_id)
        if run is None or run.status not in TERMINAL_RUN_STATUSES:
            return False
        attempts = await repo.list_run_attempts(run_id)
        if not attempts:
            return False
        attempt = attempts[-1]
        return (
            attempt.worker_id == worker_id
            and attempt.outcome == run.status
            and attempt.finished_at is not None
            and attempt.finished_at == run.finished_at
        )


async def release_run_lease_for_retry(run_id: str, worker_id: str) -> bool:
    """释放当前 attempt 的 lease，允许下一次 ARQ attempt 使用新 token。"""
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        released = await repo.release_lease_for_retry(run_id, worker_id=worker_id)
    return released


async def reconcile_expired_run_leases(*, now: datetime | None = None) -> list[str]:
    """收敛过期 Run ownership；重复或并发执行只返回本次实际转换的 Run。"""
    async with pg_manager.get_async_session_context() as db:
        candidates = await AgentRunRepository(db).list_expired_lease_candidates(now=now)
    reconciled: list[str] = []
    terminal_turn_ids: list[str] = []
    failures = 0
    for run_id, _thread_id, _uid, _app_id in candidates:
        try:
            async with pg_manager.get_async_session_context() as db:
                run = await reconcile_expired_run(db, run_id=run_id, now=now)
                if run is None:
                    continue
                terminal_turn_ids.append(run.turn_id)
                reconciled.append(run.id)
        except Exception:
            failures += 1
            logger.exception("Expired lease recovery failed: run_id={}", run_id)
    for turn_id in terminal_turn_ids:
        await finish_turn_observation_if_terminal(turn_id)
    await reconcile_pending_runtime_cleanups()
    if failures:
        raise RuntimeError(f"失联执行恢复失败：{failures} 条")
    return reconciled


async def reconcile_pending_runtime_cleanups() -> list[str]:
    """重试 PostgreSQL 持久拥有的 runtime cleanup，并在成功后发布终态。"""
    async with pg_manager.get_async_session_context() as db:
        pending_runs = await AgentRunRepository(db).list_pending_runtime_cleanups()
    cleaned: list[str] = []
    failures = 0
    for run in pending_runs:
        try:
            if not await complete_run_runtime_cleanup(run):
                continue
            if run.status in TERMINAL_RUN_STATUSES:
                await publish_run_settlement(run.id, run.status, thread_id=run.thread_id)
            async with pg_manager.get_async_session_context() as db:
                current = await AgentRunRepository(db).get_run(run.id)
            if current is not None:
                await finish_run_lifecycle(current)
            cleaned.append(run.id)
        except Exception:
            failures += 1
            logger.exception("Run cleanup recovery failed: run_id={}", run.id)
    await reconcile_cancelling_turns()
    if failures:
        raise RuntimeError(f"执行清理恢复失败：{failures} 条")
    return cleaned
