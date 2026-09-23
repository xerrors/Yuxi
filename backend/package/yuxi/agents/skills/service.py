from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.service import get_enabled_mcp_server_slugs
from yuxi.agents.skills.buildin import BUILTIN_SKILLS_DIR
from yuxi.agents.skills.repository import SkillRepository
from yuxi.config import (
    get_runtime_dir,
    get_skill_data_dir,
    get_skill_projection_dir,
)
from yuxi.permissions import (
    ResourcePermission,
    normalize_permission_config,
    resolve_agent_permission,
    resolve_skill_permission,
)
from yuxi.storage.postgres.models_business import Agent, Skill, User
from yuxi.utils.logging_config import logger
from yuxi.utils.paths import ensure_within_root, open_directory_fd, open_regular_file_fd

SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_PATTERN = SKILL_SLUG_PATTERN
AGENT_BOUND_SKILL_SOURCE_TYPE = "agent_bound"
SELF_SKILL_SLUG_SUFFIX = "-self-skill"

TEXT_FILE_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".html",
    ".css",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
    ".env",
    ".csv",
    ".tsv",
    ".rst",
    ".ipynb",
    ".vue",
    ".jsx",
    ".tsx",
}

BUILTIN_SKILL_OPERATOR = "builtin-system"
ADMIN_ROLES = {"admin", "superadmin"}
DEFAULT_SKILL_SHARE_CONFIG = {"access_level": "user", "department_ids": [], "user_uids": []}
BUILTIN_SKILL_SHARE_CONFIG = {"access_level": "global", "department_ids": [], "user_uids": []}
SKILL_DRAFT_TTL_SECONDS = 60 * 60
PERSONAL_SKILL_SOURCE_TYPE = "personal"
_USER_SKILLS_LOCK = threading.Lock()
_USER_SKILLS_LOCKS: dict[str, threading.Lock] = {}
_USER_SKILL_PROJECTION_LOCK_SCOPE = "yuxi:skills:user-projection:v1:"
SKILL_STORAGE_LOCK = 0x5958534B


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    """描述当前用户最终可用的 Skill 及其真实来源。"""

    id: Any
    slug: str
    name: str
    description: str
    source_type: str
    source_scope: str
    source_dir: Path
    enabled: bool
    created_by: str | None
    share_config: dict[str, Any] | None
    tool_dependencies: list[str]
    mcp_dependencies: list[str]
    skill_dependencies: list[str]
    version: str | None = None
    content_hash: str | None = None
    bound_agent_id: int | None = None
    overrides_shared: bool = False
    shadowed_by_personal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回可安全提供给前端的 Skill 元数据。"""
        data = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source_scope": self.source_scope,
            "bound_agent_id": self.bound_agent_id,
            "is_agent_bound": self.bound_agent_id is not None,
            "enabled": self.enabled,
            "created_by": self.created_by,
            "tool_dependencies": self.tool_dependencies,
            "mcp_dependencies": self.mcp_dependencies,
            "skill_dependencies": self.skill_dependencies,
            "overrides_shared": self.overrides_shared,
            "shadowed_by_personal": self.shadowed_by_personal,
        }
        if self.share_config is not None:
            data["share_config"] = self.share_config
        return data


def _get_user_skills_lock(uid: str) -> threading.Lock:
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


def normalize_string_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def is_valid_skill_slug(slug: str) -> bool:
    if not isinstance(slug, str):
        return False
    return bool(SKILL_SLUG_PATTERN.match(slug.strip()))


def is_builtin_skill(item: Skill | dict) -> bool:
    source_type = item.get("source_type") if isinstance(item, dict) else item.source_type
    return source_type == "builtin"


def is_agent_bound_skill(item: Skill | dict) -> bool:
    """判断 Skill 是否绑定到某个 Agent。"""
    if isinstance(item, dict):
        return item.get("bound_agent_id") is not None
    return item.bound_agent_id is not None


def _ensure_not_agent_bound(item: Skill) -> None:
    """拒绝针对 Agent 专属技能的独立共享、启停与删除操作。"""
    if is_agent_bound_skill(item):
        raise ValueError("Agent 专属技能不允许单独共享、启停或删除，请通过所属智能体管理")


async def resolve_bound_agent(db: AsyncSession, item: Skill) -> Agent | None:
    """读取绑定 Skill 所属的 Agent；归属缺失时返回 None 表示 fail-closed。"""
    from yuxi.repositories.agent_repository import AgentRepository

    if item.bound_agent_id is None:
        return None
    return await AgentRepository(db).get_by_id(item.bound_agent_id)


async def resolve_agent_bound_permission_source(db: AsyncSession, item: Skill) -> Agent:
    """返回绑定 Skill 的权限来源 Agent；缺失或未绑定时显式失败。"""
    agent = await resolve_bound_agent(db, item)
    if agent is None:
        raise ValueError("Agent 专属技能的绑定智能体不存在")
    return agent


async def resolve_agent_bound_permission(db: AsyncSession, user: User, item: Skill) -> ResourcePermission:
    """解析 Agent 专属技能的权限：完全派生自绑定 Agent 的共享配置。"""
    agent = await resolve_bound_agent(db, item)
    if agent is None:
        return ResourcePermission.NONE
    return resolve_agent_permission(user, agent)


async def _bound_agents_by_agent_id(db: AsyncSession, items: list[Skill]) -> dict[int, Agent]:
    """批量加载绑定 Skill 对应的 Agent，避免逐条查询。"""
    from yuxi.repositories.agent_repository import AgentRepository

    agent_ids = {item.bound_agent_id for item in items if item.bound_agent_id is not None}
    if not agent_ids:
        return {}
    agents = await AgentRepository(db).list_by_ids(sorted(agent_ids))
    return {agent.id: agent for agent in agents}


async def _effective_skill_permission(
    db: AsyncSession,
    user: User,
    item: Skill,
    bound_agents: dict[int, Agent] | None = None,
) -> ResourcePermission:
    """按 Skill 归属解析有效权限；普通 Skill 走自身共享配置。"""
    if item.bound_agent_id is None:
        return resolve_skill_permission(user, item)
    agents = bound_agents if bound_agents is not None else await _bound_agents_by_agent_id(db, [item])
    agent = agents.get(item.bound_agent_id)
    if agent is None:
        return ResourcePermission.NONE
    return resolve_agent_permission(user, agent)


async def user_can_access_skill_async(
    db: AsyncSession,
    user: User,
    item: Skill,
    *,
    require_enabled: bool = True,
) -> bool:
    """异步版可访问判断，覆盖 Agent 专属技能的派生权限。"""
    if require_enabled and not item.enabled:
        return False
    if item.bound_agent_id is None:
        return resolve_skill_permission(user, item) != ResourcePermission.NONE
    return await _effective_skill_permission(db, user, item) != ResourcePermission.NONE


async def user_can_manage_skill_async(db: AsyncSession, user: User, item: Skill) -> bool:
    """异步版可管理判断，覆盖 Agent 专属技能的派生权限。"""
    if item.bound_agent_id is None:
        return user_can_manage_skill(user, item)
    return await _effective_skill_permission(db, user, item) == ResourcePermission.MANAGE


def get_allowed_skill_access_levels(user: User) -> list[str]:
    if user.role in ADMIN_ROLES:
        return ["global", "department", "user"]
    return ["user"]


def normalize_skill_share_config(
    share_config: dict | None,
    *,
    operator_uid: str,
    source_type: str = "upload",
    allowed_access_levels: set[str] | None = None,
) -> dict:
    if source_type == "builtin":
        return {"version": 2, "read_scope": BUILTIN_SKILL_SHARE_CONFIG.copy(), "manage_scope": None}

    default_scope = {
        "access_level": "user",
        "department_ids": [],
        "user_uids": [operator_uid],
    }
    return normalize_permission_config(
        share_config or {"version": 2, "read_scope": default_scope, "manage_scope": None},
        allowed_access_levels=allowed_access_levels,
        unauthorized_access_level_message="当前用户无权使用该 Skill 共享范围",
        strict=True,
    )


def user_can_access_skill(user: User, skill: Skill, *, require_enabled: bool = True) -> bool:
    if require_enabled and not skill.enabled:
        return False
    return resolve_skill_permission(user, skill) != ResourcePermission.NONE


def user_can_manage_skill(user: User, skill: Skill) -> bool:
    if is_builtin_skill(skill):
        return user.role in ADMIN_ROLES
    return resolve_skill_permission(user, skill) == ResourcePermission.MANAGE


def can_skill_depend_on(parent: Skill, dependency: Skill) -> bool:
    """判断 Skill 是否可以把另一个 Skill 声明为依赖。"""
    return _can_depend_on(
        parent_config=normalize_permission_config(parent.share_config),
        parent_created_by=parent.created_by,
        parent_is_bound=is_agent_bound_skill(parent),
        dependency=dependency,
    )


def _can_depend_on(
    *,
    parent_config: dict,
    parent_created_by: str | None,
    parent_is_bound: bool,
    dependency: Skill,
) -> bool:
    """按父 Skill 的有效权限范围校验依赖可见性。

    Agent 专属技能的父范围是绑定 Agent 的共享范围，因此这里必须使用派生后的配置。
    """
    if not dependency.enabled:
        return False
    if is_builtin_skill(dependency):
        return True

    dep_config = normalize_permission_config(dependency.share_config)
    dependency_scopes = [scope for scope in (dep_config["read_scope"], dep_config["manage_scope"]) if scope]
    parent_scopes = [scope for scope in (parent_config["read_scope"], parent_config["manage_scope"]) if scope]
    owner_scope = {"access_level": "user", "department_ids": [], "user_uids": []}
    if not dependency_scopes:
        dependency_scopes = [{**owner_scope, "user_uids": [str(dependency.created_by or "")]}]
    if parent_is_bound:
        # 绑定 Skill 的可访问范围等于绑定 Agent 的读取范围（权限派生，不是自身 share_config）。
        parent_scopes = [parent_config["read_scope"]] if parent_config["read_scope"] else []
    if not parent_scopes:
        parent_scopes = [{**owner_scope, "user_uids": [str(parent_created_by or "")]}]
    return all(
        any(_scope_contains(dependency_scope, parent_scope) for dependency_scope in dependency_scopes)
        for parent_scope in parent_scopes
    )


def _scope_contains(container: dict, target: dict) -> bool:
    """判断一个共享范围是否完整覆盖另一个范围。"""

    container_level = container.get("access_level")
    target_level = target.get("access_level")
    if container_level == "global":
        return True
    if target_level == "global" or container_level != target_level:
        return False
    if target_level == "department":
        container_ids = {int(value) for value in container.get("department_ids") or []}
        target_ids = {int(value) for value in target.get("department_ids") or []}
        return target_ids.issubset(container_ids)
    if target_level == "user":
        container_uids = {str(value) for value in container.get("user_uids") or []}
        target_uids = {str(value) for value in target.get("user_uids") or []}
        return target_uids.issubset(container_uids)
    return False


def _ensure_non_builtin(item: Skill) -> None:
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许执行该操作")


def get_skills_root_dir() -> Path:
    """返回共享与内置 Skill 的持久源目录。"""
    root = get_skill_data_dir() / "shared"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_skill_drafts_root_dir() -> Path:
    """返回可丢弃的 Skill 安装草稿目录。"""
    root = get_runtime_dir() / "skill_import_drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_skill_draft(draft_id: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", str(draft_id or "")):
        raise ValueError("无效的安装草稿")
    draft_dir = (get_skill_drafts_root_dir() / draft_id).resolve()
    try:
        draft_dir.relative_to(get_skill_drafts_root_dir().resolve())
    except ValueError:
        raise ValueError("无效的安装草稿") from None
    metadata_path = draft_dir / "metadata.json"
    if not metadata_path.exists():
        raise ValueError("安装草稿不存在或已过期")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if data.get("expires_at", 0) < time.time():
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise ValueError("安装草稿已过期")
    return draft_dir, data


def _load_and_select_draft_items(
    draft_id: str, slugs: list[str] | None, operator: User
) -> tuple[Path, dict, list[dict]]:
    """加载安装草稿，校验权限与来源类型，并按需筛选选中的条目。"""
    draft_dir, data = _load_skill_draft(draft_id)
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权确认该安装草稿")
    if data.get("source_type") not in {"upload", "remote"}:
        raise ValueError("无效的安装草稿来源")

    draft_items = data.get("items") or []
    if slugs is not None:
        selected_slugs = set(slugs)
        if not selected_slugs:
            raise ValueError("至少选择一个 Skill")
        available_slugs = {str(item.get("slug") or "").strip() for item in draft_items}
        if selected_slugs - available_slugs:
            raise ValueError("确认安装包含草稿外的 Skill")
        draft_items = [item for item in draft_items if str(item.get("slug") or "").strip() in selected_slugs]

    return draft_dir, data, draft_items


def get_user_skills_root_dir(uid: str) -> Path:
    """返回当前用户获授权的共享 Skill 只读投影根目录。"""
    from yuxi.workspace.paths import workspace_uid_dirname

    safe_uid = workspace_uid_dirname(uid)
    root = get_skill_projection_dir() / safe_uid
    root.mkdir(parents=True, exist_ok=True)
    return root


async def sync_user_accessible_skills_async(
    uid: str,
    source_dirs: dict[str, str | Path],
) -> Path:
    """在线程池同步用户获授权的共享 Skill 投影，避免阻塞 Agent 事件循环。"""
    return await asyncio.to_thread(
        sync_user_accessible_skills,
        uid,
        source_dirs,
    )


async def refresh_user_skill_projection_async(uid: str) -> dict[str, str]:
    """按数据库中的最新授权快照重建用户共享 Skill 投影。"""
    from yuxi.repositories.user_repository import UserRepository
    from yuxi.storage.postgres.manager import pg_manager

    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("uid is required to refresh the user Skill projection")

    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
            {"lock_scope": f"{_USER_SKILL_PROJECTION_LOCK_SCOPE}{normalized_uid}"},
        )
        user = await UserRepository().get_by_uid_with_db(db, normalized_uid)
        if user is None or bool(user.is_deleted):
            source_dirs: dict[str, str] = {}
        else:
            source_dirs = {
                item.slug: str(_resolve_skill_dir(item))
                for item in await _list_accessible_shared_skills(db, user)
                if item.slug
            }
        await sync_user_accessible_skills_async(normalized_uid, source_dirs)
        return source_dirs


def _remove_skill_from_user_projection(uid: str, slug: str) -> None:
    """从一个已物化 uid 投影移除 Skill，授权变更时保持 fail-closed。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    with _get_user_skills_lock(uid), _user_skills_file_lock(uid):
        _remove_skill_projection_entry(get_user_skills_root_dir(uid) / slug)


