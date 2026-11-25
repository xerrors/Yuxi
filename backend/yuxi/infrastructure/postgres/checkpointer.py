"""LangGraph PostgreSQL checkpoint pool 的 saver 与建表生命周期。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from yuxi.infrastructure.postgres.manager import PostgresManager

# Session 锁值与已部署进程保持兼容；必须在同一物理连接释放。
CHECKPOINTER_SETUP_LOCK_KEY = 94721802


async def get_latest_root_checkpoint_id(db: AsyncSession, *, thread_id: str) -> str | None:
    """按 saver 的版本顺序查最新根 checkpoint，只读取主键。"""
    return await db.scalar(
        text(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = :thread_id AND checkpoint_ns = '' ORDER BY checkpoint_id DESC LIMIT 1"
        ),
        {"thread_id": thread_id},
    )


def get_langgraph_checkpointer(manager: PostgresManager) -> AsyncPostgresSaver:
    """为本次构图创建独立 saver，共享有界连接池而不共享其实例锁。"""
    manager.require_initialized()
    if manager.langgraph_pool is None:
        raise RuntimeError("PostgreSQL LangGraph connection pool is not initialized.")
    return AsyncPostgresSaver(manager.langgraph_pool)


async def setup_langgraph_checkpointer(manager: PostgresManager) -> AsyncPostgresSaver:
    """跨进程串行创建 checkpoint 表，迁移实例不参与运行时构图。"""
    if manager.langgraph_checkpointer is None:
        manager.langgraph_checkpointer = get_langgraph_checkpointer(manager)
    checkpointer = manager.langgraph_checkpointer
    if not manager.langgraph_checkpointer_setup:
        async with manager.langgraph_pool.connection() as connection:
            await connection.execute(f"SELECT pg_advisory_lock({CHECKPOINTER_SETUP_LOCK_KEY})")
            try:
                await checkpointer.setup()
            finally:
                try:
                    cursor = await connection.execute(f"SELECT pg_advisory_unlock({CHECKPOINTER_SETUP_LOCK_KEY})")
                    row = await cursor.fetchone()
                    if not row or row[0] is not True:
                        raise RuntimeError("Failed to release LangGraph checkpoint advisory lock")
                except BaseException:
                    # Session 级锁不随事务回滚释放，解锁失败时必须销毁物理连接。
                    await connection.close()
                    raise
            manager.langgraph_checkpointer_setup = True
            logger.info("LangGraph checkpoint tables verified/created")
    return checkpointer
