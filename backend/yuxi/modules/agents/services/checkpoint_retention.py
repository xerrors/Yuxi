"""LangGraph checkpoint 线程历史保留：Run 终态后把该线程裁剪到最近若干版本。

恢复与审批只读取每个线程命名空间的最新 checkpoint，历史版本没有消费者；每步保留会让
checkpoints/checkpoint_blobs/checkpoint_writes 随轮次无界增长。本模块在终态路径做增量裁剪，
worker 每日兜底清扫软删线程、超 K 线程与孤儿线程，存量与异常残留由 scripts/checkpoint-cleanup.sh
兜底。当前基线没有写 sessions.status='deleted' 的流程，读侧却已把 'deleted' 当作不可见态，
因此 deleted 分支作为待接线的契约保留：删除写主落地即生效，此前的残留由 K 上限封顶。
"""

from __future__ import annotations

from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager

THREAD_CHECKPOINT_KEEP_LATEST = 5

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
# 落入每条语句各自重算的 keep（最新版本必然入选），因此不需要额外加锁。
_ACTIVE_RUN_SQL = text("SELECT 1 FROM agent_runs WHERE thread_id = :t AND status IN ('pending','running','cancel_requested') LIMIT 1")


async def purge_thread_checkpoint_history(thread_id: str, *, keep: int = THREAD_CHECKPOINT_KEEP_LATEST) -> bool:
    """裁剪单个线程超出 keep 的历史版本及其独占 blob/writes；有未终态 Run 的线程跳过。

    返回是否完成处理（跳过记为完成）；失败只告警返回 False——checkpoint 裁剪是优化而非
    业务事实，残留由清理脚本与每日清扫兜底，但清扫计数不得把失败计成成功。
    """
    if not thread_id:
        return
    try:
        async with pg_manager.get_async_session_context() as db:
            active = await db.execute(_ACTIVE_RUN_SQL, {"t": thread_id})
            if active.first() is not None:
                return True
            params = {"thread_id": thread_id, "keep": keep}
            await db.execute(text(_DROP_WRITES), params)
            await db.execute(text(_DROP_BLOBS), params)
            await db.execute(text(_DROP_CHECKPOINTS), params)
            await db.commit()
        return True
    except Exception:
        logger.opt(exception=True).warning(f"checkpoint history purge failed: thread={thread_id}")
        return False


_SWEEP_BATCH_LIMIT = 500
_ORPHAN_GRACE_DAYS = 7


async def sweep_checkpoint_garbage() -> dict:
    """每日兜底清扫：deleted 会话线程、超 K 版本线程、无主孤儿线程（宽限期内不动）。

    前两道防线（终态裁剪、未来的删除级联）失效或被绕过时的自愈路径；单轮各限 500 线程，
    跑不完留给下一轮。返回计数只含成功处理数，供 worker 日志与运行时观测核对。
    """
    stats = {"deleted_threads": 0, "trimmed_threads": 0, "orphan_threads": 0}
    async with pg_manager.get_async_session_context() as db:
        deleted = (
            (
                await db.execute(
                    text(
                        "select distinct s.thread_id from sessions s join checkpoints k on k.thread_id = s.thread_id"
                        " where s.status = 'deleted'"
                        " and not exists (select 1 from agent_runs r where r.thread_id = s.thread_id"
                        "   and r.status in ('pending','running','cancel_requested')) limit :n"
                    ),
                    {"n": _SWEEP_BATCH_LIMIT},
                )
            )
            .scalars()
            .all()
        )
    # saver 在首个业务会话（会惰性完成 manager 初始化）之后再取。
    saver = get_langgraph_checkpointer(pg_manager)
    for thread_id in deleted:
        try:
            await saver.adelete_thread(thread_id)
            stats["deleted_threads"] += 1
        except Exception:
            logger.opt(exception=True).warning(f"checkpoint sweep failed for deleted thread={thread_id}")

    async with pg_manager.get_async_session_context() as db:
        over_k = (
            (
                await db.execute(
                    text(
                        # 选择口径与裁剪口径同维：逐 (thread_id, checkpoint_ns) 判断是否超 K，
                        # 否则「各命名空间都没超 K 而总和超 K」的线程会被计入 trimmed_threads 却零删除。
                        "select distinct t.thread_id from ("
                        "  select thread_id, checkpoint_ns from checkpoints"
                        "  group by thread_id, checkpoint_ns having count(*) > :keep"
                        ") t join sessions s on s.thread_id = t.thread_id and s.status <> 'deleted'"
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
        if await purge_thread_checkpoint_history(thread_id):
            stats["trimmed_threads"] += 1

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
    for thread_id in orphans:
        try:
            await saver.adelete_thread(thread_id)
            stats["orphan_threads"] += 1
        except Exception:
            logger.opt(exception=True).warning(f"checkpoint sweep failed for orphan thread={thread_id}")
    if any(stats.values()):
        logger.info(f"checkpoint sweep: {stats}")
    return stats