async def apply_skill_projection_policy_change(db: AsyncSession, slug: str) -> None:
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


def _remove_skill_projection_entry(path: Path) -> None:
    """删除一个投影条目，不跟随可能存在的符号链接。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _build_builtin_skill_dir_path(slug: str) -> str:
    return (Path("shared") / slug).as_posix()


def _dir_contains_symlink(path: Path) -> bool:
    """检查目录内是否包含任意符号链接子路径。"""
    return any(child.is_symlink() for child in path.rglob("*"))


def copy_skill_tree_no_symlinks(source_dir: Path, target_dir: Path) -> None:
    """复制不含符号链接的 Skill 目录到 staging。"""
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if _dir_contains_symlink(source_dir):
        raise ValueError(f"Skill 来源只允许普通文件和目录: {source_dir}")
    try:
        shutil.copytree(source_dir, target_dir, symlinks=False)
    except BaseException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def _copy_skill_snapshot(
    source_dir: Path,
    target_dir: Path,
    *,
    expected_slug: str | None = None,
    final_slug: str | None = None,
) -> dict[str, Any]:
    """复制并解析 Skill staging，可校验来源或重写最终 slug。"""
    copy_skill_tree_no_symlinks(source_dir, target_dir)
    parsed = parse_skill_dir_metadata(target_dir)
    if expected_slug and parsed["slug"] != expected_slug:
        raise ValueError("Skill slug 在复制过程中发生变化")
    if final_slug and parsed["slug"] != final_slug:
        skill_md = target_dir / "SKILL.md"
        skill_md.write_text(
            _rewrite_frontmatter_slug(skill_md.read_text(encoding="utf-8"), final_slug),
            encoding="utf-8",
        )
    return parsed


def skill_dirs_equal(dir1: Path, dir2: Path) -> bool:
    """按 no-follow 字节与执行位比较来源和投影，非法来源显式失败。"""
    source_hash = _compute_projection_hash(dir1)
    try:
        return source_hash == _compute_projection_hash(dir2)
    except OSError:
        # 缺失或被替换为链接的投影必须重建，不能沿用相同字节的链接。
        return False


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


def _compute_dir_hash(source_dir: Path) -> str:
    hasher = hashlib.sha256()
    entries = sorted(source_dir.rglob("*"), key=lambda path: path.relative_to(source_dir).as_posix())
    for entry in entries:
        relative_path = entry.relative_to(source_dir).as_posix()
        hasher.update(relative_path.encode("utf-8"))
        hasher.update(b"\0")
        if entry.is_dir():
            hasher.update(b"directory\0")
            continue
        if not entry.is_file():
            hasher.update(b"other\0")
            continue
        hasher.update(b"file\0")
        hasher.update(bytes([stat.S_IMODE(entry.stat().st_mode) & 0o111]))
        with entry.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        hasher.update(b"\0")
    return hasher.hexdigest()


def _replace_skill_target(
    target_dir: Path,
    source_dir: Path,
    *,
    validate: Callable[[Path], None] | None = None,
) -> None:
    """将 source_dir 原子地复制为 target_dir：先复制到临时目录，可选校验后再替换。"""
    temp_target = target_dir.with_name(f".{target_dir.name}.tmp-{uuid.uuid4().hex[:8]}")
    trash_dir: Path | None = None
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)

    copy_skill_tree_no_symlinks(source_dir, temp_target)
    try:
        if validate is not None:
            validate(temp_target)
        if target_dir.exists():
            trash_dir = target_dir.with_name(f".{target_dir.name}.bak-{uuid.uuid4().hex[:8]}")
            target_dir.rename(trash_dir)
        temp_target.rename(target_dir)
    except Exception:
        shutil.rmtree(temp_target, ignore_errors=True)
        if trash_dir and trash_dir.exists() and not target_dir.exists():
            trash_dir.rename(target_dir)
        raise

    if trash_dir and trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)


async def list_accessible_skills(
    db: AsyncSession,
    user: User,
    *,
    require_enabled: bool = True,
) -> list[ResolvedSkill]:
    """返回当前用户最终生效的共享、内置与 Agent 专属技能。"""
    shared_items, personal_items = await asyncio.gather(
        _list_accessible_shared_skills(db, user, require_enabled=require_enabled),
        list_personal_skills(str(user.uid)),
    )
    personal_by_slug = {item.slug: item for item in personal_items}

    effective: dict[str, ResolvedSkill] = {}
    bound_agents = await _bound_agents_by_agent_id(db, shared_items)
    for item in shared_items:
        if item.bound_agent_id is not None:
            agent = bound_agents.get(item.bound_agent_id)
            if agent is None:
                # 绑定 Agent 已不存在：fail-closed，不退回普通 Skill 语义。
                logger.warning(f"跳过绑定智能体缺失的专属技能: slug={item.slug}")
                continue
            effective[item.slug] = _resolved_agent_bound_skill(item, agent)
            continue
        effective[item.slug] = _resolved_shared_skill(
            item,
            shadowed_by_personal=item.slug in personal_by_slug,
        )
    for slug, item in personal_by_slug.items():
        effective[slug] = replace(item, overrides_shared=slug in effective)
    return list(effective.values())


async def list_skill_cards_for_user(
    db: AsyncSession,
    user: User,
    *,
    include_agent_bound: bool = False,
) -> list[ResolvedSkill]:
    """返回管理页所需的共享、内置与个人 Skill 卡片。

    Agent 专属技能默认不进入普通管理列表；显式include_agent_bound 才返回。
    """
    shared_items, personal_items = await asyncio.gather(
        list_visible_skills_for_management(db, user),
        list_personal_skills(str(user.uid)),
    )
    personal_slugs = {item.slug for item in personal_items}
    shared_slugs = {item.slug for item in shared_items}

    personal_cards = [replace(item, overrides_shared=item.slug in shared_slugs) for item in personal_items]
    bound_agents = await _bound_agents_by_agent_id(db, shared_items)
    shared_cards: list[ResolvedSkill] = []
    for item in shared_items:
        if item.bound_agent_id is not None:
            if not include_agent_bound:
                continue
            agent = bound_agents.get(item.bound_agent_id)
            if agent is None:
                continue
            shared_cards.append(_resolved_agent_bound_skill(item, agent))
            continue
        shared_cards.append(_resolved_shared_skill(item, shadowed_by_personal=item.slug in personal_slugs))
    return [*personal_cards, *shared_cards]


async def list_visible_skills_for_management(
    db: AsyncSession,
    user: User,
    *,
    include_agent_bound: bool = False,
) -> list[Skill]:
    """返回管理页可见 Skill：Agent 专属技能的可见性跟随绑定 Agent 权限。

    管理列表属于「普通 Skill 管理面」，默认不返回 Agent 专属技能；
    显式 include_agent_bound 才用于 self-skill 本身的读写入口。
    """
    repo = SkillRepository(db)
    items = await repo.list_all()
    bound_agents = await _bound_agents_by_agent_id(db, items)
    visible: list[Skill] = []
    seen: set[str] = set()
    for item in items:
        if item.slug in seen:
            continue
        if item.bound_agent_id is not None and not include_agent_bound:
            continue
        permission = await _effective_skill_permission(db, user, item, bound_agents)
        if permission == ResourcePermission.MANAGE or (item.enabled and permission != ResourcePermission.NONE):
            visible.append(item)
            seen.add(item.slug)
    return visible


async def list_skills(db: AsyncSession) -> list[Skill]:
    repo = SkillRepository(db)
    return await repo.list_all()


async def list_skill_slugs(db: AsyncSession, *, user: User | None = None) -> list[str]:
    """返回对外的 Skill slug 列表；Agent 专属技能不参与普通选择与依赖引用。"""
    if user is not None:
        return [
            item.slug
            for item in await _list_accessible_shared_skills(db, user)
            if isinstance(item.slug, str) and not is_agent_bound_skill(item)
        ]
    result = await db.execute(
        select(Skill.slug)
        .where(Skill.enabled.is_(True), Skill.bound_agent_id.is_(None))
        .order_by(Skill.updated_at.desc(), Skill.id.desc())
    )
    return [slug for slug in result.scalars().all() if isinstance(slug, str)]


async def get_skill_dependency_options(
    db: AsyncSession, user: User, slug: str | None = None
) -> dict[str, list[str] | list[dict]]:
    from yuxi.agents.toolkits.service import get_tool_metadata

    def get_tools():
        all_tools = get_tool_metadata()
        return [{"slug": tool["slug"], "name": tool.get("name", tool["slug"])} for tool in all_tools]

    skill_slugs, tool_list, mcp_names = await asyncio.gather(
        list_skill_slugs(db, user=user),
        asyncio.to_thread(get_tools),
        get_enabled_mcp_server_slugs(db=db),
    )
    if slug:
        skill_slugs = [item for item in skill_slugs if item != slug]

    return {
        "tools": tool_list,
        "mcps": mcp_names,
        "skills": skill_slugs,
    }


async def _list_accessible_shared_skills(
    db: AsyncSession,
    user: User,
    *,
    require_enabled: bool = True,
) -> list[Skill]:
    """按现有共享范围返回用户可访问的数据库 Skill。

    Agent 专属技能的权限派生自绑定 Agent：能访问 Agent 即可访问其专属技能。
    """
    repo = SkillRepository(db)
    items = await repo.list_enabled() if require_enabled else await repo.list_all()
    bound_agents = await _bound_agents_by_agent_id(db, items)
    return [
        item
        for item in items
        if await _effective_skill_permission(db, user, item, bound_agents) != ResourcePermission.NONE
    ]


async def _list_shared_skill_slugs(db: AsyncSession, user: User) -> list[str]:
    """返回依赖配置可引用的共享 Skill slug。"""
    return [item.slug for item in await _list_accessible_shared_skills(db, user) if isinstance(item.slug, str)]


def _get_all_tool_names() -> list[str]:
    """获取所有工具名称（包括 buildin 和其他来源）"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    all_tools = get_tool_metadata()
    return [tool["slug"] for tool in all_tools]


