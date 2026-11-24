"""用户设置的环境状态与重建用例。"""

import asyncio

import httpx

from yuxi.infrastructure.filesystem import await_io
from yuxi.modules.agents.runtime.sandbox import get_sandbox_provider
from yuxi.shared.errors import StateConflictError, UpstreamError


async def get_vm_stats() -> dict[str, int]:
    """读取当前后端的运行实例计数，控制面失败不伪装为空节点。"""
    try:
        return await asyncio.to_thread(get_sandbox_provider().vm_stats)
    except (httpx.HTTPError, ValueError, UpstreamError) as exc:
        raise UpstreamError("VM 统计暂不可用，请重试") from exc


async def get_user_sandbox(uid: str) -> dict:
    """读取控制状态，不唤醒环境。"""
    return await asyncio.to_thread(get_sandbox_provider().status, uid)


async def rebuild_user_sandbox(uid: str, *, failed_generation: int | None = None) -> dict:
    """取消请求时也等待持有生命周期锁的外部操作完成。"""
    provider = get_sandbox_provider()
    if failed_generation is None:
        return await await_io(asyncio.to_thread(provider.rebuild, uid))
    if provider.backend != "cube":
        raise StateConflictError("当前运行环境不是 Cube", code="sandbox_changed")
    return await await_io(asyncio.to_thread(provider.rebuild, uid, failed_generation=failed_generation))


async def get_user_sandbox_disk_usage(uid: str) -> dict:
    """显式查询磁盘，等待普通用户命令及其清理完成。"""
    return await await_io(asyncio.to_thread(get_sandbox_provider().disk_usage, uid))
