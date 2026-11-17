"""Agent Run 的 ARQ 投递、Redis 增量和取消提示传输。"""

from __future__ import annotations

import asyncio
import json
import os

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.redis import get_async_redis_client
from yuxi.infrastructure.redis.queue import get_arq_pool

RUN_CANCEL_KEY_TTL_SECONDS = int(os.getenv("RUN_CANCEL_KEY_TTL_SECONDS", "1800"))
RUN_EVENTS_STREAM_TTL_SECONDS = int(os.getenv("RUN_EVENTS_STREAM_TTL_SECONDS", "7200"))
RUN_EVENTS_STREAM_MAXLEN = int(os.getenv("RUN_EVENTS_STREAM_MAXLEN", "0"))


def _cancel_key(run_id: str) -> str:
    """生成当前 Run 的取消提示键。"""
    return f"run:cancel:{run_id}"


def _event_stream_key(run_id: str) -> str:
    """生成当前 Run 的短期事件流键。"""
    return f"run:events:v2:{run_id}"


async def get_redis_client():
    """取得当前进程共用的短期传输连接。"""
    return await get_async_redis_client()


async def enqueue_agent_run(run_id: str) -> None:
    """只投递 owning transaction 已提交的 Run。"""
    queue = await get_arq_pool()
    await queue.enqueue_job("process_agent_run", run_id, _job_id=f"run:{run_id}")


async def publish_cancel_signal(run_id: str) -> None:
    """尽力发布带过期时间的取消提示。"""
    try:
        redis = await get_redis_client()
        key = _cancel_key(run_id)
        await redis.set(key, "1", ex=RUN_CANCEL_KEY_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"Failed to publish cancel signal for run {run_id}: {e}")


async def publish_cancel_signals(run_ids: list[str]) -> None:
    """并发发布一组 best-effort Run 取消信号。"""
    await asyncio.gather(*(publish_cancel_signal(run_id) for run_id in run_ids))


async def _read_cancel_signal(run_id: str) -> bool:
    """读取当前 Run 的取消提示。"""
    redis = await get_redis_client()
    return bool(await redis.get(_cancel_key(run_id)))


async def wait_for_cancel_signal(run_id: str, poll_interval_seconds: float = 1.0) -> bool:
    """按固定间隔读取 Redis key，直到收到取消或 watcher 被关闭。"""

    poll_interval_seconds = max(0.0, float(poll_interval_seconds))
    loop = asyncio.get_running_loop()
    key_failure_logged = False

    while True:
        attempt_started_at = loop.time()
        try:
            if await _read_cancel_signal(run_id):
                return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not key_failure_logged:
                logger.warning(f"Failed to read cancel signal for run {run_id}: {e}")
                key_failure_logged = True
        else:
            key_failure_logged = False

        remaining = poll_interval_seconds - (loop.time() - attempt_started_at)
        await asyncio.sleep(max(0.0, remaining))


async def clear_cancel_signal(run_id: str) -> None:
    """尽力清除当前 Run 的取消提示。"""
    try:
        redis = await get_redis_client()
        key = _cancel_key(run_id)
        await redis.delete(key)
    except Exception as e:
        logger.warning(f"Failed to clear cancel signal for run {run_id}: {e}")


async def append_run_stream_events(run_id: str, events: list[dict]) -> list[str]:
    """批量写入逐条公开事件，保留顺序与各自恢复位置。"""
    if not events:
        return []
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    kwargs = {}
    if RUN_EVENTS_STREAM_MAXLEN > 0:
        kwargs = {"maxlen": RUN_EVENTS_STREAM_MAXLEN, "approximate": True}
    async with redis.pipeline(transaction=False) as pipeline:
        for event in events:
            if event.get("yuxi", {}).get("run_id") != run_id:
                raise ValueError("公开事件 Run 归属不一致")
            pipeline.xadd(key, {"version": "2", "event": json.dumps(event, ensure_ascii=False)}, **kwargs)
        pipeline.expire(key, RUN_EVENTS_STREAM_TTL_SECONDS)
        result = await pipeline.execute()
    return [str(cursor) for cursor in result[:-1]]


def _decode_run_stream_row(run_id: str, cursor: str, fields: dict) -> dict:
    """仅接受当前公开格式，不读取旧封套。"""
    if fields.get("version") != "2":
        raise ValueError("Redis 事件格式版本不兼容")
    event = json.loads(fields["event"])
    if event.get("yuxi", {}).get("run_id") != run_id:
        raise ValueError("Redis 事件 Run 归属不一致")
    return {"seq": str(cursor), "event": event}


async def list_run_stream_events(
    run_id: str,
    *,
    after_seq: str = "0-0",
    limit: int = 200,
) -> list[dict]:
    """按游标读取当前 Run 的后续事件。"""
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    start = "-" if after_seq in {"0-0", ""} else f"({after_seq}"
    rows = await redis.xrange(key, min=start, max="+", count=limit)
    return [_decode_run_stream_row(run_id, event_id, fields) for event_id, fields in rows]


async def list_recent_run_stream_events(run_id: str, *, limit: int = 100) -> list[dict]:
    """从 Redis Stream 反向读取最近的 run events，返回顺序为新到旧。"""
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    rows = await redis.xrevrange(key, max="+", min="-", count=limit)
    return [_decode_run_stream_row(run_id, event_id, fields) for event_id, fields in rows]