async def _validate_dependencies(
    *,
    parent: Skill,
    parent_share_config: dict | None = None,
    tool_dependencies: list[str],
    mcp_dependencies: list[str],
    skill_dependencies: list[str],
    available_skills: dict[str, Skill],
) -> tuple[list[str], list[str], list[str]]:
    tools = normalize_string_list(tool_dependencies)
    mcps = normalize_string_list(mcp_dependencies)
    skills = normalize_string_list(skill_dependencies)

    # 验证所有工具（不仅仅是 buildin）
    available_tools = set(_get_all_tool_names())
    invalid_tools = [name for name in tools if name not in available_tools]
    if invalid_tools:
        raise ValueError(f"存在无效工具依赖: {', '.join(invalid_tools)}")

    available_mcps = set(await get_enabled_mcp_server_slugs(db=None))
    invalid_mcps = [name for name in mcps if name not in available_mcps]
    if invalid_mcps:
        raise ValueError(f"存在无效 MCP 依赖: {', '.join(invalid_mcps)}")

    invalid_skills = [name for name in skills if name not in available_skills]
    if invalid_skills:
        raise ValueError(f"存在无效 skill 依赖: {', '.join(invalid_skills)}")

    if parent.slug in skills:
        raise ValueError("skill_dependencies 不允许包含自身")

    parent_config = (
        normalize_permission_config(parent_share_config)
        if parent_share_config is not None
        else normalize_permission_config(parent.share_config)
    )
    forbidden_skills = [
        name
        for name in skills
        if not _can_depend_on(
            parent_config=parent_config,
            parent_created_by=parent.created_by,
            parent_is_bound=is_agent_bound_skill(parent),
            dependency=available_skills[name],
        )
    ]
    if forbidden_skills:
        raise ValueError(f"存在权限范围不匹配的 skill 依赖: {', '.join(forbidden_skills)}")

    return tools, mcps, skills


