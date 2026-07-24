"""Agent run repository."""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AGENT_RUN_TERMINAL_STATUSES, AgentRun, SubagentThread
from yuxi.utils.datetime_utils import utc_now_naive

TERMINAL_RUN_STATUSES = set(AGENT_RUN_TERMINAL_STATUSES)


class AgentRunRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one_or_none()

    async def lock_run(self, run_id: str) -> AgentRun | None:
        """锁定指定 Run，供跨 request/run 的事务状态机使用。"""
        return await self._lock_run(run_id)

    async def get_run_by_request_id(self, request_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_run_for_user(self, run_id: str, uid: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(and_(AgentRun.id == run_id, AgentRun.uid == str(uid))))
        return result.scalar_one_or_none()

    async def get_subagent_run_for_creator(
        self,
        *,
        uid: str,
        created_by_run_id: str,
        run_id: str,
    ) -> AgentRun | None:
        """读取当前父 run 作用域内的子智能体 run，并校验线程关系一致性。"""
        creator_run = await self.get_run_for_user(created_by_run_id, uid)
        if not creator_run:
            return None

        run = await self.get_run_for_user(run_id, uid)
        if not run or run.run_type != "subagent":
            return None
        if run.created_by_run_id != creator_run.id:
            return None

        relation_id = run.subagent_thread_relation_id
        if not relation_id:
            return None
        result = await self.db.execute(
            select(SubagentThread).where(
                SubagentThread.id == relation_id,
                SubagentThread.uid == str(uid),
            )
        )
        relation = result.scalar_one_or_none()
        if not relation or relation.parent_conversation_id != creator_run.conversation_id:
            return None
        if relation.child_thread_id != run.conversation_thread_id:
            return None
        return run

    async def get_latest_subagent_run_by_thread_for_user(
        self, conversation_thread_id: str, uid: str
    ) -> AgentRun | None:
        """读取某个子线程最近一次子智能体 run，用于状态页和继续线程校验。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type == "subagent",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_run_by_thread_for_user(self, conversation_thread_id: str, uid: str) -> AgentRun | None:
        """读取线程最近一次 run，用于恢复查询 checkpoint 时的运行时模型。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.uid == str(uid),
                AgentRun.run_type.in_(["chat", "resume", "subagent"]),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_chat_or_resume_run(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> AgentRun | None:
        """读取队列作用域内最新的顶层 chat/resume run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.uid == str(uid),
                AgentRun.agent_slug == agent_slug,
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.run_type.in_(["chat", "resume"]),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_child_runs_for_user(self, created_by_run_id: str, uid: str) -> list[AgentRun]:
        """列出由指定 run 创建的所有子 run。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.created_by_run_id == created_by_run_id,
                AgentRun.uid == str(uid),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def list_active_child_runs_for_user(self, created_by_run_id: str, uid: str) -> list[AgentRun]:
        """列出由指定 run 创建且尚未结束的子 run，用于父 run 取消时级联处理。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.created_by_run_id == created_by_run_id,
                AgentRun.uid == str(uid),
                AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def get_active_run_by_thread_for_user(
        self,
        *,
        agent_slug: str,
        conversation_thread_id: str,
        uid: str,
    ) -> AgentRun | None:
        """检查同一用户、智能体、线程上是否已有未结束 run，避免并发写同一线程。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(
                AgentRun.agent_slug == agent_slug,
                AgentRun.uid == str(uid),
                AgentRun.conversation_thread_id == conversation_thread_id,
                AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_run(
        self,
        *,
        run_id: str,
        conversation_thread_id: str,
        agent_slug: str,
        uid: str,
        request_id: str,
        input_payload: dict,
        conversation_id: int | None = None,
        created_by_run_id: str | None = None,
        subagent_thread_relation_id: int | None = None,
        run_type: str = "chat",
        input_message_id: int | None = None,
    ) -> AgentRun:
        """登记一条 run 记录；输入正文和图片应通过 input_message_id 指向 Message。"""
        run = AgentRun(
            id=run_id,
            conversation_thread_id=conversation_thread_id,
            agent_slug=agent_slug,
            uid=str(uid),
            request_id=request_id,
            conversation_id=conversation_id,
            created_by_run_id=created_by_run_id,
            subagent_thread_relation_id=subagent_thread_relation_id,
            run_type=run_type,
            input_message_id=input_message_id,
            input_payload=input_payload or {},
            status="pending",
        )
        self.db.add(run)
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
        if run.status in TERMINAL_RUN_STATUSES or run.status == "cancel_requested":
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
    ) -> tuple[AgentRun | None, bool]:
        run = await self._lock_run(run_id)
        if not run:
            return None, False
        if run.status in TERMINAL_RUN_STATUSES:
            return run, False
        if run.status == "cancel_requested" and status != "cancelled":
            return run, False
        run.status = status
        run.error_type = error_type
        run.error_message = error_message
        run.finished_at = utc_now_naive()
        run.updated_at = run.finished_at
        await self.db.flush()
        return run, True

    async def _lock_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
