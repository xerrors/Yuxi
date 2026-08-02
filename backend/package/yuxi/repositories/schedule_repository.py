from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import ScheduleDefinition, ScheduleLog
from yuxi.utils import logger


class ScheduleRepository:
    """定时任务及执行日志仓储层"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, schedule_id: str) -> ScheduleDefinition | None:
        """根据 ID 获取调度定义"""
        stmt = select(ScheduleDefinition).where(ScheduleDefinition.id == schedule_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_schedules(
        self, *, user_id: str | None = None, enabled: bool | None = None, limit: int = 100, offset: int = 0
    ) -> list[ScheduleDefinition]:
        """列表查询调度配置"""
        stmt = select(ScheduleDefinition)
        if user_id is not None:
            stmt = stmt.where(ScheduleDefinition.user_id == user_id)
        if enabled is not None:
            stmt = stmt.where(ScheduleDefinition.enabled == enabled)

        stmt = stmt.order_by(ScheduleDefinition.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create_schedule(self, schedule: ScheduleDefinition) -> ScheduleDefinition:
        """创建新的调度"""
        self.db.add(schedule)
        await self.db.flush()
        await self.db.refresh(schedule)
        return schedule

    async def update_schedule(self, schedule_id: str, data: dict[str, Any]) -> ScheduleDefinition | None:
        """更新调度定义"""
        schedule = await self.get_by_id(schedule_id)
        if not schedule:
            return None

        for key, value in data.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)

        schedule.updated_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(schedule)
        return schedule

    async def delete_schedule(self, schedule_id: str) -> bool:
        """删除调度"""
        stmt = delete(ScheduleDefinition).where(ScheduleDefinition.id == schedule_id)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return (result.rowcount or 0) > 0

    async def get_by_id_for_user(
        self, schedule_id: str, user_id: str, *, is_admin: bool = False
    ) -> ScheduleDefinition | None:
        """按 id 取 schedule，并按 user_id 过滤；admin 跳过 owner 过滤。

        HTTP 路由和 @tool 入口请用此方法；ARQ worker 仍用 get_by_id。
        """
        stmt = select(ScheduleDefinition).where(ScheduleDefinition.id == schedule_id)
        if not is_admin:
            stmt = stmt.where(ScheduleDefinition.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_for_user(
        self,
        schedule_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        is_admin: bool = False,
    ) -> ScheduleDefinition | None:
        """按 (id, user_id) 校验后更新；admin 跳过 owner 过滤。"""
        schedule = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not schedule:
            return None

        for key, value in data.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)
        schedule.updated_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(schedule)
        return schedule

    async def delete_for_user(self, schedule_id: str, user_id: str, *, is_admin: bool = False) -> bool:
        """按 (id, user_id) 校验后删除；admin 跳过 owner 过滤。"""
        schedule = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not schedule:
            return False
        await self.db.delete(schedule)
        await self.db.flush()
        return True

    async def list_logs_for_user(
        self,
        schedule_id: str,
        user_id: str,
        *,
        limit: int,
        offset: int,
        is_admin: bool = False,
    ) -> list[ScheduleLog]:
        """校验 schedule 归属后返回其执行日志；admin 跳过 owner 过滤。"""
        owner_ok = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not owner_ok:
            return []

        stmt = (
            select(ScheduleLog)
            .where(ScheduleLog.schedule_id == schedule_id)
            .order_by(ScheduleLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_due_schedules_with_lock(self, limit: int = 50) -> list[ScheduleDefinition]:
        """
        利用 Postgres 事务行级锁 FOR UPDATE SKIP LOCKED 锁定并获取到期且启用的调度任务列表。
        必须在外部主事务中执行以保证排他锁生效。
        """
        stmt = (
            select(ScheduleDefinition)
            .where(
                ScheduleDefinition.enabled.is_(True),
                ScheduleDefinition.next_run_at <= func.now(),
            )
            .order_by(ScheduleDefinition.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create_log(self, log: ScheduleLog) -> ScheduleLog:
        """写入调度触发日志"""
        self.db.add(log)
        await self.db.flush()
        return log

    async def get_logs_by_schedule_id(self, schedule_id: str, limit: int = 50, offset: int = 0) -> list[ScheduleLog]:
        """获取指定调度的历史执行日志列表"""
        stmt = (
            select(ScheduleLog)
            .where(ScheduleLog.schedule_id == schedule_id)
            .order_by(ScheduleLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_log_execution_status(self, run_id: str, execution_status: str) -> None:
        """当后台 Worker 的 run 执行状态改变时，根据 run_id 自动同步日志的 execution_status"""
        try:
            stmt = update(ScheduleLog).where(ScheduleLog.run_id == run_id).values(execution_status=execution_status)
            await self.db.execute(stmt)
            await self.db.flush()
        except Exception as e:
            logger.error(f"Failed to update schedule log status for run_id {run_id}: {e}")
