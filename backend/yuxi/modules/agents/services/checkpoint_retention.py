"""Run 终态后裁剪 checkpoint 历史；每日清扫超量线程和宽限期外的孤儿线程。

恢复与审批只消费最新状态。保留版本引用的 blob 必须留下，存量维护见 scripts/checkpoint-cleanup.sh。
"""

from __future__ import annotations

from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager

THREAD_CHECKPOINT_KEEP_LATEST = 5

# 区分已处理、活跃跳过与失败，避免把跳过统计成已处理。
PURGE_TRIMMED = "trimmed"
PURGE_SKIPPED_ACTIVE = "skipped_active"
PURGE_FAILED = "failed"

# 限制终态后处理的锁等待及单条 SQL 耗时，超时回滚后由后续清理兜底。
PURGE_LOCK_TIMEOUT = "2s"
PURGE_STATEMENT_TIMEOUT = "10s"


async def purge_thread_checkpoint_history(thread_id: str, *, keep: int = THREAD_CHECKPOINT_KEEP_LATEST) -> str:
    """按命名空间保留最近 keep 版，返回 trimmed/skipped_active/failed。

    keep 必须为正数。trimmed 表示已按策略处理（包括本就无超量历史），不代表删除行数。
    数据库失败只告警并返回 failed，不影响已提交的 Run 终态。
    """
    if keep < 1:
        raise ValueError(f"checkpoint 保留数量必须是大于 0 的整数，收到 keep={keep}")
    try:
        async with pg_manager.get_async_session_context() as db:
            active = await db.execute(_ACTIVE_RUN_SQL, {"t": thread_id})
            if active.first() is not None:
                return PURGE_SKIPPED_ACTIVE
            await db.execute(text(f"SET LOCAL lock_timeout = '{PURGE_LOCK_TIMEOUT}'"))
            await db.execute(text(f"SET LOCAL statement_timeout = '{PURGE_STATEMENT_TIMEOUT}'"))

            # 每条 DELETE 重算保留集；先删引用，再删 checkpoint。
            # 并发新增版本挤出的旧 writes 可能多留一轮，后续裁剪回收。
            params = {"thread_id": thread_id, "keep": keep}
            await db.execute(text(_DROP_WRITES), params)
            await db.execute(text(_DROP_BLOBS), params)
            await db.execute(text(_DROP_CHECKPOINTS), params)
            await db.commit()
        return PURGE_TRIMMED
    except Exception:
        logger.opt(exception=True).warning(f"checkpoint history purge failed: thread={thread_id}")
        return PURGE_FAILED


async def sweep_checkpoint_garbage() -> dict:
    """每日各处理至多 500 个超量线程和过期孤儿，活跃跳过单独计数。"""
    stats = {"trimmed_threads": 0, "skipped_active_threads": 0, "orphan_threads": 0}
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            text(
                # 选择口径与裁剪口径同维：逐 (thread_id, checkpoint_ns) 判断是否超 K，
                # 否则「各命名空间都没超 K 而总和超 K」的线程会被计入处理量却零删除。
                "select distinct t.thread_id from ("
                "  select thread_id, checkpoint_ns from checkpoints"
                "  group by thread_id, checkpoint_ns having count(*) > :keep"
                ") t join sessions s on s.thread_id = t.thread_id"
                " where not exists (select 1 from agent_runs r where r.thread_id = t.thread_id"
                f"   and r.status in {_NON_TERMINAL_RUN_STATUSES}) limit :n"
            ),
            {"keep": THREAD_CHECKPOINT_KEEP_LATEST, "n": _SWEEP_BATCH_LIMIT},
        )
        over_k = result.scalars().all()
    for thread_id in over_k:
        outcome = await purge_thread_checkpoint_history(thread_id)
        if outcome == PURGE_TRIMMED:
            stats["trimmed_threads"] += 1
        elif outcome == PURGE_SKIPPED_ACTIVE:
            stats["skipped_active_threads"] += 1

    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            text(
                "select k.thread_id from checkpoints k left join sessions s on s.thread_id = k.thread_id"
                " where s.id is null group by k.thread_id"
                " having max((k.checkpoint ->> 'ts')::timestamptz) < now() - make_interval(days => :grace)"
                " limit :n"
            ),
            {"grace": _ORPHAN_GRACE_DAYS, "n": _SWEEP_BATCH_LIMIT},
        )
        orphans = result.scalars().all()
    # saver 在首个业务会话（会惰性完成 manager 初始化）之后再取。
    saver = get_langgraph_checkpointer(pg_manager)
    for thread_id in orphans:
        try:
            await saver.adelete_thread(thread_id)
            stats["orphan_threads"] += 1
        except Exception:
            logger.opt(exception=True).warning(f"checkpoint sweep failed for orphan thread={thread_id}")
    if any(stats.values()):
        logger.info(f"checkpoint sweep: {stats}")
    return stats


_SWEEP_BATCH_LIMIT = 500
_ORPHAN_GRACE_DAYS = 7

_KEEP_CTE = """
WITH per_ns AS (
  SELECT checkpoint_ns, checkpoint_id,
         row_number() OVER (PARTITION BY checkpoint_ns ORDER BY checkpoint_id DESC) AS rn
  FROM checkpoints WHERE thread_id = :thread_id
),
keep AS (SELECT checkpoint_ns, checkpoint_id FROM per_ns WHERE rn <= :keep)
"""

_DROP_WRITES = (
    _KEEP_CTE
    + """
    DELETE FROM checkpoint_writes w
    WHERE w.thread_id = :thread_id
      AND NOT EXISTS (
        SELECT 1 FROM keep k
        WHERE k.checkpoint_ns = w.checkpoint_ns
          AND k.checkpoint_id = w.checkpoint_id)"""
)

_DROP_BLOBS = (
    _KEEP_CTE
    + """
    DELETE FROM checkpoint_blobs b
    WHERE b.thread_id = :thread_id
      AND NOT EXISTS (
        SELECT 1 FROM keep k
        JOIN checkpoints c
          ON c.thread_id = :thread_id
         AND c.checkpoint_ns = k.checkpoint_ns
         AND c.checkpoint_id = k.checkpoint_id
        WHERE k.checkpoint_ns = b.checkpoint_ns
          AND (c.checkpoint -> 'channel_versions' ->> b.channel) = b.version)"""
)

_DROP_CHECKPOINTS = (
    _KEEP_CTE
    + """
    DELETE FROM checkpoints c
    WHERE c.thread_id = :thread_id
      AND NOT EXISTS (
        SELECT 1 FROM keep k
        WHERE k.checkpoint_ns = c.checkpoint_ns
          AND k.checkpoint_id = c.checkpoint_id)"""
)

# 与 Run 终态集合互补；清扫筛选和运维脚本须保持同一口径。
_NON_TERMINAL_RUN_STATUSES = "('pending','running','cancel_requested')"
_ACTIVE_RUN_SQL = text(f"SELECT 1 FROM agent_runs WHERE thread_id = :t AND status IN {_NON_TERMINAL_RUN_STATUSES} LIMIT 1")