async def update_skill_dependencies(
    db: AsyncSession,
    *,
    slug: str,
    tool_dependencies: list[str],
    mcp_dependencies: list[str],
    skill_dependencies: list[str],
    operator: User,
) -> Skill:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    _ensure_non_builtin(item)
    repo = SkillRepository(db)
    skill_items = await _list_accessible_shared_skills(db, operator)
    available_skills = {skill.slug: skill for skill in skill_items}
    parent_share_config = (
        normalize_permission_config((await resolve_agent_bound_permission_source(db, item)).share_config)
        if is_agent_bound_skill(item)
        else None
    )
    tools, mcps, skills = await _validate_dependencies(
        parent=item,
        parent_share_config=parent_share_config,
        tool_dependencies=tool_dependencies,
        mcp_dependencies=mcp_dependencies,
        skill_dependencies=skill_dependencies,
        available_skills=available_skills,
    )

    updated = await repo.update_dependencies(
        item,
        tool_dependencies=tools,
        mcp_dependencies=mcps,
        skill_dependencies=skills,
        updated_by=operator.uid,
    )
    await db.commit()
    return updated


def _validate_skill_slug_value(slug: str, *, field_name: str) -> str:
    slug = slug.strip()
    if not slug:
        raise ValueError(f"SKILL.md frontmatter 缺少 {field_name}")
    if len(slug) > 128:
        raise ValueError(f"SKILL.md frontmatter.{field_name} 长度不能超过 128")
    if not SKILL_NAME_PATTERN.match(slug):
        raise ValueError(f"SKILL.md frontmatter.{field_name} 必须是小写字母/数字/短横线，且不能连续短横线")
    return slug


def _validate_skill_display_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("SKILL.md frontmatter 缺少 name")
    if len(name) > 128:
        raise ValueError("SKILL.md frontmatter.name 长度不能超过 128")
    return name


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    frontmatter_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(line)
    else:
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    frontmatter_raw = "".join(frontmatter_lines)
    body = "".join(lines[body_start:])
    return frontmatter_raw, body


def _parse_skill_markdown(content: str) -> tuple[str, str, str, dict[str, Any]]:
    frontmatter_raw, _body = _split_frontmatter(content)
    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        raise ValueError(f"SKILL.md frontmatter YAML 解析失败: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")

    name = _validate_skill_display_name(str(data.get("name", "")))
    raw_slug = str(data.get("slug", "")).strip()
    slug = (
        _validate_skill_slug_value(raw_slug, field_name="slug")
        if raw_slug
        else _validate_skill_slug_value(name, field_name="name")
    )
    description = str(data.get("description", "")).strip()
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")

    return slug, name, description, data


def _rewrite_frontmatter_slug(content: str, new_slug: str) -> str:
    frontmatter_raw, body = _split_frontmatter(content)
    data = yaml.safe_load(frontmatter_raw)
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")
    if data.get("slug"):
        data["slug"] = new_slug
    else:
        data["name"] = new_slug
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n{body}"


def _validate_zip_paths(zip_file: zipfile.ZipFile) -> None:
    for name in zip_file.namelist():
        pure = PurePosixPath(name)
        if pure.is_absolute():
            raise ValueError(f"ZIP 包含不安全绝对路径: {name}")
        if ".." in pure.parts:
            raise ValueError(f"ZIP 包含路径穿越片段: {name}")


async def _generate_available_slug(repo: SkillRepository, base_slug: str) -> str:
    root = get_skills_root_dir()
    if not await repo.exists_slug(base_slug) and not (root / base_slug).exists():
        return base_slug

    idx = 2
    while True:
        candidate = f"{base_slug}-v{idx}"
        if not await repo.exists_slug(candidate) and not (root / candidate).exists():
            return candidate
        idx += 1


def parse_skill_dir_metadata(source_skill_dir: Path) -> dict[str, Any]:
    skill_md_path = source_skill_dir / "SKILL.md"
    if not skill_md_path.exists() or not skill_md_path.is_file():
        raise ValueError("技能目录缺少根级 SKILL.md")

    content = skill_md_path.read_text(encoding="utf-8")
    parsed_slug, parsed_name, parsed_desc, meta = _parse_skill_markdown(content)
    return {
        "slug": parsed_slug,
        "name": parsed_name,
        "description": parsed_desc,
        "tool_dependencies": normalize_string_list(meta.get("tool_dependencies")),
        "mcp_dependencies": normalize_string_list(meta.get("mcp_dependencies")),
        "skill_dependencies": normalize_string_list(meta.get("skill_dependencies")),
    }


def get_personal_skills_root_dir(uid: str) -> Path:
    """返回 UserWorkspace 内认证用户唯一的个人 Skill 目录。"""
    from yuxi.workspace.paths import user_workspace_dir

    return user_workspace_dir(uid) / "agents" / "skills"


def _personal_skills_root(uid: str) -> Path:
    """返回已创建且位于当前用户工作区内的个人 Skill 根。"""
    from yuxi.workspace.paths import ensure_user_workspace, user_workspace_dir

    ensure_user_workspace(uid)
    workspace_root = user_workspace_dir(uid).resolve()
    root = get_personal_skills_root_dir(uid)
    root.mkdir(parents=True, exist_ok=True)
    return ensure_within_root(root.resolve(), workspace_root, error_message="个人 Skill 路径越界")


