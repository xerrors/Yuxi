from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import yuxi.bootstrap.worker as worker_bootstrap
from yuxi.modules.system.services import options as option_values


@pytest.fixture(autouse=True)
def api_key_derivation_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.check_sandbox_provider", AsyncMock())
    monkeypatch.setattr("yuxi.modules.knowledge.runtime.knowledge_base.initialize", AsyncMock())
    monkeypatch.setattr("yuxi.infrastructure.neo4j.get_shared_neo4j_connection", lambda: None)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-that-is-at-least-32-chars")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "worker-startup-test")
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "test-api-key-derivation-secret-32-chars")


@pytest.mark.asyncio
async def test_worker_startup_prepares_runtime_and_recovers_jobs(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_initialize():
        calls.append("initialize")

    async def fake_require_current_schema(_manager):
        calls.append("require_current_schema")

    async def fake_invalidate_option_cache(key):
        del key
        calls.append("invalidate_option_cache")

    async def fake_recover_pending_dispatches():
        calls.append("recover_pending_dispatches")

    async def fake_publish_reconciliation_health():
        calls.append("publish_reconciliation_health")

    async def fake_publish_job_reconciliation_health():
        calls.append("publish_job_reconciliation_health")

    async def fake_reconcile_expired_run_leases():
        calls.append("reconcile_expired_run_leases")
        return []

    async def fake_reconcile_pending_runtime_cleanups():
        calls.append("reconcile_pending_runtime_cleanups")
        return []

    async def fake_reconciliation_loop():
        calls.append("reconciliation_loop")

    async def fake_reconcile_and_publish_jobs():
        calls.append("reconcile_and_publish_jobs")
        return []

    async def fake_job_reconciliation_loop():
        calls.append("job_reconciliation_loop")

    async def fake_recover_scheduled_dispatches():
        calls.append("recover_scheduled_dispatches")

    async def fake_claim_and_dispatch_due_jobs():
        calls.append("claim_and_dispatch_due_jobs")

    async def fake_checkpoint_sweep_loop():
        calls.append("checkpoint_sweep_loop")

    monkeypatch.setattr(worker_bootstrap.pg_manager, "initialize", fake_initialize)
    monkeypatch.setattr(worker_bootstrap, "require_current_schema", fake_require_current_schema)
    monkeypatch.setattr(option_values, "invalidate_option_cache", fake_invalidate_option_cache)
    monkeypatch.setattr(worker_bootstrap, "recover_pending_dispatches", fake_recover_pending_dispatches)
    for name in ("reconcile_stopped_trees", "recover_cooperation_waits", "maintain_user_sandboxes"):
        monkeypatch.setattr(worker_bootstrap, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(worker_bootstrap, "reconcile_expired_run_leases", fake_reconcile_expired_run_leases)
    monkeypatch.setattr(
        worker_bootstrap,
        "reconcile_pending_runtime_cleanups",
        fake_reconcile_pending_runtime_cleanups,
    )
    monkeypatch.setattr(worker_bootstrap, "_publish_reconciliation_health", fake_publish_reconciliation_health)
    monkeypatch.setattr(worker_bootstrap, "_publish_job_reconciliation_health", fake_publish_job_reconciliation_health)
    monkeypatch.setattr(worker_bootstrap, "_reconcile_agent_run_leases_forever", fake_reconciliation_loop)
    monkeypatch.setattr(worker_bootstrap, "reconcile_and_publish_jobs", fake_reconcile_and_publish_jobs)
    monkeypatch.setattr(worker_bootstrap, "_reconcile_background_jobs_forever", fake_job_reconciliation_loop)
    monkeypatch.setattr(worker_bootstrap, "_reconcile_sandboxes_forever", AsyncMock())
    monkeypatch.setattr(worker_bootstrap, "_sweep_checkpoint_garbage_forever", fake_checkpoint_sweep_loop)
    monkeypatch.setattr(worker_bootstrap, "recover_scheduled_dispatches", fake_recover_scheduled_dispatches)
    monkeypatch.setattr(worker_bootstrap, "claim_and_dispatch_due_jobs", fake_claim_and_dispatch_due_jobs)

    ctx = {}
    await worker_bootstrap.startup(ctx)
    await ctx[worker_bootstrap._RECONCILIATION_TASK_KEY]
    await ctx[worker_bootstrap._JOB_RECONCILIATION_TASK_KEY]
    await ctx[worker_bootstrap._CHECKPOINT_SWEEP_TASK_KEY]

    assert calls == [
        "initialize",
        "require_current_schema",
        "invalidate_option_cache",
        "reconcile_expired_run_leases",
        "reconcile_pending_runtime_cleanups",
        "recover_pending_dispatches",
        "recover_scheduled_dispatches",
        "claim_and_dispatch_due_jobs",
        "publish_reconciliation_health",
        "reconcile_and_publish_jobs",
        "publish_job_reconciliation_health",
        "reconciliation_loop",
        "job_reconciliation_loop",
        "checkpoint_sweep_loop",
    ]
    assert ctx["worker_id"] == worker_bootstrap.WORKER_ID


@pytest.mark.asyncio
async def test_worker_shutdown_closes_queue_clients_before_postgres(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    never_finishes = asyncio.Event()
    reconciliation_job = asyncio.create_task(never_finishes.wait())

    async def fake_close_queue_clients():
        calls.append("redis")

    async def fake_close_postgres():
        calls.append("postgres")

    monkeypatch.setattr("yuxi.infrastructure.redis.queue.close_queue_clients", fake_close_queue_clients)
    monkeypatch.setattr("yuxi.infrastructure.neo4j.close_shared_neo4j_connection", lambda: calls.append("neo4j"))
    monkeypatch.setattr(worker_bootstrap.pg_manager, "close", fake_close_postgres)

    await worker_bootstrap.shutdown({worker_bootstrap._RECONCILIATION_TASK_KEY: reconciliation_job})

    assert calls == ["redis", "neo4j", "postgres"]
    assert reconciliation_job.cancelled()


@pytest.mark.asyncio
async def test_worker_shutdown_closes_postgres_when_queue_close_fails(monkeypatch):
    """队列清理失败仍释放数据库连接，记录故障并允许 ARQ 继续关闭消费池。"""
    failure = ConnectionError("queue close failed")
    postgres = AsyncMock()
    monkeypatch.setattr("yuxi.infrastructure.redis.queue.close_queue_clients", AsyncMock(side_effect=failure))
    monkeypatch.setattr(worker_bootstrap.pg_manager, "close", postgres)
    errors = []
    monkeypatch.setattr(worker_bootstrap.logger, "error", errors.append)
    from arq.worker import Worker

    async def unused_job(_ctx):
        """仅用于构造真实 ARQ worker。"""

    consumer_pool = AsyncMock()
    worker = Worker(functions=[unused_job], redis_pool=consumer_pool, on_shutdown=worker_bootstrap.shutdown, handle_signals=False)
    await worker.close()
    postgres.assert_awaited_once()
    consumer_pool.close.assert_awaited_once_with(close_connection_pool=True)
    assert worker._pool is None
    assert errors == ["Worker shutdown failed: component=queue_clients, error_type=ConnectionError"]


async def test_background_job_publication_failure_does_not_refresh_health(monkeypatch):
    sleep_calls = 0
    health_calls = 0

    async def controlled_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    async def fail_reconciliation():
        raise ConnectionError("arq publication failed")

    async def publish_health():
        nonlocal health_calls
        health_calls += 1

    monkeypatch.setattr(worker_bootstrap.asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(worker_bootstrap, "reconcile_and_publish_jobs", fail_reconciliation)
    monkeypatch.setattr(worker_bootstrap, "_publish_job_reconciliation_health", publish_health)

    with pytest.raises(asyncio.CancelledError):
        await worker_bootstrap._reconcile_background_jobs_forever()

    assert health_calls == 0


@pytest.mark.asyncio
async def test_reconciliation_failure_does_not_refresh_success_lease(monkeypatch: pytest.MonkeyPatch):
    calls = {"reconcile": 0, "publish": 0}

    async def no_wait(_seconds: float):
        return None

    async def fail_then_cancel():
        calls["reconcile"] += 1
        if calls["reconcile"] == 1:
            raise RuntimeError("database schema mismatch")
        raise asyncio.CancelledError

    async def publish():
        calls["publish"] += 1

    monkeypatch.setattr(worker_bootstrap.asyncio, "sleep", no_wait)
    for name in (
        "reconcile_pending_runtime_cleanups",
        "reconcile_stopped_trees",
        "recover_cooperation_waits",
        "recover_pending_dispatches",
        "recover_scheduled_dispatches",
        "claim_and_dispatch_due_jobs",
    ):
        monkeypatch.setattr(worker_bootstrap, name, AsyncMock())
    monkeypatch.setattr(worker_bootstrap, "reconcile_expired_run_leases", fail_then_cancel)
    monkeypatch.setattr(worker_bootstrap, "_publish_reconciliation_health", publish)

    with pytest.raises(asyncio.CancelledError):
        await worker_bootstrap._reconcile_agent_run_leases_forever()

    assert calls == {"reconcile": 2, "publish": 0}


@pytest.mark.parametrize("secret", ["JWT_SECRET_KEY", "API_KEY_DERIVATION_SECRET"])
async def test_worker_rejects_invalid_actual_process_secret(monkeypatch, secret):
    """单独启动 worker 也必须拒绝实际收到的无效密钥。"""
    monkeypatch.setenv(secret, "short")
    with pytest.raises(ValueError, match="至少 32"):
        await worker_bootstrap.startup({})


async def test_worker_rejects_incompatible_schema_before_runtime_recovery(monkeypatch):
    """应用初始化不能替代 worker 自身的版本检查。"""
    monkeypatch.setattr(worker_bootstrap.pg_manager, "initialize", lambda: None)
    monkeypatch.setattr(worker_bootstrap, "require_current_schema", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await worker_bootstrap.startup({})


async def test_reconciliation_continues_later_phases_without_publishing_success(monkeypatch):
    """坏恢复阶段不阻断后续派发，部分成功也不续报完整恢复健康。"""
    progressed = []

    async def bad_wait():
        """模拟已记录的恢复失败。"""
        raise ValueError("broken wait")

    async def dispatch():
        """观察后续派发阶段仍有机会推进。"""
        progressed.append("dispatched")

    for name in (
        "reconcile_expired_run_leases",
        "reconcile_pending_runtime_cleanups",
        "reconcile_stopped_trees",
        "recover_scheduled_dispatches",
        "claim_and_dispatch_due_jobs",
    ):
        monkeypatch.setattr(worker_bootstrap, name, AsyncMock())
    monkeypatch.setattr(worker_bootstrap, "recover_cooperation_waits", bad_wait)
    monkeypatch.setattr(worker_bootstrap, "recover_pending_dispatches", dispatch)
    health = AsyncMock()
    monkeypatch.setattr(worker_bootstrap, "_publish_reconciliation_health", health)
    await worker_bootstrap._reconcile_agent_runs_once()
    assert progressed == ["dispatched"]
    health.assert_not_awaited()
