"""Run 的短期增量仍按 Redis Stream 保存并过期。"""

import json
import uuid

import pytest

from yuxi.modules.agents.services.transport import (
    RUN_EVENTS_STREAM_TTL_SECONDS,
    append_run_stream_events,
    get_redis_client,
)
from yuxi.infrastructure.redis import close_async_redis_client

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture(autouse=True)
async def isolated_run_events_redis_client():
    """只在当前测试的事件循环内复用 Redis 客户端。"""
    await close_async_redis_client()
    yield
    await close_async_redis_client()


async def test_stream_batch_preserves_payload_and_expiry():
    """从真实 Redis 回读事件身份、内容与 TTL。"""
    run_id = str(uuid.uuid4())
    key = f"run:events:v2:{run_id}"
    redis = await get_redis_client()
    first = {
        "type": "yuxi.run.metadata",
        "yuxi": {"run_id": run_id, "thread_id": "test-thread"},
        "probe": "batch",
    }
    second = {
        "type": "yuxi.session.run.settled",
        "yuxi": {"run_id": run_id, "thread_id": "test-thread"},
        "status": "completed",
    }
    try:
        cursors = await append_run_stream_events(run_id, [first, second])
        rows = await redis.xrange(key)
        assert len(rows) == 2
        assert [row[0] for row in rows] == cursors
        assert cursors[0] < cursors[1]
        assert all(row[1]["version"] == "2" for row in rows)
        assert [json.loads(row[1]["event"]) for row in rows] == [first, second]
        assert 0 < await redis.ttl(key) <= RUN_EVENTS_STREAM_TTL_SECONDS
    finally:
        await redis.delete(key)
