from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi import config as sys_config
from yuxi.agents.mcp.service import get_enabled_mcp_server_slugs
from yuxi.agents.skills.repository import SkillRepository
from yuxi.storage.postgres.models_business import Skill, User
from yuxi.utils.logging_config import logger
from yuxi.utils.share_config import SHARE_ACCESS_LEVELS, normalize_share_config

SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_PATTERN = SKILL_SLUG_PATTERN

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
SKILL_SOURCE_TYPES = {"builtin", "upload", "remote"}
ACCESS_LEVELS = SHARE_ACCESS_LEVELS
ADMIN_ROLES = {"admin", "superadmin"}
DEFAULT_SKILL_SHARE_CONFIG = {"access_level": "user", "department_ids": [], "user_uids": []}
BUILTIN_SKILL_SHARE_CONFIG = {"access_level": "global", "department_ids": [], "user_uids": []}
DEPRECATED_BUILTIN_SKILL_SLUGS = {"standards-status-check"}
SKILL_DRAFT_TTL_SECONDS = 60 * 60
_THREAD_SKILLS_LOCK = threading.Lock()
_THREAD_SKILLS_LOCKS: dict[str, threading.Lock] = {}


def _get_thread_skills_lock(thread_id: str) -> threading.Lock:
    with _THREAD_SKILLS_LOCK:
        lock = _THREAD_SKILLS_LOCKS.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _THREAD_SKILLS_LOCKS[thread_id] = lock
        return lock


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


def get_allowed_skill_access_levels(user: User) -> list[str]:
    if user.role in ADMIN_ROLES:
        return ["global", "department", "user"]
    return ["user"]


def normalize_skill_share_config(
    share_config: dict | None,
    *,
    operator_uid: str,
    operator_department_id: int | str | None,
    source_type: str = "upload",
    allowed_access_levels: set[str] | None = None,
) -> dict:
    if source_type == "builtin":
        return BUILTIN_SKILL_SHARE_CONFIG.copy()

    return normalize_share_config(
        share_config,
        default_config=DEFAULT_SKILL_SHARE_CONFIG,
        default_access_level="user",
        invalid_access_level_message="无效的 Skill 权限等级",
        user_uid=operator_uid,
        department_id=operator_department_id,
        allowed_access_levels=allowed_access_levels,
        unauthorized_access_level_message="当前用户无权使用该 Skill 共享范围",
    )


def user_can_access_skill(user: User, skill: Skill, *, require_enabled: bool = True) -> bool:
    if require_enabled and not skill.enabled:
        return False
    if user.role == "superadmin":
        return True

    user_uid = str(user.uid or "")
    if user_uid and skill.created_by == user_uid:
        return True

    share_config = skill.share_config or DEFAULT_SKILL_SHARE_CONFIG.copy()
    access_level = share_config.get("access_level")
    if access_level == "global":
        return True
    if access_level == "department":
        if user.department_id is None:
            return False
        try:
            return int(user.department_id) in [int(value) for value in share_config.get("department_ids") or []]
        except (TypeError, ValueError):
            return False
    if access_level == "user":
        return bool(user_uid and user_uid in (share_config.get("user_uids") or []))
    return False


def user_can_manage_skill(user: User, skill: Skill) -> bool:
    if is_builtin_skill(skill):
        return user.role in ADMIN_ROLES
    return user.role in ADMIN_ROLES or skill.created_by == str(user.uid or "")


def can_skill_depend_on(parent: Skill, dependency: Skill) -> bool:
    if not dependency.enabled:
        return False
    if is_builtin_skill(dependency):
        return True

    dep_config = dependency.share_config or DEFAULT_SKILL_SHARE_CONFIG.copy()
    parent_config = parent.share_config or DEFAULT_SKILL_SHARE_CONFIG.copy()
    dep_level = dep_config.get("access_level")
    parent_level = parent_config.get("access_level")

    if dep_level == "global":
        return True
    if parent_level == "global":
        return False
    if parent_level == "department" and dep_level == "department":
        parent_ids = {int(value) for value in parent_config.get("department_ids") or []}
        dep_ids = {int(value) for value in dep_config.get("department_ids") or []}
        return parent_ids.issubset(dep_ids)
    if parent_level == "user" and dep_level == "user":
        parent_uids = {str(value) for value in parent_config.get("user_uids") or []}
        dep_uids = {str(value) for value in dep_config.get("user_uids") or []}
        return parent_uids.issubset(dep_uids)
    return False


