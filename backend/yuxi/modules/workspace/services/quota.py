"""个人 Workspace 的周期用量统计与软配额。"""

import asyncio
import subprocess
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.runtime_settings import get_int_env
from yuxi.modules.agents.runtime.sandbox.provider import get_sandbox_provider
from yuxi.modules.workspace.paths import measure_allocated_bytes
from yuxi.modules.workspace.repositories.usage import get_usage, save_usage, usage_attempts
from yuxi.modules.workspace.usage_changes import acknowledge_workspace_change, pending_workspace_changes, workspace_usage_scan_lock
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import StateConflictError


def get_workspace_quota_bytes() -> int:
    """读取 API 与 worker 共用的正整数软配额。"""
    return get_int_env("WORKSPACE_QUOTA_BYTES", 10 * 1024**3)


async def workspace_usage(db: AsyncSession, uid: str) -> dict:
    """返回当前身份的用量、额度和最后一次观测时间。"""
    snapshot = await get_usage(db, uid)
    limit = get_workspace_quota_bytes()
    used = snapshot.used_bytes if snapshot else None
    return {
        "used_bytes": used,
        "measurement_failed": bool(snapshot and snapshot.last_attempt_at != snapshot.measured_at),
        "measured_at": snapshot.measured_at if snapshot else None,
        "limit_bytes": limit,
        "over_quota": _is_over_quota(used, limit),
    }


async def workspace_over_quota(db: AsyncSession, uid: str) -> bool:
    """按最近一次成功统计判断当前身份是否达到软配额。"""
    snapshot = await get_usage(db, uid)
    return _is_over_quota(snapshot.used_bytes if snapshot else None, get_workspace_quota_bytes())


async def require_upload_capacity(db: AsyncSession, uid: str) -> None:
    """上传前检查最近一次用量快照。"""
    if await workspace_over_quota(db, uid):
        raise StateConflictError("个人空间已满，请删除文件并等待用量更新后重试", code="workspace_quota_exceeded")


async def scan_workspace_usage() -> None:
    """扫描已标记用户，在成功落库后确认休眠环境对应的代次。"""
    with workspace_usage_scan_lock() as acquired:
        if acquired:
            await _scan_marked_workspaces()


async def _scan_marked_workspaces() -> None:
    """在巡检锁持有期间读取标记，每个用量快照单独提交。"""
    pending = await await_io(asyncio.to_thread(pending_workspace_changes))
    if not pending:
        return

    async with pg_manager.get_async_session_context() as db:
        attempts = await usage_attempts(db, [change.uid for change in pending])

    try:
        active = await asyncio.to_thread(get_sandbox_provider().active_workspace_uids, list(attempts)) if attempts else set()
    except Exception:
        logger.exception("无法确认沙盒活动状态，本轮保留所有用量标记")
        active = set(attempts)
    earliest = datetime.min.replace(tzinfo=UTC)
    pending.sort(key=lambda change: (attempts.get(change.uid) or earliest, change.uid))

    for change in pending:
        uid = change.uid
        if uid not in attempts:
            await asyncio.to_thread(acknowledge_workspace_change, change)
            continue

        measured_at = utc_now()
        try:
            used = await measure_allocated_bytes(uid)
        except (OSError, ValueError, subprocess.SubprocessError):
            logger.exception("个人空间扫描未完成，保留上一次观测 uid={}", uid)
            used = None
        try:
            async with pg_manager.get_async_session_context() as snapshot_db:
                await save_usage(snapshot_db, uid, used, measured_at)
        except SQLAlchemyError as exc:
            logger.warning("个人空间扫描落库失败 uid={} error={}", uid, type(exc).__name__)
            continue
        if used is not None and uid not in active:
            await asyncio.to_thread(acknowledge_workspace_change, change)


def _is_over_quota(used_bytes: int | None, limit_bytes: int) -> bool:
    """达到或超过软配额；尚未成功测量时视为未超。"""
    return used_bytes is not None and used_bytes >= limit_bytes