def _resolve_personal_skill_dir(root: Path, slug: str) -> Path:
    """安全解析固定根下的个人 Skill 目录。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    target = root / slug
    if target.is_symlink():
        raise ValueError("个人 Skill 路径非法")
    return target


async def list_personal_skills(uid: str) -> list[ResolvedSkill]:
    """直接扫描个人 Skill 持久目录。"""
    return await asyncio.to_thread(_scan_personal_skills, uid)


async def install_personal_skill_dir(
    uid: str,
    source_dir: Path | str,
    *,
    expected_slug: str | None = None,
) -> ResolvedSkill:
    """将一个 Skill 原子安装到当前用户个人持久源。"""
    return await asyncio.to_thread(
        _install_personal_skill_dir_sync,
        uid,
        Path(source_dir),
        expected_slug=expected_slug,
    )


async def read_personal_skill_file(uid: str, slug: str, relative_path: str) -> dict[str, Any]:
    """读取个人 Skill 中的文本文件。"""
    skill_dir = _resolve_personal_skill_dir(_personal_skills_root(uid), slug)
    target, normalized_path = _resolve_relative_path(skill_dir, relative_path)
    if not target.is_file():
        raise ValueError("文件不存在")
    if not _is_text_path(target):
        raise ValueError("仅支持读取文本文件")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文件编码不支持（仅支持 UTF-8）") from exc
    return {"path": normalized_path, "content": content}


async def delete_personal_skill(uid: str, slug: str) -> None:
    """删除当前用户个人 Skill。"""
    skill_dir = _resolve_personal_skill_dir(_personal_skills_root(uid), slug)
    if not skill_dir.is_dir():
        raise ValueError("个人 Skill 不存在")
    await asyncio.to_thread(shutil.rmtree, skill_dir)


async def enable_personal_skills_for_agent_config(
    db: AsyncSession,
    *,
    thread_id: str,
    uid: str,
    skill_slugs: list[str],
) -> bool:
    """为显式 Skill 白名单追加个人 Skill；全部模式无需写入。"""
    from yuxi.repositories.agent_repository import AgentRepository
    from yuxi.repositories.conversation_repository import ConversationRepository

    conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
    if not conversation or str(conversation.uid) != str(uid):
        return False
    agent_repo = AgentRepository(db)
    agent = await agent_repo.get_by_slug(conversation.agent_id)
    if not agent or agent.created_by != str(uid):
        return False

    context = (agent.config_json or {}).get("context") or {}
    configured_skills = context.get("skills")
    if configured_skills is None:
        return True

    selected_skills = normalize_string_list(configured_skills if isinstance(configured_skills, list) else [])
    updated_skills = normalize_string_list([*selected_skills, *skill_slugs])
    if updated_skills == selected_skills:
        return True

    await agent_repo.update(
        agent,
        config_json={"context": {"skills": updated_skills}},
        config_resource_access={"skills": set(skill_slugs)},
        updated_by=str(uid),
    )
    return True


def _resolved_shared_skill(item: Skill, *, shadowed_by_personal: bool = False) -> ResolvedSkill:
    """将数据库 Skill 适配为统一的有效 Skill 描述。"""
    source_scope = "builtin" if is_builtin_skill(item) else "shared"
    return ResolvedSkill(
        id=item.id,
        slug=item.slug,
        name=item.name,
        description=item.description,
        source_type=item.source_type,
        source_scope=source_scope,
        source_dir=_resolve_skill_dir(item),
        enabled=bool(item.enabled),
        created_by=item.created_by,
        share_config=normalize_permission_config(
            item.share_config,
        ),
        tool_dependencies=normalize_string_list(item.tool_dependencies),
        mcp_dependencies=normalize_string_list(item.mcp_dependencies),
        skill_dependencies=normalize_string_list(item.skill_dependencies),
        version=item.version,
        content_hash=item.content_hash,
        bound_agent_id=item.bound_agent_id,
        shadowed_by_personal=shadowed_by_personal,
    )


def _resolved_agent_bound_skill(item: Skill, agent: Agent) -> ResolvedSkill:
    """将 Agent 专属技能适配为运行时视图。

    权限与身份从绑定 Agent 派生：share_config 是 Agent 共享配置的读时投影，
    不是本行可写的副本，因此 Agent 改共享范围会立即生效且不会漂移。
    运行时可见路径与普通共享 Skill 一致（/home/gem/skills/<slug>），
    需要出现在 uid 授权投影里，否则沙盒内路径不可读。
    """
    return ResolvedSkill(
        id=item.id,
        slug=item.slug,
        name=item.name,
        description=item.description,
        source_type=item.source_type,
        source_scope=AGENT_BOUND_SKILL_SOURCE_TYPE,
        source_dir=_resolve_skill_dir(item),
        enabled=bool(item.enabled),
        created_by=agent.created_by,
        share_config=normalize_permission_config(agent.share_config),
        tool_dependencies=normalize_string_list(item.tool_dependencies),
        mcp_dependencies=normalize_string_list(item.mcp_dependencies),
        skill_dependencies=normalize_string_list(item.skill_dependencies),
        version=item.version,
        content_hash=item.content_hash,
        bound_agent_id=item.bound_agent_id,
    )


def _resolved_personal_skill(uid: str, root: Path, metadata: dict[str, Any]) -> ResolvedSkill:
    """将个人目录元数据适配为不含共享语义的有效 Skill 描述。"""
    slug = str(metadata["slug"])
    if not is_valid_skill_slug(slug):
        raise ValueError("个人 Skill 包含非法 slug")
    source_dir = root / slug
    return ResolvedSkill(
        id=f"personal:{slug}",
        slug=slug,
        name=str(metadata["name"]),
        description=str(metadata["description"]),
        source_type=PERSONAL_SKILL_SOURCE_TYPE,
        source_scope=PERSONAL_SKILL_SOURCE_TYPE,
        source_dir=source_dir,
        enabled=True,
        created_by=uid,
        share_config=None,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )


def _scan_personal_skills(uid: str) -> list[ResolvedSkill]:
    """扫描并校验当前用户个人 Skill 的直接子目录。"""
    items: list[ResolvedSkill] = []
    root = _personal_skills_root(uid)
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_dir() or not is_valid_skill_slug(entry.name):
            logger.warning(f"跳过非法个人 Skill 目录: uid={uid}, name={entry.name}")
            continue
        if _dir_contains_symlink(entry):
            logger.warning(f"跳过包含符号链接的个人 Skill: uid={uid}, slug={entry.name}")
            continue
        try:
            metadata = parse_skill_dir_metadata(entry)
            if metadata["slug"] != entry.name:
                raise ValueError("目录名必须与 SKILL.md slug 一致")
            items.append(_resolved_personal_skill(uid, root, metadata))
        except Exception as exc:
            logger.warning(f"跳过无法解析的个人 Skill: uid={uid}, slug={entry.name}, error={exc}")
    return items


def _install_personal_skill_dir_sync(
    uid: str,
    source_dir: Path,
    *,
    expected_slug: str | None = None,
) -> ResolvedSkill:
    """将一个 Skill 原子复制到个人目录。"""
    source_dir = source_dir.resolve()
    root = _personal_skills_root(uid)
    temp_target = root / f".install.tmp-{uuid.uuid4().hex[:8]}"
    target_dir: Path | None = None
    try:
        metadata = _copy_skill_snapshot(source_dir, temp_target, expected_slug=expected_slug)
        slug = metadata["slug"]
        target_dir = root / slug
        if target_dir.exists() or target_dir.is_symlink():
            raise ValueError(f"个人 Skill 源已存在同名 Skill: {slug}")
        temp_target.rename(target_dir)
    except (FileExistsError, OSError) as exc:
        if target_dir is None or not target_dir.exists():
            raise
        raise ValueError(f"个人 Skill 源已存在同名 Skill: {slug}") from exc
    finally:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
    return _resolved_personal_skill(uid, root, metadata)


async def _stage_skill_draft_item(
    repo: SkillRepository,
    *,
    source_skill_dir: Path,
    draft_items_dir: Path,
) -> dict[str, Any]:
    item_id = uuid.uuid4().hex
    item_dir = draft_items_dir / item_id
    parsed = _copy_skill_snapshot(source_skill_dir, item_dir)
    final_slug = await _generate_available_slug(repo, parsed["slug"])
    return {
        "draft_item_id": item_id,
        "source_dir": f"items/{item_id}",
        "slug": final_slug,
        "name": parsed["name"],
        "original_name": parsed["slug"],
        "description": parsed["description"],
        "tool_dependencies": parsed["tool_dependencies"],
        "mcp_dependencies": parsed["mcp_dependencies"],
        "skill_dependencies": parsed["skill_dependencies"],
        "warnings": [f"原始 slug {parsed['slug']} 已存在，将安装为 {final_slug}"]
        if final_slug != parsed["slug"]
        else [],
        "success": True,
    }


def _build_default_share_payload(operator: User) -> dict[str, Any]:
    default_share_config = normalize_skill_share_config(
        None,
        operator_uid=operator.uid,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    return {
        "default_share_config": default_share_config,
        "allowed_access_levels": get_allowed_skill_access_levels(operator),
    }


def _resolve_skill_dir(item: Skill) -> Path:
    dir_path = Path(item.dir_path)
    if dir_path.is_absolute():
        return dir_path
    return (get_skill_data_dir() / dir_path).resolve()


def _resolve_relative_path(skill_dir: Path, relative_path: str, *, allow_root: bool = False) -> tuple[Path, str]:
    rel = (relative_path or "").strip().replace("\\", "/")
    rel = rel.lstrip("/")
    if not rel and not allow_root:
        raise ValueError("path 不能为空")
    pure = PurePosixPath(rel) if rel else PurePosixPath(".")
    if ".." in pure.parts:
        raise ValueError("非法路径：不允许上级路径引用")

    target = ensure_within_root((skill_dir / pure).resolve(), skill_dir, error_message="非法路径：越界访问被拒绝")

    return target, rel


def _is_text_path(path: Path) -> bool:
    if path.name == "SKILL.md":
        return True
    suffix = path.suffix.lower()
    return suffix in TEXT_FILE_EXTENSIONS


def _build_tree(path: Path, base_dir: Path) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = child.relative_to(base_dir).as_posix()
        if child.is_dir():
            children.append(
                {
                    "name": child.name,
                    "path": rel,
                    "is_dir": True,
                    "children": _build_tree(child, base_dir),
                }
            )
        else:
            children.append(
                {
                    "name": child.name,
                    "path": rel,
                    "is_dir": False,
                }
            )
    return children


async def prepare_skill_upload(
    db: AsyncSession,
    *,
    filename: str,
    file_bytes: bytes,
    operator: User,
) -> dict[str, Any]:
    normalized_filename = filename.lower()
    is_zip_upload = normalized_filename.endswith(".zip")
    is_skill_md_upload = normalized_filename.endswith("skill.md")
    if not is_zip_upload and not is_skill_md_upload:
        raise ValueError("仅支持上传 .zip 或 SKILL.md 文件")

    repo = SkillRepository(db)
    draft_dir = get_skill_drafts_root_dir() / str(uuid.uuid4())
    items_dir = draft_dir / "items"
    draft_dir.mkdir(parents=True, exist_ok=False)
    items_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix=".skill-prepare-", dir=str(get_skills_root_dir().parent)) as temp_root:
            extract_dir = Path(temp_root) / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            if is_zip_upload:
                zip_path = Path(temp_root) / "upload.zip"
                zip_path.write_bytes(file_bytes)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    _validate_zip_paths(zf)
                    zf.extractall(extract_dir)
                skill_md_files = list(extract_dir.rglob("SKILL.md"))
                if len(skill_md_files) != 1:
                    raise ValueError("ZIP 必须且只能包含一个技能（检测到一个 SKILL.md）")
                source_skill_dir = skill_md_files[0].parent
            else:
                source_skill_dir = extract_dir
                (source_skill_dir / "SKILL.md").write_bytes(file_bytes)

            item = await _stage_skill_draft_item(repo, source_skill_dir=source_skill_dir, draft_items_dir=items_dir)

        data = {
            "draft_id": draft_dir.name,
            "created_by": operator.uid,
            "source_type": "upload",
            "source": filename,
            "created_at": time.time(),
            "expires_at": time.time() + SKILL_DRAFT_TTL_SECONDS,
            "items": [item],
            **_build_default_share_payload(operator),
        }
        (draft_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise


async def prepare_remote_skill_install(
    db: AsyncSession,
    *,
    source: str,
    skills: list[str],
    operator: User,
) -> dict[str, Any]:
    from yuxi.agents.skills.remote_install import prepare_remote_skills_batch

    repo = SkillRepository(db)
    draft_dir = get_skill_drafts_root_dir() / str(uuid.uuid4())
    items_dir = draft_dir / "items"
    draft_dir.mkdir(parents=True, exist_ok=False)
    items_dir.mkdir(parents=True, exist_ok=True)

    preparation = None
    try:
        preparation = await prepare_remote_skills_batch(source=source, skills=skills)
        items: list[dict[str, Any]] = []
        for result in preparation.results:
            slug = result.get("slug", "")
            if not result.get("success"):
                item = {"slug": slug, "success": False, "error": result.get("error", "安装失败")}
                items.append(item)
                continue

            try:
                item = await _stage_skill_draft_item(
                    repo,
                    source_skill_dir=Path(result["source_dir"]),
                    draft_items_dir=items_dir,
                )
            except Exception as e:
                item = {"slug": slug, "success": False, "error": str(e)}
                items.append(item)
                continue

            items.append(item)

        data = {
            "draft_id": draft_dir.name,
            "created_by": operator.uid,
            "source_type": "remote",
            "source": source,
            "created_at": time.time(),
            "expires_at": time.time() + SKILL_DRAFT_TTL_SECONDS,
            "items": items,
            **_build_default_share_payload(operator),
        }
        (draft_dir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise
    finally:
        if preparation is not None:
            await preparation.cleanup()


async def confirm_skill_install_draft(
    db: AsyncSession,
    *,
    draft_id: str,
    share_config: dict | None,
    slugs: list[str] | None = None,
    operator: User,
) -> list[dict[str, Any]]:
    draft_dir, data, draft_items = _load_and_select_draft_items(draft_id, slugs, operator)
    source_type = data.get("source_type")

    normalized_share_config = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )

    repo = SkillRepository(db)
    skills_root = get_skills_root_dir()
    results: list[dict[str, Any]] = []

    for draft_item in draft_items:
        slug = str(draft_item.get("slug") or "").strip()
        if not draft_item.get("success", True):
            result = {"slug": slug, "success": False, "error": draft_item.get("error", "安装失败")}
            results.append(result)
            continue

        if not is_valid_skill_slug(slug):
            result = {"slug": slug, "success": False, "error": "无效 skill slug"}
            results.append(result)
            continue
        if await repo.exists_slug(slug) or (skills_root / slug).exists():
            result = {"slug": slug, "success": False, "error": "Skill slug 已被占用，请重新解析安装"}
            results.append(result)
            continue

        source_dir = (draft_dir / str(draft_item.get("source_dir", ""))).resolve()
        try:
            source_dir.relative_to(draft_dir.resolve())
        except ValueError:
            result = {"slug": slug, "success": False, "error": "安装草稿路径非法"}
            results.append(result)
            continue

        temp_target = skills_root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
        final_dir = skills_root / slug
        published = False
        try:
            parsed = _copy_skill_snapshot(source_dir, temp_target, final_slug=slug)
            if final_dir.exists():
                raise ValueError("Skill slug 已被占用，请重新解析安装")
            temp_target.rename(final_dir)
            published = True
            item = await repo.create(
                slug=slug,
                name=parsed["name"],
                description=parsed["description"],
                source_type=source_type,
                tool_dependencies=parsed["tool_dependencies"],
                mcp_dependencies=parsed["mcp_dependencies"],
                skill_dependencies=parsed["skill_dependencies"],
                dir_path=(Path("shared") / slug).as_posix(),
                share_config=normalized_share_config,
                enabled=True,
                created_by=operator.uid,
            )
            await db.commit()
            results.append({"slug": item.slug, "success": True, "skill": item.to_dict()})
        except Exception as e:
            await db.rollback()
            if published:
                shutil.rmtree(final_dir, ignore_errors=True)
            result = {"slug": slug, "success": False, "error": str(e)}
            results.append(result)
        finally:
            shutil.rmtree(temp_target, ignore_errors=True)

    if any(item.get("success") for item in results):
        shutil.rmtree(draft_dir, ignore_errors=True)
    return results


async def confirm_personal_skill_install_draft(
    *,
    draft_id: str,
    slugs: list[str] | None,
    operator: User,
) -> list[dict[str, Any]]:
    """确认草稿并将选中 Skill 安装到当前用户个人持久源。"""
    draft_dir, _data, draft_items = _load_and_select_draft_items(draft_id, slugs, operator)

    results: list[dict[str, Any]] = []
    for draft_item in draft_items:
        requested_slug = str(draft_item.get("slug") or "").strip()
        personal_slug = str(draft_item.get("original_name") or requested_slug).strip()
        if not draft_item.get("success", True):
            results.append(
                {
                    "slug": personal_slug,
                    "requested_slug": requested_slug,
                    "success": False,
                    "error": draft_item.get("error", "安装失败"),
                }
            )
            continue
        if not is_valid_skill_slug(personal_slug):
            results.append(
                {
                    "slug": personal_slug,
                    "requested_slug": requested_slug,
                    "success": False,
                    "error": "无效 skill slug",
                }
            )
            continue

        source_dir = (draft_dir / str(draft_item.get("source_dir", ""))).resolve()
        try:
            source_dir.relative_to(draft_dir.resolve())
            item = await install_personal_skill_dir(
                str(operator.uid),
                source_dir,
                expected_slug=personal_slug,
            )
            results.append(
                {
                    "slug": item.slug,
                    "requested_slug": requested_slug,
                    "success": True,
                    "skill": item.to_dict(),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "slug": personal_slug,
                    "requested_slug": requested_slug,
                    "success": False,
                    "error": str(exc),
                }
            )

    if any(item.get("success") for item in results):
        shutil.rmtree(draft_dir, ignore_errors=True)
    return results


async def discard_skill_install_draft(*, draft_id: str, operator: User) -> None:
    draft_dir, data = _load_skill_draft(draft_id)
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权删除该安装草稿")
    shutil.rmtree(draft_dir, ignore_errors=True)


async def get_skill_or_raise(db: AsyncSession, slug: str) -> Skill:
    slug = slug.strip() if isinstance(slug, str) else ""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")

    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    return item


async def get_management_readable_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    item = await get_skill_or_raise(db, slug)
    permission = await _effective_skill_permission(db, user, item)
    if permission == ResourcePermission.NONE:
        raise ValueError(f"技能 '{slug}' 不存在或无权访问")
    return item


async def get_manageable_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    item = await get_skill_or_raise(db, slug)
    permission = await _effective_skill_permission(db, user, item)
    if item.bound_agent_id is None and is_builtin_skill(item):
        permission = ResourcePermission.MANAGE if user.role in ADMIN_ROLES else permission
    if permission != ResourcePermission.MANAGE:
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    return item


async def get_skill_tree(db: AsyncSession, *, slug: str, operator: User) -> list[dict[str, Any]]:
    item = await get_management_readable_skill_or_raise(db, operator, slug)
    skill_dir = _resolve_skill_dir(item)
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise ValueError(f"技能目录不存在: {item.dir_path}")
    return _build_tree(skill_dir, skill_dir)


async def read_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    operator: User,
) -> dict[str, Any]:
    item = await get_management_readable_skill_or_raise(db, operator, slug)
    skill_dir = _resolve_skill_dir(item)
    target, rel = _resolve_relative_path(skill_dir, relative_path)
    if not target.exists() or not target.is_file():
        raise ValueError(f"文件不存在: {relative_path}")
    if not _is_text_path(target):
        raise ValueError("仅支持读取文本文件")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"文件编码不支持（仅支持 UTF-8）: {e}") from e
    return {"path": rel, "content": content}


async def create_skill_node(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    is_dir: bool,
    content: str | None,
    updated_by: str | None,
    operator: User,
) -> None:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    skill_dir = _resolve_skill_dir(item)
    target, _ = _resolve_relative_path(skill_dir, relative_path)
    if target.exists():
        raise ValueError("目标已存在")

    if is_dir:
        target.mkdir(parents=True, exist_ok=False)
        return

    if not _is_text_path(target):
        raise ValueError("仅支持创建文本文件")

    target.parent.mkdir(parents=True, exist_ok=True)

    # 先写入文件，再更新元数据
    target.write_text(content or "", encoding="utf-8")

    await _update_skill_metadata_if_skills_md(db, item, content or "", skill_dir, target, updated_by)
    await db.commit()


async def update_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    content: str,
    updated_by: str | None,
    operator: User,
) -> None:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    skill_dir = _resolve_skill_dir(item)
    target, _ = _resolve_relative_path(skill_dir, relative_path)
    if not target.exists() or not target.is_file():
        raise ValueError("文件不存在")
    if not _is_text_path(target):
        raise ValueError("仅支持编辑文本文件")

    await _update_skill_metadata_if_skills_md(db, item, content, skill_dir, target, updated_by)

    target.write_text(content, encoding="utf-8")
    await db.commit()


async def _update_skill_metadata_if_skills_md(
    db: AsyncSession,
    item: Skill,
    content: str,
    skill_dir: Path,
    target: Path,
    updated_by: str | None,
) -> None:
    """如果目标文件是 SKILL.md，则解析并更新元数据"""
    if target.name == "SKILL.md" and target.parent == skill_dir:
        parsed_slug, parsed_name, parsed_desc, _ = _parse_skill_markdown(content)
        if parsed_slug != item.slug:
            raise ValueError("SKILL.md frontmatter.slug 必须与 skill slug 一致")
        repo = SkillRepository(db)
        await repo.update_metadata(item, name=parsed_name, description=parsed_desc, updated_by=updated_by)


async def delete_skill_node(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    operator: User,
) -> None:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许直接修改文件")
    skill_dir = _resolve_skill_dir(item)
    target, rel = _resolve_relative_path(skill_dir, relative_path, allow_root=False)
    if not target.exists():
        raise ValueError("目标不存在")

    if rel == "SKILL.md":
        raise ValueError("不允许删除根目录 SKILL.md")

    if target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target)
    else:
        target.unlink()


async def export_skill_zip(db: AsyncSession, *, slug: str, operator: User) -> tuple[str, str]:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    skill_dir = _resolve_skill_dir(item)
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise ValueError("技能目录不存在")

    fd, export_path = tempfile.mkstemp(prefix=f"skill-{slug}-", suffix=".zip")
    Path(export_path).unlink(missing_ok=True)
    export_file = Path(export_path)
    try:
        with zipfile.ZipFile(export_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in skill_dir.rglob("*"):
                arcname = Path(slug) / p.relative_to(skill_dir)
                zf.write(p, arcname.as_posix())
    except Exception:
        export_file.unlink(missing_ok=True)
        raise
    return export_path, f"{slug}.zip"


async def delete_skill(db: AsyncSession, *, slug: str, operator: User) -> None:
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    if not await user_can_manage_skill_async(db, operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    _ensure_non_builtin(item)
    _ensure_not_agent_bound(item)

    skill_dir = _resolve_skill_dir(item)
    trash_dir: Path | None = None

    if skill_dir.exists():
        trash_dir = skill_dir.with_name(f".deleted-{slug}-{uuid.uuid4().hex[:8]}")
        skill_dir.rename(trash_dir)

    try:
        await repo.delete(item)
        await db.commit()
    except Exception:
        if trash_dir and trash_dir.exists():
            trash_dir.rename(skill_dir)
        raise

    if trash_dir and trash_dir.exists():
        await asyncio.to_thread(shutil.rmtree, trash_dir, ignore_errors=True)


async def delete_skills_batch(db: AsyncSession, *, slugs: list[str], operator: User) -> list[dict]:
    """批量删除多个 skills（单技能独立的子事务与回滚）。"""
    if len(slugs) > 50:
        raise ValueError("批量删除的技能数量不能超过 50 个")
    results = []
    for slug in slugs:
        try:
            await delete_skill(db, slug=slug, operator=operator)
            results.append({"slug": slug, "success": True})
        except Exception as e:
            if hasattr(db, "rollback"):
                await db.rollback()
            results.append({"slug": slug, "success": False, "error": str(e)})
    return results


async def update_skill_share_config(
    db: AsyncSession,
    *,
    slug: str,
    share_config: dict | None,
    operator: User,
) -> Skill:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    _ensure_non_builtin(item)
    _ensure_not_agent_bound(item)
    normalized = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=item.source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    repo = SkillRepository(db)
    updated = await repo.update_share_config(item, share_config=normalized, updated_by=operator.uid)
    await apply_skill_projection_policy_change(db, slug)
    return updated


async def update_skill_enabled(db: AsyncSession, *, slug: str, enabled: bool, operator: User) -> Skill:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    _ensure_not_agent_bound(item)
    repo = SkillRepository(db)
    updated = await repo.update_enabled(item, enabled=enabled, updated_by=operator.uid)
    await apply_skill_projection_policy_change(db, slug)
    return updated


def list_builtin_skill_specs() -> list[dict[str, Any]]:
    """发现源码目录中的 Skill，并以 frontmatter 作为唯一元数据。"""
    specs: list[dict[str, Any]] = []
    for source_dir in sorted(BUILTIN_SKILLS_DIR.iterdir()):
        if not source_dir.is_dir() or source_dir.name.startswith(("_", ".")):
            continue
        slug = source_dir.name
        skill_md = source_dir / "SKILL.md"
        if not skill_md.exists():
            raise ValueError(f"内置 skill 缺少 SKILL.md: {source_dir}")

        content = skill_md.read_text(encoding="utf-8")
        parsed_slug, parsed_name, parsed_desc, meta = _parse_skill_markdown(content)
        if parsed_slug != slug:
            raise ValueError(f"内置 skill frontmatter.slug 必须等于 slug: {slug}")

        specs.append(
            {
                "slug": slug,
                "name": parsed_name,
                "description": parsed_desc,
                "version": str(meta.get("version", "1.0.0")),
                "tool_dependencies": normalize_string_list(meta.get("tool_dependencies")),
                "mcp_dependencies": normalize_string_list(meta.get("mcp_dependencies")),
                "skill_dependencies": normalize_string_list(meta.get("skill_dependencies")),
                "content_hash": _compute_dir_hash(source_dir),
                "source_dir": source_dir,
            }
        )

    return specs


async def init_builtin_skills(db: AsyncSession, *, created_by: str = "system") -> list[Skill]:
    if db is not None and db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": SKILL_STORAGE_LOCK})

    repo = SkillRepository(db)
    synced_items: list[Skill] = []

    for spec in list_builtin_skill_specs():
        slug = spec["slug"]
        existing = await repo.get_by_slug(slug)
        if existing and not is_builtin_skill(existing):
            raise ValueError(f"内置 skill '{slug}' 与已存在的非内置 skill 冲突")

        target_dir = get_skills_root_dir() / slug
        _replace_skill_target(target_dir, Path(spec["source_dir"]))

        if existing:
            existing.dir_path = _build_builtin_skill_dir_path(slug)
            if existing.name != spec["name"] or existing.description != spec["description"]:
                await repo.update_metadata(
                    existing,
                    name=spec["name"],
                    description=spec["description"],
                    updated_by=created_by,
                )
            if (
                normalize_string_list(existing.tool_dependencies or []) != spec["tool_dependencies"]
                or normalize_string_list(existing.mcp_dependencies or []) != spec["mcp_dependencies"]
                or normalize_string_list(existing.skill_dependencies or []) != spec["skill_dependencies"]
            ):
                await repo.update_dependencies(
                    existing,
                    tool_dependencies=spec["tool_dependencies"],
                    mcp_dependencies=spec["mcp_dependencies"],
                    skill_dependencies=spec["skill_dependencies"],
                    updated_by=created_by,
                )
            synced_items.append(
                await repo.update_builtin_install(
                    existing,
                    version=spec["version"],
                    content_hash=spec["content_hash"],
                    updated_by=created_by,
                )
            )
            continue

        synced_items.append(
            await repo.create(
                slug=slug,
                name=spec["name"],
                description=spec["description"],
                source_type="builtin",
                tool_dependencies=spec["tool_dependencies"],
                mcp_dependencies=spec["mcp_dependencies"],
                skill_dependencies=spec["skill_dependencies"],
                dir_path=_build_builtin_skill_dir_path(slug),
                share_config=BUILTIN_SKILL_SHARE_CONFIG.copy(),
                enabled=True,
                version=spec["version"],
                content_hash=spec["content_hash"],
                created_by=created_by or BUILTIN_SKILL_OPERATOR,
            )
        )

    if db is not None:
        await db.commit()
    return synced_items


SELF_SKILL_TEMPLATE = """---
slug: {slug}
name: {name}
description: {description}
---

