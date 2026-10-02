"""用户共享 Skill 投影的授权快照与目录发布用例。"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import shutil
import stat
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.config import get_skill_data_dir, get_skill_projection_dir
from yuxi.permissions import ResourcePermission, resolve_skill_permission
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.package import (
    copy_skill_tree_no_symlinks,
    is_valid_skill_slug,
    validated_shared_skill_parts,
)
from yuxi.storage.postgres.models_business import Skill, User
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import open_directory_fd, open_regular_file_fd

_USER_SKILLS_LOCK = threading.Lock()
_USER_SKILLS_LOCKS: dict[str, threading.Lock] = {}
_USER_SKILL_PROJECTION_LOCK_SCOPE = "yuxi:skills:user-projection:v1:"


async def refresh_user_skill_projection_async(uid: str) -> dict[str, str]:
    """按数据库中的最新授权快照重建用户共享 Skill 投影。"""
    from yuxi.repositories.user_repository import UserRepository
    from yuxi.storage.postgres.manager import pg_manager

    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("uid is required to refresh the user Skill projection")

    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository().get_by_uid_with_db(db, normalized_uid)
        if user is None or bool(user.is_deleted):
            source_dirs: dict[str, str] = {}
        else:
            source_dirs = {
                item.slug: str(_resolve_shared_skill_dir(item))
                for item in await lock_accessible_shared_skills_for_projection(db, user)
                if item.enabled and item.slug
            }
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
            {"lock_scope": f"{_USER_SKILL_PROJECTION_LOCK_SCOPE}{normalized_uid}"},
        )
        await asyncio.to_thread(sync_user_accessible_skills, normalized_uid, source_dirs)
        return source_dirs


async def commit_skill_policy_and_refresh_projections(db: AsyncSession, slug: str) -> None:
    """提交 Skill 授权变更，并同步所有已存在的 uid 投影。"""
    from yuxi.workspace.paths import workspace_uid_dirname

    result = await db.execute(select(User.uid).where(User.is_deleted == 0).order_by(User.id))
    projection_root = get_skill_projection_dir()
    uids = [str(uid) for uid in result.scalars().all() if (projection_root / workspace_uid_dirname(str(uid))).is_dir()]
    for uid in uids:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
            {"lock_scope": f"{_USER_SKILL_PROJECTION_LOCK_SCOPE}{uid}"},
        )
    for uid in uids:
        await asyncio.to_thread(_remove_skill_from_user_projection, uid, slug)
    await db.commit()
    for uid in uids:
        await refresh_user_skill_projection_async(uid)


def sync_user_accessible_skills(
    uid: str,
    source_dirs: dict[str, str | Path],
) -> Path:
    """将用户有权访问的共享 Skill 来源同步到统一只读目录。"""
    user_skills_root = get_user_skills_root_dir(uid)
    normalized_sources = {
        slug: Path(os.path.abspath(os.fspath(path)))
        for slug, path in source_dirs.items()
        if is_valid_skill_slug(slug) and isinstance(path, (str, Path))
    }
    accessible_slugs = set(normalized_sources)
    with _get_user_skills_lock(uid), _user_skills_file_lock(uid):
        for entry in user_skills_root.iterdir():
            if entry.name in accessible_slugs:
                continue
            _remove_skill_projection_entry(entry)

        for slug, source_dir in normalized_sources.items():
            target_dir = user_skills_root / slug
            temp_target = user_skills_root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
            try:
                if skill_dirs_equal(source_dir, target_dir):
                    continue
                copy_skill_tree_no_symlinks(source_dir, temp_target)
                _remove_skill_projection_entry(target_dir)
                temp_target.rename(target_dir)
            except FileNotFoundError:
                logger.warning(f"跳过不存在的 Skill 来源: slug={slug}")
                _remove_skill_projection_entry(target_dir)
            except (OSError, ValueError):
                _remove_skill_projection_entry(target_dir)
                raise
            finally:
                if temp_target.exists():
                    shutil.rmtree(temp_target, ignore_errors=True)

    return user_skills_root


async def lock_accessible_shared_skills_for_projection(db: AsyncSession, user: User) -> list[Skill]:
    """先锁定授权候选，再供投影按最新启停状态复制文件。"""
    repo = SkillRepository(db)
    visible = await repo.list_authorized_for_projection(user)
    locked = {item.id: item for item in await repo.lock_rows_for_read([item.id for item in visible])}
    return [
        locked[item.id]
        for item in visible
        if item.id in locked and resolve_skill_permission(user, locked[item.id]) != ResourcePermission.NONE
    ]


def get_user_skills_root_dir(uid: str) -> Path:
    """返回当前用户获授权的共享 Skill 只读投影根目录。"""
    from yuxi.workspace.paths import workspace_uid_dirname

    safe_uid = workspace_uid_dirname(uid)
    root = get_skill_projection_dir() / safe_uid
    root.mkdir(parents=True, exist_ok=True)
    return root


def skill_dirs_equal(dir1: Path, dir2: Path) -> bool:
    """按 no-follow 字节与执行位比较来源和投影，非法来源显式失败。"""
    source_hash = _compute_projection_hash(dir1)
    try:
        return source_hash == _compute_projection_hash(dir2)
    except OSError:
        # 缺失或被替换为链接的投影必须重建，不能沿用相同字节的链接。
        return False


def _get_user_skills_lock(uid: str) -> threading.Lock:
    """取得当前进程内按用户划分的投影锁。"""
    with _USER_SKILLS_LOCK:
        lock = _USER_SKILLS_LOCKS.get(uid)
        if lock is None:
            lock = threading.Lock()
            _USER_SKILLS_LOCKS[uid] = lock
        return lock


@contextmanager
def _user_skills_file_lock(uid: str):
    """在共享投影卷上串行化同一用户的目录替换。"""
    from yuxi.workspace.paths import workspace_uid_dirname

    lock_dir = get_skill_projection_dir() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{workspace_uid_dirname(uid)}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _remove_skill_from_user_projection(uid: str, slug: str) -> None:
    """从一个已物化 uid 投影移除 Skill，授权变更时保持 fail-closed。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    with _get_user_skills_lock(uid), _user_skills_file_lock(uid):
        _remove_skill_projection_entry(get_user_skills_root_dir(uid) / slug)