def _ensure_non_builtin(item: Skill) -> None:
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许执行该操作")


def get_skills_root_dir() -> Path:
    root = Path(sys_config.save_dir) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_skill_drafts_root_dir() -> Path:
    root = Path(sys_config.save_dir) / "skill_import_drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cleanup_expired_skill_drafts() -> None:
    root = get_skill_drafts_root_dir()
    now = time.time()
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        metadata_path = entry / "metadata.json"
        try:
            if not metadata_path.exists() or now - entry.stat().st_mtime > SKILL_DRAFT_TTL_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
                continue
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if data.get("expires_at", 0) < now:
                shutil.rmtree(entry, ignore_errors=True)
        except Exception:
            shutil.rmtree(entry, ignore_errors=True)


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


def get_thread_skills_root_dir(thread_id: str) -> Path:
    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        raise ValueError("thread_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", safe_thread_id):
        raise ValueError("thread_id contains invalid characters")

    root = Path(sys_config.save_dir) / "threads" / safe_thread_id / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sync_thread_readable_skills(thread_id: str, selected_slugs: list[str] | None) -> Path:
    skills_root = get_skills_root_dir().resolve()
    thread_skills_root = get_thread_skills_root_dir(thread_id)
    normalized_slugs = [slug for slug in normalize_string_list(selected_slugs) if is_valid_skill_slug(slug)]
    readable_slugs = set(normalized_slugs)
    with _get_thread_skills_lock(thread_id):
        for entry in thread_skills_root.iterdir():
            if entry.name in readable_slugs:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

        for slug in normalized_slugs:
            source_dir = (skills_root / slug).resolve()
            target_dir = thread_skills_root / slug

            try:
                source_dir.relative_to(skills_root)
            except ValueError:
                continue
            if not source_dir.is_dir():
                if target_dir.exists() or target_dir.is_symlink():
                    if target_dir.is_dir() and not target_dir.is_symlink():
                        shutil.rmtree(target_dir)
                    else:
                        target_dir.unlink()
                continue

            if target_dir.exists():
                if target_dir.is_symlink():
                    target_dir.unlink()
                elif target_dir.is_dir():
                    if _dirs_equal(target_dir, source_dir):
                        continue
                    shutil.rmtree(target_dir)
                else:
                    target_dir.unlink()

            temp_target = thread_skills_root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
            try:
                shutil.copytree(source_dir, temp_target, symlinks=False)
                temp_target.rename(target_dir)
            finally:
                if temp_target.exists():
                    shutil.rmtree(temp_target, ignore_errors=True)

    return thread_skills_root


def get_builtin_skill_specs() -> list[Any]:
    from yuxi.agents.skills.buildin import BUILTIN_SKILLS

    return BUILTIN_SKILLS


def _build_builtin_skill_dir_path(slug: str) -> str:
    return (Path("skills") / slug).as_posix()


def _dirs_equal(dir1: Path, dir2: Path) -> bool:
    """检查两个目录内容是否相同（通过文件列表比较）"""
    if not dir1.exists() or not dir2.exists():
        return False
    list1 = sorted([f.relative_to(dir1) for f in dir1.rglob("*") if f.is_file()])
    list2 = sorted([f.relative_to(dir2) for f in dir2.rglob("*") if f.is_file()])
    return list1 == list2


def _compute_dir_hash(source_dir: Path) -> str:
    hasher = hashlib.sha256()
    file_paths = sorted(path for path in source_dir.rglob("*") if path.is_file())
    for file_path in file_paths:
        relative_path = file_path.relative_to(source_dir).as_posix()
        hasher.update(relative_path.encode("utf-8"))
        hasher.update(b"\0")
        with file_path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        hasher.update(b"\0")
    return hasher.hexdigest()