# {name}

{description}

"""


def _self_skill_slug(agent_slug: str) -> str:
    """按 Agent slug 派生稳定的 self-skill slug。"""
    base = f"{agent_slug}{SELF_SKILL_SLUG_SUFFIX}"
    candidate = base[:128]
    if not SKILL_NAME_PATTERN.match(candidate):
        raise ValueError(f"无法为智能体 '{agent_slug}' 生成合法的专属技能 slug")
    return candidate


async def create_agent_self_skill(db: AsyncSession, *, agent: Agent) -> Skill:
    """为 Agent 创建唯一的绑定 Skill；已存在时原样返回（幂等）。

    self-skill 不是 Agent 创建时的副产品：没有内容需求的 Agent 不该产生空行，
    运行时也允许 Agent 尚不存在绑定 Skill。
    """
    repo = SkillRepository(db)
    existing = await repo.get_by_bound_agent_id(agent.id)
    if existing is not None:
        return existing

    slug = _self_skill_slug(agent.slug)
    if await repo.exists_slug(slug):
        raise ValueError(f"专属技能 slug '{slug}' 已被占用")

    skill_dir = get_skills_root_dir() / slug
    skill_dir.mkdir(parents=True, exist_ok=False)
    description = agent.description or f"{agent.name} 的专属技能"
    (skill_dir / "SKILL.md").write_text(
        SELF_SKILL_TEMPLATE.format(slug=slug, name=agent.name, description=description),
        encoding="utf-8",
    )

    # 绑定 Skill 不维护独立共享配置；权限在读取时从绑定 Agent 派生。
    placeholder_share_config = {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }
    return await repo.create(
        slug=slug,
        name=agent.name,
        description=description,
        source_type="upload",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        dir_path=(Path("shared") / slug).as_posix(),
        share_config=placeholder_share_config,
        enabled=True,
        bound_agent_id=agent.id,
        created_by=agent.created_by,
    )


async def upload_agent_self_skill(
    db: AsyncSession,
    *,
    agent: Agent,
    filename: str,
    file_bytes: bytes,
) -> Skill:
    """创建或覆盖 Agent 专属技能内容。

    slug 固定按 Agent 派生并重写上传包 frontmatter，不使用包内 slug；
    ZIP 必须且只能包含一个技能。覆盖为整体替换：上传包之外的历史文件不保留。
    目录先换后写库，失败时回滚内容与事务，不留下半替换状态。
    """
    normalized_filename = filename.lower()
    is_zip_upload = normalized_filename.endswith(".zip")
    is_skill_md_upload = normalized_filename.endswith("skill.md")
    if not is_zip_upload and not is_skill_md_upload:
        raise ValueError("仅支持上传 .zip 或 SKILL.md 文件")

    repo = SkillRepository(db)
    existing = await repo.get_by_bound_agent_id(agent.id, for_update=True)
    if existing is None and await repo.exists_slug(_self_skill_slug(agent.slug)):
        raise ValueError("专属技能 slug 已被占用")
    slug = _self_skill_slug(agent.slug)

    skills_root = get_skills_root_dir()
    final_dir = skills_root / slug
    temp_target = skills_root / f".{slug}.upload-{uuid.uuid4().hex[:8]}"
    trash_dir = None
    replaced = False
    try:
        with tempfile.TemporaryDirectory(prefix=".self-skill-upload-", dir=str(skills_root.parent)) as temp_root:
            extract_dir = Path(temp_root) / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            if is_zip_upload:
                zip_path = Path(temp_root) / "upload.zip"
                zip_path.write_bytes(file_bytes)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    _validate_zip_paths(zf)
                    zf.extractall(extract_dir)
                skill_md_files = list(extract_dir.rglob("SKILL.md"))
                if len(skill_md_files) != 1:
                    raise ValueError("ZIP 必须且只能包含一个技能（检测到一个 SKILL.md）")
                source_dir = skill_md_files[0].parent
            else:
                source_dir = extract_dir
                (source_dir / "SKILL.md").write_bytes(file_bytes)

            parsed = _copy_skill_snapshot(source_dir, temp_target, final_slug=slug)

            if final_dir.exists():
                trash_dir = final_dir.with_name(f".deleted-{slug}-{uuid.uuid4().hex[:8]}")
                final_dir.rename(trash_dir)
                replaced = True
            temp_target.rename(final_dir)

        if existing is None:
            item = await repo.create(
                slug=slug,
                name=parsed["name"],
                description=parsed["description"],
                source_type="upload",
                tool_dependencies=parsed["tool_dependencies"],
                mcp_dependencies=parsed["mcp_dependencies"],
                skill_dependencies=parsed["skill_dependencies"],
                dir_path=(Path("shared") / slug).as_posix(),
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
                enabled=True,
                bound_agent_id=agent.id,
                created_by=agent.created_by,
            )
        else:
            item = await repo.update_metadata(
                existing,
                name=parsed["name"],
                description=parsed["description"],
                updated_by=agent.created_by,
            )
            item = await repo.update_dependencies(
                item,
                tool_dependencies=parsed["tool_dependencies"],
                mcp_dependencies=parsed["mcp_dependencies"],
                skill_dependencies=parsed["skill_dependencies"],
                updated_by=agent.created_by,
            )
        await db.commit()
    except Exception:
        await db.rollback()
        # 内容回滚：删掉新目录并放回旧目录，避免「行在、内容丢」或反向的半替换。
        if final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        if replaced and trash_dir is not None and trash_dir.exists():
            trash_dir.rename(final_dir)
        shutil.rmtree(temp_target, ignore_errors=True)
        raise
    finally:
        if trash_dir is not None and trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
    return item


async def get_agent_self_skill(db: AsyncSession, *, agent: Agent) -> Skill | None:
    """读取 Agent 的绑定 Skill，不存在时返回 None。"""
    return await SkillRepository(db).get_by_bound_agent_id(agent.id)


async def delete_agent_self_skill(db: AsyncSession, *, agent: Agent) -> tuple[Path, Path] | None:
    """删除 Agent 绑定 Skill 的行，并返回 (垃圾目录, 原目录)。

    不在此处提交：由 Agent 删除路径在同一事务落库，避免出现
    「专属技能已删、Agent 仍在」的部分状态。绑定 Skill 不存在时返回 None。
    self-skill 没有独立的删除入口，这里只服务 Agent 删除的级联。
    """
    repo = SkillRepository(db)
    item = await repo.get_by_bound_agent_id(agent.id, for_update=True)
    if item is None:
        return None

    skill_dir = _resolve_skill_dir(item)
    if not skill_dir.exists():
        await repo.delete(item)
        return None

    trash_dir = skill_dir.with_name(f".deleted-{item.slug}-{uuid.uuid4().hex[:8]}")
    skill_dir.rename(trash_dir)
    await repo.delete(item)
    return trash_dir, skill_dir


async def discard_trashed_skill_dir(trashed: tuple[Path, Path] | None) -> None:
    """提交成功后清理垃圾目录。"""
    if trashed is None:
        return
    trash_dir, _original = trashed
    if trash_dir.exists():
        await asyncio.to_thread(shutil.rmtree, trash_dir, ignore_errors=True)


async def restore_trashed_skill_dir(trashed: tuple[Path, Path] | None) -> None:
    """提交失败时把内容目录放回原位，避免留下「行在、内容丢」的绑定 Skill。"""
    if trashed is None:
        return
    trash_dir, original = trashed
    if trash_dir.exists() and not original.exists():
        trash_dir.rename(original)
