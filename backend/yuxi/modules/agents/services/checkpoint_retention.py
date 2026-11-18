"""LangGraph checkpoint 线程历史保留：Run 终态后把该线程裁剪到最近若干版本。

恢复与审批只读取每个线程命名空间的最新 checkpoint，历史版本没有消费者；每步保留会让
checkpoints/checkpoint_blobs/checkpoint_writes 随轮次无界增长。本模块在终态路径做增量裁剪，
worker 每日兜底清扫超 K 线程与孤儿线程，存量与异常残留由 scripts/checkpoint-cleanup.sh
兜底。软删会话的整删分支不在本模块：当前基线没有把会话写成 'deleted' 的入口，删除与数据保留
语义确定后再补（PR #1173 维护者意见），在此之前删除残留由 K 上限封顶。
"""

from __future__ import annotations

from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager

THREAD_CHECKPOINT_KEEP_LATEST = 5

# 裁剪结果三态：活跃跳过与真删必须分开计，否则观测会把"什么都没删"报成治理生效。
PURGE_TRIMMED = "trimmed"
PURGE_SKIPPED_ACTIVE = "skipped_active"
PURGE_FAILED = "failed"

# 在线裁剪跑在 Run 终态之后，必须有界：锁等待 2s、语句 10s，超时回滚只记日志，
# 残留交给每日清扫与运维脚本，不让优化拖住 worker。
PURGE_LOCK_TIMEOUT = "2s"
PURGE_STATEMENT_TIMEOUT = "10s"

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

# 未终态 Run 的线程整体跳过：裁剪与在途执行竞争可能删掉尚未收敛的 writes。
# 探测与三条 DELETE 不在同一原子操作内，这是可接受的：检查后新提交的 Run，其 checkpoint 只会
# 落入每条语句各自重算的 keep（最新版本必然入选），因此不需要额外加锁。删除顺序是
# writes → blobs → checkpoints，后算的 keep 只会比先算的小，保留集永不被提前删；代价是新版本
# 恰在语句之间提交时，被挤出版本的历史 writes 会多留一轮——无人读取，下一次裁剪按
# "checkpoint_id 不在 keep 内"即回收，不为此加锁或补第四条语句。
_ACTIVE_RUN_SQL = text("SELECT 1 FROM agent_runs WHERE thread_id = :t AND status IN ('pending','running','cancel_requested') LIMIT 1")


async def purge_thread_checkpoint_history(thread_id: str, *, keep: int = THREAD_CHECKPOINT_KEEP_LATEST) -> str:
    """裁剪单个线程超出 keep 的历史版本及其独占 blob/writes，返回 trimmed/skipped_active/failed。

    keep 必须为正整数：置 0 或负数会把非活跃线程的历史全部清空，属数据销毁而非治理。
    线程本就 ≤keep 或没有 checkpoint 同样返回 PURGE_TRIMMED：计数口径是"该线程已按保留策略处理"，
    不是"删掉了行数"。
    每条 DELETE 各自重算 keep，所以清理期间新写入的 checkpoint 只会落入"最新版必然入选"的保留集，
    不存在"按旧快照删新版本"的窗口；这也是运维脚本 retain 的分批形态必须与本函数同形的原因。
    失败只告警返回 PURGE_FAILED——checkpoint 裁剪是优化而非业务事实，残留由每日清扫与脚本兜底。
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
            params = {"thread_id": thread_id, "keep": keep}
            await db.execute(text(_DROP_WRITES), params)
            await db.execute(text(_DROP_BLOBS), params)
            await db.execute(text(_DROP_CHECKPOINTS), params)
            await db.commit()
        return PURGE_TRIMMED
    except Exception:
        logger.opt(exception=True).warning(f"checkpoint history purge failed: thread={thread_id}")
        return PURGE_FAILED


_SWEEP_BATCH_LIMIT = 500
_ORPHAN_GRACE_DAYS = 7


async def sweep_checkpoint_garbage() -> dict:
    """每日兜底清扫：超 K 版本线程与无主孤儿线程（宽限期内不动）。

    终态裁剪失效或被绕过时的自愈路径；单轮各限 500 线程，跑不完留给下一轮。计数区分
    `trimmed_threads`（真删）、`skipped_active_threads`（因在途执行跳过）与 `orphan_threads`，
    失败既不计数也不中断其余桶。
    """
    stats = {"trimmed_threads": 0, "skipped_active_threads": 0, "orphan_threads": 0}
    async with pg_manager.get_async_session_context() as db:
        over_k = (
            (
                await db.execute(
                    text(
                        # 选择口径与裁剪口径同维：逐 (thread_id, checkpoint_ns) 判断是否超 K，
                        # 否则「各命名空间都没超 K 而总和超 K」的线程会被计入处理量却零删除。
                        "select distinct t.thread_id from ("
                        "  select thread_id, checkpoint_ns from checkpoints"
                        "  group by thread_id, checkpoint_ns having count(*) > :keep"
                        ") t join sessions s on s.thread_id = t.thread_id"
                        " where not exists (select 1 from agent_runs r where r.thread_id = t.thread_id"
                        "   and r.status in ('pending','running','cancel_requested')) limit :n"
                    ),
                    {"keep": THREAD_CHECKPOINT_KEEP_LATEST, "n": _SWEEP_BATCH_LIMIT},
                )
            )
            .scalars()
            .all()
        )
    for thread_id in over_k:
        outcome = await purge_thread_checkpoint_history(thread_id)
        if outcome == PURGE_TRIMMED:
            stats["trimmed_threads"] += 1
        elif outcome == PURGE_SKIPPED_ACTIVE:
            stats["skipped_active_threads"] += 1

    async with pg_manager.get_async_session_context() as db:
        orphans = (
            (
                await db.execute(
                    text(
                        "select k.thread_id from checkpoints k left join sessions s on s.thread_id = k.thread_id"
                        " where s.id is null group by k.thread_id"
                        " having max((k.checkpoint ->> 'ts')::timestamptz) < now() - make_interval(days => :grace)"
                        " limit :n"
                    ),
                    {"grace": _ORPHAN_GRACE_DAYS, "n": _SWEEP_BATCH_LIMIT},
                )
            )
            .scalars()
            .all()
        )
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
