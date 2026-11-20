"""授权 Session 对持久化 Project Workdir 的访问。"""

from __future__ import annotations

from dataclasses import dataclass

from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import ensure_bound_user_workdir
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import ResourceNotFoundError, StateConflictError


@dataclass(frozen=True, slots=True)
class WorkdirBinding:
    """Session 当前 Project 所拥有的已授权 Workdir 快照。"""

    session_record_id: int
    thread_id: str
    uid: str
    project_id: str
    workdir_path: str
    directory_mode: str

    @property
    def materialize_managed(self) -> bool:
        """判断该绑定是否需要在事务提交后物化目录。"""
        return self.directory_mode == "managed"


@dataclass(frozen=True, slots=True)
class AuthorizedWorkdir:
    """Service 授权上下文与持久化 Workdir。"""

    session_record_id: int
    thread_id: str
    uid: str
    workdir: Workdir
    project_id: str
    directory_mode: str

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
    thread_id = str(agent_session.thread_id or "")
    if not thread_id:
        raise RuntimeError("Session 缺少 thread_id")
    workdir_path = str(project.workdir_path or "")
    directory_mode = str(project.directory_mode or "")
    if not workdir_path or directory_mode not in {"managed", "linked"}:
        raise RuntimeError("Project Workdir 绑定无效")
    return WorkdirBinding(
        session_record_id=int(agent_session.id),
        thread_id=thread_id,
        uid=str(uid),
        project_id=project_id,
        workdir_path=workdir_path,
        directory_mode=directory_mode,
    )


def _validate_workdir_binding(binding: WorkdirBinding, *, agent_session: Session, uid: str) -> None:
    """校验传入快照仍属于当前用户和 Session。"""
    if (
        binding.uid != str(uid)
        or binding.session_record_id != int(agent_session.id)
        or binding.thread_id != str(agent_session.thread_id)
        or binding.project_id != str(agent_session.project_id)
    ):
        raise RuntimeError("传入的 Workdir 绑定与 Session 不一致")


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
        uid=str(uid),
        workdir=Workdir.open_existing(str(uid), binding.workdir_path),
        project_id=binding.project_id,
        directory_mode=binding.directory_mode,
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
    workdir_binding: WorkdirBinding | None = None,
) -> str:
    """确保 Session 的持久 Workdir 可用，并返回其相对路径。"""
    binding = workdir_binding
    if binding is None:
        binding = await resolve_session_workdir_binding(
            agent_session=agent_session,
            uid=uid,
            db=db,
        )
    else:
        _validate_workdir_binding(binding, agent_session=agent_session, uid=uid)
    if binding.materialize_managed:
        ensure_bound_user_workdir(binding.uid, binding.workdir_path)
    else:
        Workdir.open_existing(binding.uid, binding.workdir_path)
    return binding.workdir_path


async def ensure_session_workdir_acceptable(*, agent_session: Session, uid: str, db) -> None:
    """附件 Input 的接收门禁：linked Workdir 是用户拥有的目录，缺失属永久失败。

    在接收边界直接拒绝，让提交方当场得到可行动的提示，而不是生成 Input 后
    进入恢复循环对同一缺失目录无限重试；managed 目录在提交后会物化，不受影响。
    """
    binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=uid, db=db)
    if binding.materialize_managed:
        return
    try:
        Workdir.open_existing(binding.uid, binding.workdir_path)
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as exc:
        raise StateConflictError("项目目录不存在，无法接收附件；请在个人空间恢复该目录后重试") from exc


async def resolve_session_workdir_binding(*, agent_session: Session, uid: str, db, project: Project | None = None) -> WorkdirBinding:
    """解析 Session 唯一 Project 所拥有的持久 Workdir。"""
    resolved_project = project
    if resolved_project is None:
        resolved_project = await ProjectRepository(db).get_for_user(agent_session.project_id, str(uid))
    return workdir_binding_from_project(agent_session=agent_session, uid=uid, project=resolved_project)