def _remove_skill_projection_entry(path: Path) -> None:
    """删除一个投影条目，不跟随可能存在的符号链接。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _compute_projection_hash(path: Path) -> bytes:
    """通过目录 fd 读取投影比较摘要，拒绝链接和特殊文件。"""
    hasher = hashlib.sha256()

    def visit(directory_fd: int) -> None:
        """在已打开的目录内递归比较所需的类型、执行位和字节。"""
        for name in sorted(os.listdir(directory_fd)):
            hasher.update(os.fsencode(name) + b"\0")
            mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child_fd = open_directory_fd(directory_fd, (name,))
                try:
                    hasher.update(b"directory\0")
                    visit(child_fd)
                    hasher.update(b"end-directory\0")
                finally:
                    os.close(child_fd)
            else:
                with open_regular_file_fd(directory_fd, (name,)) as (file_fd, file_stat):
                    hasher.update(b"file\0" + bytes([stat.S_IMODE(file_stat.st_mode) & 0o111]))
                    content_hash = hashlib.sha256()
                    while chunk := os.read(file_fd, 1024 * 1024):
                        content_hash.update(chunk)
                    hasher.update(content_hash.digest())

    absolute = Path(os.path.abspath(path))
    directory_fd = open_directory_fd(Path(absolute.anchor), absolute.parts[1:])
    try:
        visit(directory_fd)
    finally:
        os.close(directory_fd)
    return hasher.digest()


def _resolve_shared_skill_dir(item: Skill) -> Path:
    """把已锁定的共享行映射到唯一持久来源。"""
    return get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, item.dir_path))
