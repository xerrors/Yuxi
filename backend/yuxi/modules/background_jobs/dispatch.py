from __future__ import annotations

import math
import os
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.redis.queue import get_arq_pool
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.background_jobs.registry import get_job_definition
from yuxi.modules.background_jobs.service import job_tracker

BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS = float(os.getenv("BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS", 6 * 60 * 60))
if not math.isfinite(BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS) or BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS <= 0:
    raise ValueError("BackgroundJob timeout must be a positive finite number of seconds")


async def register_job_in_session(
    session,
    *,
    name: str,
    job_type: str,
    payload: dict[str, Any],
    payload_match: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[BackgroundJobRecord, bool]:
    """在领域事务中登记 worker 动作；调用方提交后显式投递。"""
    definition = get_job_definition(job_type)
    return await job_tracker.register_in_session(
        session,
        name=name,
        job_type=job_type,
        payload=payload,
        payload_match=payload_match,
        handler_version=definition.version,
        timeout_seconds=resolve_job_timeout(timeout_seconds),
    )


async def dispatch_job(job_id: str) -> None:
    """投递已提交的执行意图；失败留给 worker 的 pending 补发。"""
    try:
        await publish_job(job_id)
    except Exception:
        logger.opt(exception=True).error("BackgroundJob publication failed; pending intent will be retried: job_id={}", job_id)


async def publish_job(job_id: str) -> None:
    """把已提交的 PG BackgroundJob 发布为 ARQ 唤醒消息；重复消息由数据库 claim 拒绝。"""
    pool = await get_arq_pool()
    await pool.enqueue_job("process_background_job", job_id)


def resolve_job_timeout(timeout_seconds: float | None, *, default: float = BACKGROUND_JOB_DEFAULT_TIMEOUT_SECONDS) -> float:
    """校验 worker 的执行预算，单次覆盖不能超过 worker 默认值。"""
    if not math.isfinite(default) or default <= 0:
        raise ValueError("BackgroundJob timeout must be a positive finite number of seconds")
    resolved = default if timeout_seconds is None else float(timeout_seconds)
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError("BackgroundJob timeout must be a positive finite number of seconds")
    if resolved > default:
        raise ValueError("BackgroundJob timeout cannot exceed the worker default timeout")
    return resolved
