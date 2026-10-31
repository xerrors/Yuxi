"""Worker 进程装配与后台执行维护。"""

import asyncio
import inspect

from yuxi.bootstrap.capabilities import knowledge_enabled, personal_assistant_enabled
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.schema import require_current_schema
from yuxi.infrastructure.worker_health import (
    JOB_RECONCILIATION_HEALTH_KEY,
    JOB_RECONCILIATION_HEALTH_TTL_SECONDS,
    JOB_RECONCILIATION_SECONDS,
    RUN_RECONCILIATION_SECONDS,
    WORKER_RECONCILIATION_HEALTH_KEY,
    WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    publish_worker_health,
)
from yuxi.modules.agents.services.cooperation import reconcile_stopped_trees, recover_cooperation_waits
from yuxi.modules.agents.services.leases import (
    WORKER_ID,
    maintain_user_sandboxes,
    reconcile_expired_run_leases,
    reconcile_pending_runtime_cleanups,
)
from yuxi.modules.agents.services.scheduler import recover_pending_dispatches
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.knowledge.mcp.auth import require_knowledge_delegation_config
from yuxi.modules.schedules.service import claim_and_dispatch_due_jobs, recover_scheduled_dispatches
from yuxi.workers.background_jobs import reconcile_and_publish_jobs

_RECONCILIATION_TASK_KEY = "agent_run_reconciliation_task"
_JOB_RECONCILIATION_TASK_KEY = "background_job_reconciliation_task"
_SANDBOX_CLEANUP_TASK_KEY = "sandbox_cleanup_task"


async def startup(ctx: dict) -> None:
    """初始化 worker 依赖。"""

    if not isinstance(ctx, dict):
        raise TypeError("ARQ worker context 必须是字典")
    ctx["worker_id"] = WORKER_ID
    personal_assistant_enabled()
    knowledge = knowledge_enabled()
    if knowledge:
        require_knowledge_delegation_config()
    AuthUtils.require_security_secrets()
    from yuxi.modules.agents.runtime.sandbox import check_sandbox_provider

    await check_sandbox_provider()
    pg_manager.initialize()
    await require_current_schema(pg_manager)
    from yuxi.modules.system.options import system_options
    from yuxi.modules.system.services.options import invalidate_option_cache

    await invalidate_option_cache(system_options.key)
    if knowledge:
        from yuxi.infrastructure.neo4j import get_shared_neo4j_connection
        from yuxi.modules.knowledge.runtime import knowledge_base

        # ARQ 消费与 pending 补发必须晚于后端就绪，启动失败保留持久任务意图。
        await knowledge_base.initialize()
        await asyncio.to_thread(get_shared_neo4j_connection)

    await _reconcile_agent_runs_once()
    await reconcile_and_publish_jobs()
    await _publish_job_reconciliation_health()
    ctx[_RECONCILIATION_TASK_KEY] = asyncio.create_task(_reconcile_agent_run_leases_forever())
    ctx[_JOB_RECONCILIATION_TASK_KEY] = asyncio.create_task(_reconcile_background_jobs_forever())
    ctx[_SANDBOX_CLEANUP_TASK_KEY] = asyncio.create_task(_reconcile_sandboxes_forever())


async def shutdown(ctx: dict) -> None:
    """关闭 worker 共享连接。"""

    if isinstance(ctx, dict):
        reconciliation_tasks = [
            ctx.pop(_RECONCILIATION_TASK_KEY, None),
            ctx.pop(_JOB_RECONCILIATION_TASK_KEY, None),
            ctx.pop(_SANDBOX_CLEANUP_TASK_KEY, None),
        ]
        reconciliation_tasks = [task for task in reconciliation_tasks if task is not None]
        for task in reconciliation_tasks:
            task.cancel()
        if reconciliation_tasks:
            await asyncio.gather(*reconciliation_tasks, return_exceptions=True)
    from yuxi.infrastructure.neo4j import close_shared_neo4j_connection
    from yuxi.infrastructure.redis.queue import close_queue_clients

    # ARQ 在 on_shutdown 返回后关闭自己的消费池，单个清理失败不能中断该阶段。
    for component, close in (
        ("queue_clients", close_queue_clients),
        ("neo4j", close_shared_neo4j_connection),
        ("postgres", pg_manager.close),
    ):
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.error(f"Worker shutdown failed: component={component}, error_type={type(exc).__name__}")


async def _reconcile_agent_run_leases_forever() -> None:
    """周期收敛执行状态，各阶段失败不阻断其他调度。"""
    while True:
        await asyncio.sleep(RUN_RECONCILIATION_SECONDS)
        await _reconcile_agent_runs_once()


async def _reconcile_agent_runs_once() -> None:
    """隔离阶段错误；只有完整成功才续报恢复能力健康。"""
    healthy = True
    for phase, operation in (
        ("expired_leases", reconcile_expired_run_leases),
        ("runtime_cleanup", reconcile_pending_runtime_cleanups),
        ("stopped_trees", reconcile_stopped_trees),
        ("cooperation_waits", recover_cooperation_waits),
        ("pending_dispatches", recover_pending_dispatches),
        ("scheduled_dispatches", recover_scheduled_dispatches),
        ("due_jobs", claim_and_dispatch_due_jobs),
    ):
        try:
            await operation()
        except Exception:
            healthy = False
            logger.exception("Agent reconciliation failed: phase=%s", phase)
    if healthy:
        try:
            await _publish_reconciliation_health()
        except Exception:
            logger.exception("Agent reconciliation health publication failed")


async def _publish_reconciliation_health() -> None:
    """续租 worker 的 AgentRun lease 收敛能力；持续失败后 readiness 自动失效。"""

    await publish_worker_health(
        WORKER_RECONCILIATION_HEALTH_KEY,
        WORKER_ID,
        WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


async def _reconcile_background_jobs_forever() -> None:
    """周期收敛失联通用 Job，并补发持久 pending 意图。"""
    while True:
        await asyncio.sleep(JOB_RECONCILIATION_SECONDS)
        try:
            reconciled = await reconcile_and_publish_jobs()
            if reconciled:
                logger.warning("Reconciled expired durable jobs: count=%s", len(reconciled))
            await _publish_job_reconciliation_health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Failed to reconcile durable jobs", exc_info=True)


async def _publish_job_reconciliation_health() -> None:
    """续租 worker 的 后台作业 收敛与 pending 补发能力。"""
    await publish_worker_health(
        JOB_RECONCILIATION_HEALTH_KEY,
        WORKER_ID,
        JOB_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


async def _reconcile_sandboxes_forever() -> None:
    """外部沙盒回收独立运行，慢释放不占据调度恢复循环。"""
    while True:
        try:
            await maintain_user_sandboxes()
        except Exception:
            logger.exception("Sandbox cleanup reconciliation failed")
        await asyncio.sleep(RUN_RECONCILIATION_SECONDS)
