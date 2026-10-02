"""Run queue/redis helpers."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

from yuxi.services.worker_health import WORKER_HEALTH_KEY
from yuxi.storage.redis import close_async_redis_client, create_arq_redis_pool, get_async_redis_client
from yuxi.utils.logging_config import logger

RUN_CANCEL_KEY_TTL_SECONDS = int(os.getenv("RUN_CANCEL_KEY_TTL_SECONDS", "1800"))
RUN_EVENTS_STREAM_TTL_SECONDS = int(os.getenv("RUN_EVENTS_STREAM_TTL_SECONDS", "7200"))
RUN_EVENTS_STREAM_MAXLEN = int(os.getenv("RUN_EVENTS_STREAM_MAXLEN", "0"))
RUN_RECONCILIATION_SECONDS = 30
WORKER_RECONCILIATION_HEALTH_KEY = f"{WORKER_HEALTH_KEY}:lease-reconciliation"
WORKER_RECONCILIATION_HEALTH_TTL_SECONDS = RUN_RECONCILIATION_SECONDS * 2 + 5

_arq_pool = None


def _cancel_key(run_id: str) -> str:
    return f"run:cancel:{run_id}"


def _event_stream_key(run_id: str) -> str:
    return f"run:events:{run_id}"


def _is_valid_stream_seq(value: str) -> bool:
    major, sep, minor = value.partition("-")
    if sep != "-":
        return False
    return major.isdigit() and minor.isdigit()


def normalize_after_seq(after_seq: str | None) -> str:
    """Normalize after_seq cursor to redis stream id format."""
    if after_seq is None:
        return "0-0"

    text = str(after_seq).strip()
    if not text:
        return "0-0"

    if _is_valid_stream_seq(text):
        return text
    return "0-0"


def build_run_event_envelope(
    *,
    run_id: str,
    event_type: str,
    payload: dict | None = None,
    thread_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "thread_id": thread_id,
        "event": event_type,
        "payload": payload or {},
        "created_at": created_at or datetime.now(tz=UTC).isoformat(),
    }


def _payload_thread_id(payload: dict | None) -> str | None:
    chunk = payload.get("chunk") if isinstance(payload, dict) else None
    if not isinstance(chunk, dict):
        return None
    thread_id = chunk.get("thread_id")
    return thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else None


async def get_redis_client():
    return await get_async_redis_client()


async def get_arq_pool():
    global _arq_pool
    if _arq_pool is not None:
        return _arq_pool

    _arq_pool = await create_arq_redis_pool()
    return _arq_pool


async def publish_cancel_signal(run_id: str) -> None:
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
    try:
        redis = await get_redis_client()
        key = _cancel_key(run_id)
        await redis.delete(key)
    except Exception as e:
        logger.warning(f"Failed to clear cancel signal for run {run_id}: {e}")


async def append_run_stream_event(run_id: str, event_type: str, payload: dict, *, thread_id: str | None = None) -> str:
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    now = datetime.now(tz=UTC)
    now_ms = int(now.timestamp() * 1000)
    event_thread_id = thread_id or _payload_thread_id(payload)
    envelope = build_run_event_envelope(
        run_id=run_id,
        event_type=event_type,
        payload=payload or {},
        thread_id=event_thread_id,
        created_at=now.isoformat(),
    )
    fields = {
        "event_type": event_type,
        "payload": json.dumps(envelope, ensure_ascii=False),
        "ts": str(now_ms),
    }

    kwargs = {}
    if RUN_EVENTS_STREAM_MAXLEN > 0:
        kwargs["maxlen"] = RUN_EVENTS_STREAM_MAXLEN
        kwargs["approximate"] = True

    # 同一连接顺序发出写入和续期，省去两次往返之间的事件循环等待。
    async with redis.pipeline(transaction=False) as pipeline:
        pipeline.xadd(key, fields, **kwargs)
        pipeline.expire(key, RUN_EVENTS_STREAM_TTL_SECONDS)
        event_id, _ = await pipeline.execute()
    return str(event_id)


def _decode_run_stream_row(run_id: str, event_id: str, fields: dict) -> dict:
    """解码单条 Redis Stream 事件，兼容旧载荷并保持统一返回形状。"""
    payload_raw = fields.get("payload") or "{}"
    try:
        payload = json.loads(payload_raw)
    except Exception:
        payload = {}

    event_type = fields.get("event_type") or "message"
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "thread_id": None,
            "event": event_type,
            "payload": payload if isinstance(payload, dict) else {},
            "created_at": None,
        }

    ts_value = fields.get("ts")
    return {
        "seq": str(event_id),
        "event_type": event_type,
        "payload": payload,
        "ts": int(ts_value) if ts_value else None,
    }


async def list_run_stream_events(
    run_id: str,
    *,
    after_seq: str = "0-0",
    limit: int = 200,
) -> list[dict]:
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    start = "-" if after_seq in {"0-0", ""} else f"({after_seq}"
    rows = await redis.xrange(key, min=start, max="+", count=limit)
    events = []

    for event_id, fields in rows:
        events.append(_decode_run_stream_row(run_id, event_id, fields))
    return events


async def list_recent_run_stream_events(run_id: str, *, limit: int = 100) -> list[dict]:
    """从 Redis Stream 反向读取最近的 run events，返回顺序为新到旧。"""
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    rows = await redis.xrevrange(key, max="+", min="-", count=limit)
    events = []

    for event_id, fields in rows:
        events.append(_decode_run_stream_row(run_id, event_id, fields))
    return events


async def get_last_run_stream_seq(run_id: str) -> str:
    redis = await get_redis_client()
    key = _event_stream_key(run_id)
    rows = await redis.xrevrange(key, max="+", min="-", count=1)
    if not rows:
        return "0-0"
    event_id, _ = rows[0]
    return str(event_id)


async def close_queue_clients() -> None:
    global _arq_pool
    if _arq_pool is not None:
        try:
            await _arq_pool.close()
        except Exception:
            pass
        _arq_pool = None
    await close_async_redis_client()