def _replace_skill_target(target_dir: Path, source_dir: Path) -> None:
    temp_target = target_dir.with_name(f".{target_dir.name}.tmp-{uuid.uuid4().hex[:8]}")
    trash_dir: Path | None = None
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)

    shutil.copytree(source_dir, temp_target, symlinks=False)
    try:
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
) -> list[Skill]:
    repo = SkillRepository(db)
    items = await repo.list_enabled() if require_enabled else await repo.list_all()
    return [item for item in items if user_can_access_skill(user, item, require_enabled=require_enabled)]


async def list_manageable_skills(db: AsyncSession, user: User) -> list[Skill]:
    repo = SkillRepository(db)
    return [item for item in await repo.list_all() if user_can_manage_skill(user, item)]


async def list_visible_skills_for_management(db: AsyncSession, user: User) -> list[Skill]:
    repo = SkillRepository(db)
    visible: list[Skill] = []
    seen: set[str] = set()
    for item in await repo.list_all():
        if item.slug in seen:
            continue
        if user_can_manage_skill(user, item) or (item.enabled and user_can_access_skill(user, item)):
            visible.append(item)
            seen.add(item.slug)
    return visible


async def list_skills(db: AsyncSession) -> list[Skill]:
    repo = SkillRepository(db)
    return await repo.list_all()


