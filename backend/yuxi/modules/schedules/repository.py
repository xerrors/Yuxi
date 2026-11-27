"""用户 Agent 定时任务的 PostgreSQL 访问边界。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.identity.models import User
from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun
from yuxi.shared.datetime import utc_now


class ScheduledAgentRepository:
    """读写用户拥有的定时任务和触发记录。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_jobs(self, uid: str) -> list[ScheduledAgentJob]:
        result = await self.db.execute(
            select(ScheduledAgentJob)
            .where(ScheduledAgentJob.uid == str(uid), ScheduledAgentJob.deleted_at.is_(None))
            .order_by(ScheduledAgentJob.created_at.desc(), ScheduledAgentJob.id.desc())
        )
        return list(result.scalars().all())

    async def get_job(
        self,
        job_id: str,
        uid: str,
        *,
        lock: bool = False,
    ) -> ScheduledAgentJob | None:
        stmt = select(ScheduledAgentJob).where(
            ScheduledAgentJob.id == job_id,
            ScheduledAgentJob.uid == str(uid),
            ScheduledAgentJob.deleted_at.is_(None),
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.db.scalar(stmt)

    async def get_job_by_creation_request(
        self,
        uid: str,
        request_id: str,
    ) -> ScheduledAgentJob | None:
        """按用户作用域读取幂等创建结果，包括已软删除任务。"""
        return await self.db.scalar(
            select(ScheduledAgentJob).where(
                ScheduledAgentJob.uid == str(uid),
                ScheduledAgentJob.creation_request_id == request_id,
            )
        )

    async def add_job(self, job: ScheduledAgentJob) -> ScheduledAgentJob:
        self.db.add(job)
        await self.db.flush()
        return job

    async def list_recent_runs(
        self,
        job_ids: list[str],
        uid: str,
        limit_per_job: int,
    ) -> list[tuple[ScheduledAgentRun, AgentInput | None, AgentRun | None]]:
        """批量读取最近触发记录及其 Input 和当前顶层 Run。"""
        if not job_ids:
            return []
        ranked_runs = (
            select(
                ScheduledAgentRun.id.label("scheduled_run_id"),
                func.row_number()
                .over(
                    partition_by=ScheduledAgentRun.job_id,
                    order_by=(ScheduledAgentRun.scheduled_for.desc(), ScheduledAgentRun.id.desc()),
                )
                .label("position"),
            )
            .where(ScheduledAgentRun.job_id.in_(job_ids))
            .subquery()
        )
        result = await self.db.execute(
            select(ScheduledAgentRun, AgentInput, AgentRun)
            .join(ranked_runs, ranked_runs.c.scheduled_run_id == ScheduledAgentRun.id)
            .join(ScheduledAgentJob, ScheduledAgentJob.id == ScheduledAgentRun.job_id)
            .outerjoin(AgentInput, AgentInput.id == ScheduledAgentRun.input_id)
            .outerjoin(AgentTurn, AgentTurn.id == AgentInput.turn_id)
            .outerjoin(AgentRun, AgentRun.id == AgentTurn.current_run_id)
            .where(
                ScheduledAgentRun.job_id.in_(job_ids),
                ScheduledAgentJob.uid == str(uid),
                ranked_runs.c.position <= limit_per_job,
            )
            .order_by(
                ScheduledAgentRun.job_id,
                ScheduledAgentRun.scheduled_for.desc(),
                ScheduledAgentRun.id.desc(),
            )
        )
        return list(result.all())

    async def list_run_history(
        self, job_id: str, uid: str, *, limit: int, offset: int
    ) -> tuple[list[tuple[ScheduledAgentRun, AgentInput | None, AgentRun | None]], int]:
        """在任务归属边界内分页读取全部触发记录和当前执行事实。"""
        conditions = (
            ScheduledAgentRun.job_id == job_id,
            ScheduledAgentJob.uid == str(uid),
            ScheduledAgentJob.deleted_at.is_(None),
        )
        total = await self.db.scalar(
            select(func.count(ScheduledAgentRun.id))
            .join(ScheduledAgentJob, ScheduledAgentJob.id == ScheduledAgentRun.job_id)
            .where(*conditions)
        )
        result = await self.db.execute(
            select(ScheduledAgentRun, AgentInput, AgentRun)
            .join(ScheduledAgentJob, ScheduledAgentJob.id == ScheduledAgentRun.job_id)
            .outerjoin(AgentInput, AgentInput.id == ScheduledAgentRun.input_id)
            .outerjoin(AgentTurn, AgentTurn.id == AgentInput.turn_id)
            .outerjoin(AgentRun, AgentRun.id == AgentTurn.current_run_id)
            .where(*conditions)
            .order_by(ScheduledAgentRun.scheduled_for.desc(), ScheduledAgentRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.all()), int(total or 0)

    async def get_input_and_run(self, input_id: str) -> tuple[AgentInput | None, AgentRun | None]:
        """读取定时输入及其当前顶层执行段。"""
        row = (
            await self.db.execute(
                select(AgentInput, AgentRun)
                .outerjoin(AgentTurn, AgentTurn.id == AgentInput.turn_id)
                .outerjoin(AgentRun, AgentRun.id == AgentTurn.current_run_id)
                .where(AgentInput.id == input_id)
            )
        ).one_or_none()
        return row if row else (None, None)

    async def claim_due_job(self, *, now: datetime) -> ScheduledAgentJob | None:
        """锁定活动用户的一个到期任务；触发事实由 service 在同一事务内创建。"""
        return await self.db.scalar(
            select(ScheduledAgentJob)
            .join(User, User.uid == ScheduledAgentJob.uid)
            .where(
                User.is_deleted == 0,
                ScheduledAgentJob.enabled.is_(True),
                ScheduledAgentJob.deleted_at.is_(None),
                ScheduledAgentJob.next_run_at <= now,
            )
            .order_by(ScheduledAgentJob.next_run_at.asc(), ScheduledAgentJob.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def has_active_run(self, job_id: str) -> bool:
        """按 Input/Turn 事实判断任务是否已有未结束工作。"""
        run_id = await self.db.scalar(
            select(ScheduledAgentRun.id)
            .outerjoin(AgentInput, AgentInput.id == ScheduledAgentRun.input_id)
            .outerjoin(AgentTurn, AgentTurn.id == AgentInput.turn_id)
            .where(
                ScheduledAgentRun.job_id == job_id,
                or_(
                    ScheduledAgentRun.status == "dispatching",
                    and_(
                        ScheduledAgentRun.status == "submitted",
                        or_(
                            AgentInput.id.is_(None),
                            AgentInput.status == "pending",
                            and_(
                                AgentInput.status == "consumed",
                                or_(
                                    AgentTurn.id.is_(None),
                                    AgentTurn.status.in_(("running", "waiting", "cancelling")),
                                ),
                            ),
                        ),
                    ),
                ),
            )
            .limit(1)
        )
        return run_id is not None

    async def add_run(self, run: ScheduledAgentRun) -> ScheduledAgentRun:
        """新增执行记录并 flush。"""
        self.db.add(run)
        await self.db.flush()
        return run

    async def get_run(self, run_id: str) -> ScheduledAgentRun | None:
        """按稳定 ID 读取一次触发意图。"""
        return await self.db.get(ScheduledAgentRun, run_id)

    async def list_dispatching_runs(self, *, before: datetime, limit: int = 100) -> list[ScheduledAgentRun]:
        result = await self.db.execute(
            select(ScheduledAgentRun)
            .where(
                ScheduledAgentRun.status == "dispatching",
                ScheduledAgentRun.created_at <= before,
            )
            .order_by(ScheduledAgentRun.created_at.asc(), ScheduledAgentRun.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_job(self, job: ScheduledAgentJob) -> None:
        """软删除任务，保留执行记录。"""
        job.enabled = False
        job.deleted_at = utc_now()
        job.updated_at = job.deleted_at
        await self.db.flush()


async def delete_user_schedules(db: AsyncSession, *, uid: str) -> None:
    """账号删除事务移除其任务定义，历史由外键级联清理。"""
    await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.uid == uid))
