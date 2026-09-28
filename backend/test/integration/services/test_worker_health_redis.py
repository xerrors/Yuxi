"""使用真实 ARQ 心跳和 Redis 验证轻量探针。"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from arq import create_pool
from arq.worker import Worker
from yuxi.services import worker_health
from yuxi.storage.redis import get_arq_redis_settings

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def unused_job(ctx):
    """满足 ARQ 注册约束，健康检查测试不投递任务。"""


@pytest_asyncio.fixture
async def health_redis(monkeypatch):
    """健康键和队列仅属于本测试，清理不影响共享 worker。"""
    redis = await create_pool(get_arq_redis_settings())
    key = f"pytest-worker-health:{uuid.uuid4().hex}"
    monkeypatch.setattr(worker_health, "WORKER_HEALTH_KEY", key)
    try:
        yield redis, key
    finally:
        await redis.delete(key)
        await redis.aclose()


async def test_arq_heartbeat_expires_without_renewal(health_redis):
    """ARQ 原生心跳可通过探针，停止续租后由 Redis 过期事实拒绝。"""
    redis, key = health_redis
    worker = Worker(
        functions=[unused_job],
        redis_pool=redis,
        queue_name=f"{key}:queue",
        health_check_key=key,
        health_check_interval=0.1,
        handle_signals=False,
    )
    await worker.record_health()
    assert await redis.get(key)
    assert 0 < await redis.pttl(key) <= 1100
    assert worker_health.main() == 0
    await asyncio.sleep(1.2)
    assert await redis.get(key) is None
    assert worker_health.main() == 1


@pytest.mark.parametrize("state", ["missing", "empty", "persistent", "excessive"])
async def test_invalid_redis_leases_fail(health_redis, state):
    """Redis 中的非法心跳不能维持健康状态。"""
    redis, key = health_redis
    if state == "empty":
        await redis.set(key, b"", px=1000)
    elif state == "persistent":
        await redis.set(key, b"alive")
    elif state == "excessive":
        await redis.set(key, b"alive", px=worker_health.WORKER_HEALTH_MAX_TTL_MS + 60000)
    assert worker_health.main() == 1


async def test_unreachable_redis_fails(monkeypatch):
    """连接拒绝产生非零结果。"""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    assert worker_health.main() == 1
