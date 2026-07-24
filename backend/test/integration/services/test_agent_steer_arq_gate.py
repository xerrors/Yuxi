"""Steer 实施 Gate：验证 ARQ running job 租约到期后可重领。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.constants import in_progress_key_prefix
from arq.worker import Worker, func

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_expired_running_job_lease_reuses_same_job_id():
    """模拟 worker 异常退出遗留租约，确认租约到期后同一 job 会被重新领取。"""
    suffix = uuid.uuid4().hex
    queue_name = f"arq:queue:steer-gate:{suffix}"
    job_id = f"steer-gate-{suffix}"
    result_key = f"steer-gate:result:{suffix}"
    pool = await create_pool(RedisSettings(host="redis"), default_queue_name=queue_name)

    async def gate_job(ctx) -> None:
        await ctx["redis"].set(result_key, "executed", ex=30)

    worker = Worker(
        [func(gate_job, name="gate_job")],
        redis_pool=pool,
        queue_name=queue_name,
        ctx={"redis": pool},
        burst=True,
        poll_delay=0.01,
        handle_signals=False,
    )
    try:
        await pool.enqueue_job("gate_job", _job_id=job_id)
        lease_key = in_progress_key_prefix + job_id
        await pool.psetex(lease_key, 100, b"1")

        await worker.start_jobs([job_id.encode()])
        assert worker.tasks == {}
        assert await pool.get(result_key) is None

        await asyncio.sleep(0.15)
        await worker.start_jobs([job_id.encode()])
        await asyncio.gather(*worker.tasks.values())

        assert await pool.get(result_key) == b"executed"
    finally:
        await pool.delete(result_key, in_progress_key_prefix + job_id)
        await pool.zrem(queue_name, job_id)
        await worker.close()
