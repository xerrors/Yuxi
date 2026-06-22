"""Agent run repository."""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRun
from yuxi.utils.datetime_utils import utc_now_naive

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


class AgentRunRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one_or_none()

    async def get_run_by_request_id(self, request_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_resume_run(self, parent_run_id: str, resume_request_id: str) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun).where(
                AgentRun.parent_run_id == parent_run_id,
                AgentRun.resume_request_id == resume_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid))))
        return result.scalar_one_or_none()

    async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type == "subagent",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_child_runs_for_user(self, parent_agent_run_id: str, uid: str) -> list[AgentRun]:
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.parent_agent_run_id == parent_agent_run_id,
                AgentRun.uid == str(uid),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def list_active_child_runs_for_user(self, parent_agent_run_id: str, uid: str) -> list[AgentRun]:
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.parent_agent_run_id == parent_agent_run_id,
                AgentRun.uid == str(uid),
                AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def create_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        agent_id: str,
        uid: str,
        request_id: str,
        input_payload: dict,
        conversation_id: int | None = None,
        parent_run_id: str | None = None,
        parent_agent_run_id: str | None = None,
        run_type: str = "chat",
        resume_request_id: str | None = None,
        checkpoint_thread_id: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            uid=str(uid),
            request_id=request_id,
            conversation_id=conversation_id,
            parent_run_id=parent_run_id,
            parent_agent_run_id=parent_agent_run_id,
            run_type=run_type,
            resume_request_id=resume_request_id,
            checkpoint_thread_id=checkpoint_thread_id or thread_id,
            input_payload=input_payload or {},
            status="pending",
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def set_input_message(self, run_id: str, message_id: int) -> AgentRun | None:
        run = await self.get_run(run_id)
        if not run:
            return None
        run.input_message_id = message_id
        run.updated_at = utc_now_naive()
        await self.db.flush()
        return run

    async def set_output_message(self, run_id: str, message_id: int) -> AgentRun | None:
        run = await self.get_run(run_id)
        if not run:
            return None
        run.output_message_id = message_id
        run.updated_at = utc_now_naive()
        await self.db.flush()
        return run

    async def mark_running(self, run_id: str) -> AgentRun | None:
        run = await self._lock_run(run_id)
        if not run:
            return None
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        now = utc_now_naive()
        run.status = "running"
        run.started_at = run.started_at or now
        run.updated_at = now
        await self.db.flush()
        return run

    async def request_cancel(self, run_id: str) -> AgentRun | None:
        run = await self._lock_run(run_id)
        if not run:
            return None
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        run.status = "cancel_requested"
        run.updated_at = utc_now_naive()
        await self.db.flush()
        return run

    async def set_terminal_status(
        self,
        run_id: str,
        *,
        status: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> AgentRun | None:
        run = await self._lock_run(run_id)
        if not run:
            return None
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        run.status = status
        run.error_type = error_type
        run.error_message = error_message
        run.finished_at = utc_now_naive()
        run.updated_at = run.finished_at
        await self.db.flush()
        return run

    async def _lock_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        return result.scalar_one_or_none()
