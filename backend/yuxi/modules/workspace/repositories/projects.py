"""Project 持久化 Repository。"""

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES, AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.sessions import exclude_personal_sessions
from yuxi.modules.workspace.errors import ProjectHasPendingAgentWorkError
from yuxi.modules.workspace.models import Project


class ProjectRepository:
    """读写当前用户的 Project 业务事实。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def add(self, project: Project) -> Project:
        """新增 Project 并 flush。"""
        self.db.add(project)
        await self.db.flush()
        return project

    async def get_for_user(self, project_id: str, uid: str) -> Project | None:
        """按用户读取 Project。"""
        return await self.db.scalar(select(Project).where(Project.id == project_id, Project.uid == str(uid)))

    async def lock_active_for_user(self, project_id: str, uid: str) -> Project | None:
        """锁定当前用户的 active Project。"""
        return await self.db.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.uid == str(uid),
                Project.status == "active",
            )
            .with_for_update()
        )

    async def lock_active_selectable_for_user(self, project_id: str, uid: str) -> Project | None:
        """锁定当前用户可管理的 active selectable Project。"""
        return await self.db.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .with_for_update()
        )

    async def get_by_idempotency_key(self, idempotency_key: str, uid: str) -> Project | None:
        """按用户和幂等键读取 Project。"""
        return await self.db.scalar(select(Project).where(Project.uid == str(uid), Project.idempotency_key == idempotency_key))

    async def list_selectable_for_user(self, uid: str) -> list[Project]:
        """列出用户可选择的 Project。"""
        result = await self.db.execute(
            select(Project)
            .where(
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
        return list(result.scalars().all())

    async def list_bound_workdir_paths_for_user(self, uid: str) -> set[str]:
        """读取全部持久目录绑定，包含软删除项目以避免复用其文件。"""
        result = await self.db.scalars(select(Project.workdir_path).where(Project.uid == str(uid)))
        return set(result.all())

    async def list_selectable_workdir_paths_for_user(self, uid: str) -> list[str]:
        """列出用户已选择 Project 的去重 Workdir 路径。"""
        result = await self.db.execute(
            select(Project.workdir_path)
            .where(
                Project.uid == str(uid),
                Project.selection_status == "selectable",
                Project.status == "active",
            )
            .distinct()
        )
        return list(result.scalars().all())

    async def list_history_candidates(self, uid: str) -> list[tuple[Session, str]]:
        """列出可解析实际 Workdir 的普通历史对话。"""
        result = await self.db.execute(
            select(Session, Project.workdir_path)
            .join(Project, (Project.uid == Session.uid) & (Project.id == Session.project_id))
            .where(
                Session.uid == str(uid),
                Session.status == "active",
                Project.status == "active",
                exclude_personal_sessions(),
            )
            .order_by(Session.updated_at.desc(), Session.id.desc())
        )
        return list(result.all())

    async def delete_project_and_archive_threads(self, project: Project, *, deleted_at: datetime) -> int:
        """锁定关联 Thread，确认空闲后归档并软删除 Project。"""
        rows = await self.db.execute(
            select(Session.thread_id)
            .where(Session.uid == project.uid, Session.project_id == project.id)
            .order_by(Session.thread_id)
            .with_for_update(key_share=True)
        )
        thread_ids = list(rows.scalars())
        if thread_ids:
            active_turn = await self.db.scalar(
                select(AgentTurn.id)
                .where(
                    AgentTurn.thread_id.in_(thread_ids),
                    AgentTurn.status.in_(("running", "waiting", "cancelling")),
                )
                .limit(1)
            )
            pending_input = await self.db.scalar(
                select(AgentInput.id).where(AgentInput.thread_id.in_(thread_ids), AgentInput.status == "pending").limit(1)
            )
            active_run = await self.db.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.uid == project.uid,
                    or_(
                        AgentRun.thread_id.in_(thread_ids),
                        AgentRun.runtime_scope_id.in_(thread_ids),
                    ),
                    or_(
                        AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
                        AgentRun.runtime_cleanup_pending.is_(True),
                    ),
                )
                .limit(1)
            )
            if active_turn or pending_input or active_run:
                raise ProjectHasPendingAgentWorkError

        result = await self.db.execute(
            update(Session)
            .where(
                Session.uid == project.uid,
                Session.project_id == project.id,
                Session.status == "active",
            )
            .values(status="archived", updated_at=deleted_at)
        )
        project.status = "deleted"
        project.deleted_at = deleted_at
        project.updated_at = deleted_at
        await self.db.flush()
        return int(result.rowcount or 0)
