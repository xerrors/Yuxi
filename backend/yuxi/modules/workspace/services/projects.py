"""Project 创建、选择与历史目录复用用例。"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from yuxi.modules.workspace.errors import ProjectHasPendingAgentWorkError
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import allocate_default_user_workdir_path, create_managed_workdir, normalize_workdir_path
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError

MAX_PROJECT_NAME_LENGTH = 255


async def create_project_view(*, uid: str, request_id: str, name: str, directory_mode: str, workdir_path: str | None, db) -> dict:
    """幂等创建 linked selectable Project，同 request_id 重放时要求创建意图一致。"""
    normalized_request_id = (request_id or "").strip()
    if not normalized_request_id:
        raise InvalidInputError("request_id 不能为空")
    if directory_mode != "linked" or not (workdir_path or "").strip():
        raise InvalidInputError("手动创建项目必须选择目录")
    normalized_name = _normalize_project_name(name, required=True)
    try:
        normalized_path = normalize_workdir_path(workdir_path)
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc

    repository = ProjectRepository(db)
    existing = await repository.get_by_idempotency_key(normalized_request_id, uid)
    if existing is not None:
        _require_matching_creation_intent(existing, name=normalized_name, workdir_path=normalized_path)
        return existing.to_dict()

    try:
        project = await create_project_record(
            uid=uid,
            name=normalized_name,
            directory_mode=directory_mode,
            selection_status="selectable",
            workdir_path=normalized_path,
            db=db,
            idempotency_key=normalized_request_id,
        )
        await db.commit()
    except IntegrityError:
        # 并发提交同一 request_id 时由唯一约束兜底：回滚后重放已提交记录，仍按原创建意图校验。
        await db.rollback()
        replay = await repository.get_by_idempotency_key(normalized_request_id, uid)
        if replay is None:
            raise StateConflictError("Project 创建冲突")
        _require_matching_creation_intent(replay, name=normalized_name, workdir_path=normalized_path)
        project = replay
    return project.to_dict()


async def create_implicit_project(*, uid: str, db, idempotency_key: str | None = None, naming_text: str | None = None) -> Project:
    """为新 Session 创建 implicit managed Project。"""
    return await create_project_record(
        uid=uid,
        name=None,
        directory_mode="managed",
        selection_status="implicit",
        db=db,
        idempotency_key=idempotency_key,
        naming_text=naming_text,
    )


def materialize_implicit_project(project: Project) -> None:
    """在创建用例提交前排他建立新绑定的目录，保留任何晚到的占用。"""
    try:
        create_managed_workdir(project.uid, project.workdir_path)
    except (FileExistsError, ValueError) as exc:
        raise StateConflictError("工作目录路径已被占用，请重试创建对话") from exc


async def create_project_record(
    *,
    uid: str,
    name: str | None,
    directory_mode: str,
    selection_status: str,
    db,
    workdir_path: str | None = None,
    idempotency_key: str | None = None,
    naming_text: str | None = None,
) -> Project:
    """在当前事务内创建 Project，但不提交或物化 managed 目录。"""
    normalized_name = _normalize_project_name(name, required=selection_status == "selectable")
    if directory_mode not in {"managed", "linked"}:
        raise InvalidInputError("directory_mode 必须是 managed 或 linked")
    if selection_status not in {"implicit", "selectable"}:
        raise InvalidInputError("selection_status 非法")

    project_id = str(uuid.uuid4())
    if directory_mode == "managed":
        if workdir_path is not None:
            raise InvalidInputError("managed Project 不接受 workdir_path")
        await _lock_project_workdir_changes(db=db, uid=uid)
        reserved_paths = await ProjectRepository(db).list_bound_workdir_paths_for_user(uid)
        try:
            normalized_path = allocate_default_user_workdir_path(uid, project_id, reserved_paths=reserved_paths, naming_text=naming_text)
        except ValueError as exc:
            raise StateConflictError("工作目录的父路径已被占用") from exc
    else:
        if workdir_path is None:
            raise InvalidInputError("linked Project 必须指定 workdir_path")
        try:
            normalized_path = normalize_workdir_path(workdir_path)
            await _lock_project_workdir_changes(db=db, uid=uid)
            Workdir.open_existing(uid, normalized_path)
        except FileNotFoundError as exc:
            raise ResourceNotFoundError("目录不存在") from exc
        except (NotADirectoryError, PermissionError, OSError, ValueError) as exc:
            raise InvalidOperationError(str(exc)) from exc

    project = Project(
        id=project_id,
        uid=uid,
        name=normalized_name,
        selection_status=selection_status,
        workdir_path=normalized_path,
        directory_mode=directory_mode,
        idempotency_key=idempotency_key.strip() if idempotency_key else None,
    )
    return await ProjectRepository(db).add(project)


async def list_projects_view(*, uid: str, db) -> list[dict]:
    """列出当前用户可选择的 Project。"""
    projects = await ProjectRepository(db).list_selectable_for_user(uid)
    return [project.to_dict() for project in projects]


async def rename_project_view(*, uid: str, project_id: str, name: str, db) -> dict:
    """重命名当前用户的 selectable Project。"""
    normalized_name = _normalize_project_name(name, required=True)
    repository = ProjectRepository(db)
    project = await repository.lock_active_selectable_for_user(project_id, uid)
    if project is None:
        raise ResourceNotFoundError("Project 不存在")

    project.name = normalized_name
    project.updated_at = utc_now()
    await db.commit()
    await db.refresh(project)
    return project.to_dict()


async def delete_project_view(*, uid: str, project_id: str, db) -> dict:
    """删除 Project 前归档全部空闲 Thread，保留历史和文件。"""
    repository = ProjectRepository(db)
    project = await repository.lock_active_selectable_for_user(project_id, uid)
    if project is None:
        raise ResourceNotFoundError("Project 不存在")

    try:
        archived_threads = await repository.delete_project_and_archive_threads(
            project,
            deleted_at=utc_now(),
        )
    except ProjectHasPendingAgentWorkError as exc:
        raise StateConflictError("项目内仍有执行或待处理输入，暂不能归档") from exc
    await db.commit()
    return {"message": "项目已删除，其中对话已归档", "archived_threads": archived_threads}


async def list_history_candidates_view(*, uid: str, db, query: str = "", limit: int = 20, offset: int = 0) -> dict:
    """列出可作为新建 Project 目录快捷入口的历史 Session。"""
    sessions = await ProjectRepository(db).list_history_candidates(uid)
    normalized_query = (query or "").strip().lower()
    items = []
    seen_workdirs = set()
    for item, workdir_path in sessions:
        if workdir_path in seen_workdirs:
            continue
        if (
            normalized_query
            and normalized_query not in (item.title or "").lower()
            and normalized_query not in (item.agent_id or "").lower()
        ):
            continue
        seen_workdirs.add(workdir_path)
        try:
            Workdir.open_existing(uid, workdir_path)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
            continue
        items.append(
            {
                "thread_id": item.thread_id,
                "title": item.title,
                "agent_id": item.agent_id,
                "workdir_path": workdir_path,
                "updated_at": item.updated_at.isoformat(),
            }
        )
        if len(items) > offset + limit:
            break
    return {"items": items[offset : offset + limit], "has_more": len(items) > offset + limit}


async def _lock_project_workdir_changes(*, db, uid: str) -> None:
    """串行化同一用户的 Project 目录分配、绑定与测试目录删除。"""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"project-workdir:{uid}"},
    )


def _normalize_project_name(name: str | None, *, required: bool) -> str | None:
    """规范化并校验 Project 名称。"""
    normalized_name = (name or "").strip()
    if required and not normalized_name:
        raise InvalidInputError("项目名称不能为空")
    if len(normalized_name) > MAX_PROJECT_NAME_LENGTH:
        raise InvalidInputError("项目名称过长")
    return normalized_name or None


def _require_matching_creation_intent(project: Project, *, name: str, workdir_path: str) -> None:
    """要求已有 Project 仍有效且匹配当前幂等创建意图。"""
    if project.status == "deleted":
        raise StateConflictError("request_id 已用于已删除的 Project")
    if project.name != name or project.directory_mode != "linked" or project.workdir_path != workdir_path:
        raise StateConflictError("request_id 已用于其他 Project 创建意图")
