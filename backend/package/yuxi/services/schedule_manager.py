import traceback
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.services.run_queue_service import get_redis_client
from yuxi.services.schedule_service import ScheduleService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ScheduleLog
from yuxi.utils import logger


def compute_next_run(cron_expr: str, timezone_str: str) -> datetime:
    """以当前时间为基准，计算带时区感知的下一次有效触发时间点，并转化为 UTC 时区的 datetime"""
    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")

    now_tz = datetime.now(tz)
    iter_cron = croniter(cron_expr, now_tz)
    next_dt = iter_cron.get_next(datetime)
    return next_dt.astimezone(UTC)


class ScheduleManager:
    """定时任务管理器，负责到期任务的排他锁定、状态推进和日志滚动"""

    async def poll_due_schedules(self, main_db: AsyncSession) -> int:
        """
        在主事务(T1)中锁定符合条件的到期 schedule 行记录。
        对每个 schedule 行，采用独立的 Session 会话连接(T2)进行创建 run 操作以隔离事务。
        主事务只用于控制行锁生命周期并在最后统一 commit 释放。
        """
        repo = ScheduleRepository(main_db)
        # 1. 批量加锁查取 (SKIP LOCKED)
        due_schedules = await repo.get_due_schedules_with_lock(limit=50)
        if not due_schedules:
            return 0

        triggered_count = 0
        now_time = datetime.now(UTC)
        service = ScheduleService()

        for schedule in due_schedules:
            thread_id, run_id = None, None
            try:
                # 2. 为每个定时触发单独申请一个子 Session 会话连接 (T2) 以进行 Conversation/Run 写入，避免 T1 锁泄漏
                async with pg_manager.get_async_session_context() as run_db:
                    thread_id, run_id = await service.create_scheduled_run(schedule=schedule, db=run_db)

                # 3. 触发成功：计算下一次触发时间并推进主数据库字段
                schedule.last_run_at = now_time
                schedule.next_run_at = compute_next_run(schedule.cron_expr, schedule.timezone)
                schedule.run_count += 1
                schedule.failed_count = 0  # 重置重试次数

                # 主 session 写入 triggered 的成功日志
                trigger_delay = 0
                if schedule.next_run_at:
                    trigger_delay = int((now_time - schedule.next_run_at).total_seconds() * 1000)
                    if trigger_delay < 0:
                        trigger_delay = 0

                log = ScheduleLog(
                    id=str(uuid.uuid4()),
                    schedule_id=schedule.id,
                    run_id=run_id,
                    thread_id=thread_id,
                    status="triggered",
                    execution_status="pending",
                    trigger_delay_ms=trigger_delay,
                    created_at=now_time,
                )
                main_db.add(log)
                triggered_count += 1

            except Exception as e:
                logger.error(f"ScheduleManager: trigger failed for schedule {schedule.id}: {e}")
                schedule.failed_count += 1

                # 如果连续重试失败达到 3 次，强制强行越过该周期并推进到下一周期，防止永久死循环
                if schedule.failed_count >= 3:
                    logger.warning(
                        f"ScheduleManager: schedule {schedule.id} failed continuously "
                        "for 3 times, shifting to next period."
                    )
                    schedule.next_run_at = compute_next_run(schedule.cron_expr, schedule.timezone)
                    schedule.failed_count = 0

                # 主 session 写入 trigger_failed 的失败日志
                log = ScheduleLog(
                    id=str(uuid.uuid4()),
                    schedule_id=schedule.id,
                    status="trigger_failed",
                    error_message=str(e),
                    created_at=now_time,
                )
                main_db.add(log)

        # 4. 主事务统一提交，释放在主 session 周期持有的行锁
        await main_db.commit()
        return triggered_count

    async def daily_cleanup_schedule_logs(self, db: AsyncSession) -> int:
        """
        每日凌晨执行，清理超过 30 天或单个 schedule 大于 1000 条的日志。
        """
        try:
            # 1. 删除 30 天以前的日志
            thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
            stmt1 = delete(ScheduleLog).where(ScheduleLog.created_at < thirty_days_ago)
            res1 = await db.execute(stmt1)
            deleted_count = res1.rowcount or 0

            # 2. 针对每个 schedule，保留最近 1000 条，其余多出的执行物理删除
            # 首先查出所有的 schedule_id
            repo = ScheduleRepository(db)
            schedules = await repo.list_schedules(limit=10000)
            for sched in schedules:
                # 查出这个 schedule_id 最近的 1000 个日志的最小创建时间
                sub_stmt = delete(ScheduleLog).where(
                    ScheduleLog.schedule_id == sched.id,
                    ScheduleLog.id.not_in(
                        select(ScheduleLog.id)
                        .where(ScheduleLog.schedule_id == sched.id)
                        .order_by(ScheduleLog.created_at.desc())
                        .limit(1000)
                    ),
                )
                res2 = await db.execute(sub_stmt)
                deleted_count += res2.rowcount or 0

            await db.commit()
            logger.info(f"Daily logs cleanup finished, deleted {deleted_count} logs.")
            return deleted_count
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to perform daily log cleanup: {e}, {traceback.format_exc()}")
            return 0


# ========== ARQ Workers 对应的轮询与清理任务封装 ==========


async def schedule_poll_job(ctx):
    """
    ARQ Cron Job — 周期性调度轮询任务（每 30 秒执行一次）
    """
    redis = await get_redis_client()
    # redis-py 自带 Lock，timeout为28秒，阻止同一时间有第二个 Worker 争夺执行
    lock = redis.lock("schedule:poll:lock", timeout=28, blocking_timeout=0)

    try:
        acquired = await lock.acquire()
        if not acquired:
            logger.debug("schedule_poll_job: Lock occupied, skipping this tick.")
            return
    except Exception as e:
        logger.warning(f"schedule_poll_job: failed to acquire Redis lock: {e}")
        return

    try:
        async with pg_manager.get_async_session_context() as db:
            manager = ScheduleManager()
            triggered_count = await manager.poll_due_schedules(db)
            if triggered_count > 0:
                logger.info(f"schedule_poll_job: Triggered {triggered_count} schedules successfully.")
    except Exception as e:
        logger.error(f"schedule_poll_job: unhandled exception: {e}, {traceback.format_exc()}")
    finally:
        try:
            await lock.release()
        except Exception:
            # 可能是已被自动释放，吞掉
            pass


async def daily_cleanup_schedule_logs_job(ctx):
    """
    ARQ Cron Job — 凌晨日志清理任务
    """
    logger.info("daily_cleanup_schedule_logs_job: Starting cleanup task.")
    try:
        async with pg_manager.get_async_session_context() as db:
            manager = ScheduleManager()
            await manager.daily_cleanup_schedule_logs(db)
    except Exception as e:
        logger.error(f"daily_cleanup_schedule_logs_job failed: {e}")
