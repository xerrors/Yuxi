"""授权 Session 对持久化 Project Workdir 的访问。"""

from __future__ import annotations

from dataclasses import dataclass

from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import ResourceNotFoundError, StateConflictError


@dataclass(frozen=True, slots=True)
class WorkdirBinding:
    """Session 当前 Project 所拥有的已授权 Workdir 快照。"""

    uid: str
    workdir_path: str

    def open_existing(self) -> Workdir:
        """打开当前绑定的真实目录，缺失或被替换时拒绝执行。"""
        try:
            return Workdir.open_existing(self.uid, self.workdir_path)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            raise StateConflictError("对话的工作目录已不存在或不可访问", code="session_workdir_unavailable") from exc


@dataclass(frozen=True, slots=True)
class AuthorizedWorkdir:
    """Service 授权上下文与持久化 Workdir。"""

    session_record_id: int
    thread_id: str
    workdir: Workdir

    @property
    def workdir_path(self) -> str:
        return self.workdir.relative_path


def workdir_binding_from_project(*, agent_session: Session, uid: str, project: Project | None) -> WorkdirBinding:
    """从已加载的 Project 构造线程 Workdir 快照，避免再次查询 Project。"""
    if project is None:
        raise RuntimeError("Session 绑定的 Project 不存在")
    session_project_id = str(agent_session.project_id or "")
    project_id = str(project.id or "")
    if not session_project_id or not project_id or project_id != session_project_id:
        raise RuntimeError("Session 与 Project 绑定不一致")
    if str(project.uid or "") != str(uid):
        raise RuntimeError("Project 不属于当前用户")
    workdir_path = str(project.workdir_path or "")
    directory_mode = str(project.directory_mode or "")
    if not workdir_path or directory_mode not in {"managed", "linked"}:
        raise RuntimeError("Project Workdir 绑定无效")
    return WorkdirBinding(
        uid=str(uid),
        workdir_path=workdir_path,
    )


async def resolve_authorized_workdir(*, thread_id: str, uid: str, db, app_id: str | None = None) -> AuthorizedWorkdir:
    """按 Thread 的用户与 APP 身份授权并打开持久 Workdir。"""
    agent_session = await SessionRepository(db).get_session_by_thread_id(thread_id)
    return await resolve_authorized_session_workdir(
        agent_session=agent_session,
        uid=uid,
        db=db,
        app_id=app_id,
    )


async def resolve_authorized_session_workdir(
    *, agent_session: Session | None, uid: str, db, app_id: str | None = None
) -> AuthorizedWorkdir:
    """复用已查询的 Thread，重新校验完整作用域后打开 Workdir。"""
    if (
        agent_session is None
        or agent_session.uid != str(uid)
        or getattr(agent_session, "app_id", None) != app_id
        or agent_session.status == "deleted"
    ):
        raise ResourceNotFoundError("对话线程不存在")
    binding = await resolve_session_workdir_binding(
        agent_session=agent_session,
        uid=str(uid),
        db=db,
    )
    return AuthorizedWorkdir(
        session_record_id=agent_session.id,
        thread_id=agent_session.thread_id,
        workdir=binding.open_existing(),
    )


async def resolve_session_workdir_path(*, agent_session: Session, uid: str, db) -> str:
    """解析 Session 的持久 Workdir 路径。"""
    binding = await resolve_session_workdir_binding(
        agent_session=agent_session,
        uid=uid,
        db=db,
    )
    return binding.workdir_path


async def ensure_session_workdir_available(
    *,
    agent_session: Session,
    uid: str,
    db,
) -> str:
    """确认 Session 的持久 Workdir 仍可用，并返回绑定路径。"""
    binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=uid, db=db)
    binding.open_existing()
    return binding.workdir_path


async def session_workdir_is_available(*, agent_session: Session, db) -> bool:
    """自动调度跳过暂不可访问的绑定，保留已接收的工作等待恢复。"""
    try:
        await ensure_session_workdir_available(agent_session=agent_session, uid=agent_session.uid, db=db)
    except StateConflictError as exc:
        if exc.code != "session_workdir_unavailable":
            raise
        return False
    return True


async def resolve_session_workdir_binding(*, agent_session: Session, uid: str, db, project: Project | None = None) -> WorkdirBinding:
    """解析 Session 唯一 Project 所拥有的持久 Workdir。"""
    resolved_project = project
    if resolved_project is None:
        resolved_project = await ProjectRepository(db).get_for_user(agent_session.project_id, str(uid))
    return workdir_binding_from_project(agent_session=agent_session, uid=uid, project=resolved_project)