async def list_skill_slugs(db: AsyncSession, *, user: User | None = None) -> list[str]:
    if user is not None:
        return [item.slug for item in await list_accessible_skills(db, user) if isinstance(item.slug, str)]
    result = await db.execute(
        select(Skill.slug).where(Skill.enabled.is_(True)).order_by(Skill.updated_at.desc(), Skill.id.desc())
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


def _get_all_tool_names() -> list[str]:
    """获取所有工具名称（包括 buildin 和其他来源）"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    all_tools = get_tool_metadata()
    return [tool["slug"] for tool in all_tools]


async def _validate_dependencies(
    *,
    parent: Skill,
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

    forbidden_skills = [name for name in skills if not can_skill_depend_on(parent, available_skills[name])]
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
    skill_items = await list_accessible_skills(db, operator)
    available_skills = {skill.slug: skill for skill in skill_items}
    tools, mcps, skills = await _validate_dependencies(
        parent=item,
        tool_dependencies=tool_dependencies,
        mcp_dependencies=mcp_dependencies,
        skill_dependencies=skill_dependencies,
        available_skills=available_skills,
    )

    return await repo.update_dependencies(
        item,
        tool_dependencies=tools,
        mcp_dependencies=mcps,
        skill_dependencies=skills,
        updated_by=operator.uid,
    )


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


def _parse_skill_dir_metadata(source_skill_dir: Path) -> dict[str, Any]:
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


async def _stage_skill_draft_item(
    repo: SkillRepository,
    *,
    source_skill_dir: Path,
    draft_items_dir: Path,
) -> dict[str, Any]:
    item_id = uuid.uuid4().hex
    item_dir = draft_items_dir / item_id
    shutil.copytree(source_skill_dir, item_dir, symlinks=False)
    parsed = _parse_skill_dir_metadata(item_dir)
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
        operator_department_id=operator.department_id,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    return {
        "default_share_config": default_share_config,
        "allowed_access_levels": get_allowed_skill_access_levels(operator),
    }


async def _import_skill_dir_impl(
    db: AsyncSession,
    *,
    source_skill_dir: Path,
    created_by: str | None,
    source_type: str,
    share_config: dict,
) -> Skill:
    repo = SkillRepository(db)
    skills_root = get_skills_root_dir()
    parsed = _parse_skill_dir_metadata(source_skill_dir)
    final_slug = await _generate_available_slug(repo, parsed["slug"])
    with tempfile.TemporaryDirectory(prefix=".skill-import-", dir=str(skills_root.parent)) as temp_root:
        stage_dir = Path(temp_root) / "stage"
        shutil.copytree(source_skill_dir, stage_dir)

        if final_slug != parsed["slug"]:
            content = (stage_dir / "SKILL.md").read_text(encoding="utf-8")
            (stage_dir / "SKILL.md").write_text(_rewrite_frontmatter_slug(content, final_slug), encoding="utf-8")

        temp_target = skills_root / f".{final_slug}.tmp-{uuid.uuid4().hex[:8]}"
        if temp_target.exists():
            await asyncio.to_thread(shutil.rmtree, temp_target)
        shutil.move(str(stage_dir), str(temp_target))

        final_dir = skills_root / final_slug
        if final_dir.exists():
            await asyncio.to_thread(shutil.rmtree, temp_target, ignore_errors=True)
            raise ValueError(f"技能目录冲突，请重试: {final_slug}")
        temp_target.rename(final_dir)

        try:
            item = await repo.create(
                slug=final_slug,
                name=parsed["name"],
                description=parsed["description"],
                source_type=source_type,
                tool_dependencies=parsed["tool_dependencies"],
                mcp_dependencies=parsed["mcp_dependencies"],
                skill_dependencies=parsed["skill_dependencies"],
                dir_path=(Path("skills") / final_slug).as_posix(),
                share_config=share_config,
                enabled=True,
                created_by=created_by,
            )
        except Exception:
            await asyncio.to_thread(shutil.rmtree, final_dir, ignore_errors=True)
            raise

    return item


def _resolve_skill_dir(item: Skill) -> Path:
    dir_path = Path(item.dir_path)
    if dir_path.is_absolute():
        return dir_path
    return (Path(sys_config.save_dir) / dir_path).resolve()


def _resolve_relative_path(skill_dir: Path, relative_path: str, *, allow_root: bool = False) -> tuple[Path, str]:
    rel = (relative_path or "").strip().replace("\\", "/")
    rel = rel.lstrip("/")
    if not rel and not allow_root:
        raise ValueError("path 不能为空")
    pure = PurePosixPath(rel) if rel else PurePosixPath(".")
    if ".." in pure.parts:
        raise ValueError("非法路径：不允许上级路径引用")

    target = (skill_dir / pure).resolve()
    try:
        target.relative_to(skill_dir)
    except ValueError:
        raise ValueError("非法路径：越界访问被拒绝") from None

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
            if not result.get("success"):
                items.append(
                    {"slug": result.get("slug", ""), "success": False, "error": result.get("error", "安装失败")}
                )
                continue
            item = await _stage_skill_draft_item(
                repo,
                source_skill_dir=Path(result["source_dir"]),
                draft_items_dir=items_dir,
            )
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
    operator: User,
) -> list[dict[str, Any]]:
    draft_dir, data = _load_skill_draft(draft_id)
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权确认该安装草稿")

    source_type = data.get("source_type")
    if source_type not in {"upload", "remote"}:
        raise ValueError("无效的安装草稿来源")

    normalized_share_config = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        operator_department_id=operator.department_id,
        source_type=source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )

    repo = SkillRepository(db)
    skills_root = get_skills_root_dir()
    results: list[dict[str, Any]] = []

    for draft_item in data.get("items") or []:
        if not draft_item.get("success", True):
            results.append(
                {"slug": draft_item.get("slug", ""), "success": False, "error": draft_item.get("error", "安装失败")}
            )
            continue

        slug = str(draft_item.get("slug") or "").strip()
        if not is_valid_skill_slug(slug):
            results.append({"slug": slug, "success": False, "error": "无效 skill slug"})
            continue
        if await repo.exists_slug(slug) or (skills_root / slug).exists():
            results.append({"slug": slug, "success": False, "error": "Skill slug 已被占用，请重新解析安装"})
            continue

        source_dir = (draft_dir / str(draft_item.get("source_dir", ""))).resolve()
        try:
            source_dir.relative_to(draft_dir.resolve())
        except ValueError:
            results.append({"slug": slug, "success": False, "error": "安装草稿路径非法"})
            continue

        try:
            parsed = _parse_skill_dir_metadata(source_dir)
            with tempfile.TemporaryDirectory(prefix=".skill-confirm-", dir=str(skills_root.parent)) as temp_root:
                stage_dir = Path(temp_root) / "stage"
                shutil.copytree(source_dir, stage_dir)
                if parsed["slug"] != slug:
                    content = (stage_dir / "SKILL.md").read_text(encoding="utf-8")
                    (stage_dir / "SKILL.md").write_text(_rewrite_frontmatter_slug(content, slug), encoding="utf-8")

                temp_target = skills_root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
                shutil.move(str(stage_dir), str(temp_target))
                final_dir = skills_root / slug
                if final_dir.exists():
                    shutil.rmtree(temp_target, ignore_errors=True)
                    results.append({"slug": slug, "success": False, "error": "Skill slug 已被占用，请重新解析安装"})
                    continue
                temp_target.rename(final_dir)

                try:
                    item = await repo.create(
                        slug=slug,
                        name=parsed["name"],
                        description=parsed["description"],
                        source_type=source_type,
                        tool_dependencies=parsed["tool_dependencies"],
                        mcp_dependencies=parsed["mcp_dependencies"],
                        skill_dependencies=parsed["skill_dependencies"],
                        dir_path=(Path("skills") / slug).as_posix(),
                        share_config=normalized_share_config,
                        enabled=True,
                        created_by=operator.uid,
                    )
                    results.append({"slug": item.slug, "success": True, "skill": item.to_dict()})
                except Exception:
                    shutil.rmtree(final_dir, ignore_errors=True)
                    raise
        except Exception as e:
            if hasattr(db, "rollback"):
                await db.rollback()
            results.append({"slug": slug, "success": False, "error": str(e)})

    if any(item.get("success") for item in results):
        shutil.rmtree(draft_dir, ignore_errors=True)
    return results


async def discard_skill_install_draft(*, draft_id: str, operator: User) -> None:
    draft_dir, data = _load_skill_draft(draft_id)
    if data.get("created_by") != operator.uid and operator.role not in ADMIN_ROLES:
        raise ValueError("无权删除该安装草稿")
    shutil.rmtree(draft_dir, ignore_errors=True)


async def import_skill_dir(
    db: AsyncSession,
    *,
    source_dir: Path | str,
    created_by: str | None,
    source_type: str = "upload",
    share_config: dict | None = None,
) -> Skill:
    source_skill_dir = Path(source_dir).resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    if not source_skill_dir.is_relative_to(tmp_root):
        raise ValueError("技能目录路径不合法")
    if not source_skill_dir.exists() or not source_skill_dir.is_dir():
        raise ValueError("技能目录不存在")
    return await _import_skill_dir_impl(
        db,
        source_skill_dir=source_skill_dir,
        created_by=created_by,
        source_type=source_type,
        share_config=share_config or DEFAULT_SKILL_SHARE_CONFIG.copy(),
    )


async def get_skill_or_raise(db: AsyncSession, slug: str) -> Skill:
    slug = slug.strip() if isinstance(slug, str) else ""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")

    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    return item


async def get_accessible_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    item = await get_skill_or_raise(db, slug)
    if not user_can_access_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权访问")
    return item


async def get_management_readable_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    item = await get_skill_or_raise(db, slug)
    if not user_can_manage_skill(user, item) and not user_can_access_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权访问")
    return item


async def get_manageable_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    item = await get_skill_or_raise(db, slug)
    if not user_can_manage_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    return item


async def get_skill_tree(db: AsyncSession, slug: str) -> list[dict[str, Any]]:
    item = await get_skill_or_raise(db, slug)
    skill_dir = _resolve_skill_dir(item)
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise ValueError(f"技能目录不存在: {item.dir_path}")
    return _build_tree(skill_dir, skill_dir)


async def read_skill_file(db: AsyncSession, slug: str, relative_path: str) -> dict[str, Any]:
    item = await get_skill_or_raise(db, slug)
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
) -> None:
    item = await get_skill_or_raise(db, slug)
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


async def update_skill_file(
    db: AsyncSession,
    *,
    slug: str,
    relative_path: str,
    content: str,
    updated_by: str | None,
) -> None:
    item = await get_skill_or_raise(db, slug)
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


async def delete_skill_node(db: AsyncSession, *, slug: str, relative_path: str) -> None:
    item = await get_skill_or_raise(db, slug)
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


async def export_skill_zip(db: AsyncSession, slug: str) -> tuple[str, str]:
    item = await get_skill_or_raise(db, slug)
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


async def delete_skill(db: AsyncSession, *, slug: str) -> None:
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    _ensure_non_builtin(item)

    skill_dir = _resolve_skill_dir(item)
    trash_dir: Path | None = None

    if skill_dir.exists():
        trash_dir = skill_dir.with_name(f".deleted-{slug}-{uuid.uuid4().hex[:8]}")
        skill_dir.rename(trash_dir)

    try:
        await repo.delete(item)
    except Exception:
        if trash_dir and trash_dir.exists():
            trash_dir.rename(skill_dir)
        raise

    if trash_dir and trash_dir.exists():
        await asyncio.to_thread(shutil.rmtree, trash_dir, ignore_errors=True)


async def delete_skills_batch(db: AsyncSession, *, slugs: list[str]) -> list[dict]:
    """批量删除多个 skills（单技能独立的子事务与回滚）。"""
    if len(slugs) > 50:
        raise ValueError("批量删除的技能数量不能超过 50 个")
    results = []
    for slug in slugs:
        try:
            await delete_skill(db, slug=slug)
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
    normalized = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        operator_department_id=operator.department_id,
        source_type=item.source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    return await SkillRepository(db).update_share_config(item, share_config=normalized, updated_by=operator.uid)


async def update_skill_enabled(db: AsyncSession, *, slug: str, enabled: bool, operator: User) -> Skill:
    item = await get_manageable_skill_or_raise(db, operator, slug)
    return await SkillRepository(db).update_enabled(item, enabled=enabled, updated_by=operator.uid)


def list_builtin_skill_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for raw_spec in get_builtin_skill_specs():
        slug = str(getattr(raw_spec, "slug", "")).strip()
        source_dir = Path(str(getattr(raw_spec, "source_dir", ""))).resolve()
        configured_description = str(getattr(raw_spec, "description", "")).strip()
        version = str(getattr(raw_spec, "version", "1.0.0")).strip() or "1.0.0"
        configured_tools = normalize_string_list(getattr(raw_spec, "tool_dependencies", None))
        configured_mcps = normalize_string_list(getattr(raw_spec, "mcp_dependencies", None))
        configured_skills = normalize_string_list(getattr(raw_spec, "skill_dependencies", None))

        if not is_valid_skill_slug(slug):
            raise ValueError(f"内置 skill slug 非法: {slug}")
        if not source_dir.exists() or not source_dir.is_dir():
            logger.warning(f"跳过不存在的内置 skill 目录: {source_dir}")
            continue

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
                "description": configured_description or parsed_desc,
                "version": version,
                "tool_dependencies": configured_tools or normalize_string_list(meta.get("tool_dependencies")),
                "mcp_dependencies": configured_mcps or normalize_string_list(meta.get("mcp_dependencies")),
                "skill_dependencies": configured_skills or normalize_string_list(meta.get("skill_dependencies")),
                "content_hash": _compute_dir_hash(source_dir),
                "source_dir": source_dir,
            }
        )

    return specs


async def init_builtin_skills(db: AsyncSession, *, created_by: str = "system") -> list[Skill]:
    repo = SkillRepository(db)
    synced_items: list[Skill] = []

    for slug in DEPRECATED_BUILTIN_SKILL_SLUGS:
        existing = await repo.get_by_slug(slug)
        if not existing or not is_builtin_skill(existing):
            continue

        skill_dir = _resolve_skill_dir(existing)
        if skill_dir.exists():
            await asyncio.to_thread(shutil.rmtree, skill_dir, ignore_errors=True)
        await repo.delete(existing)

    for spec in list_builtin_skill_specs():
        slug = spec["slug"]
        existing = await repo.get_by_slug(slug)
        if existing and not is_builtin_skill(existing):
            raise ValueError(f"内置 skill '{slug}' 与已存在的非内置 skill 冲突")

        target_dir = get_skills_root_dir() / slug
        _replace_skill_target(target_dir, Path(spec["source_dir"]))

        if existing:
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

    return synced_items
