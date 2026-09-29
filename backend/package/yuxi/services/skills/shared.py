from __future__ import annotations

import asyncio
import hashlib
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.mcp.service import get_enabled_mcp_server_slugs
from yuxi.agents.skills.buildin import BUILTIN_SKILLS_DIR
from yuxi.config import get_skill_data_dir
from yuxi.permissions import ResourcePermission, normalize_permission_config, resolve_skill_permission
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.draft import consume_installed_draft_items, load_and_select_draft_items
from yuxi.services.skills.package import (
    copy_skill_snapshot,
    copy_skill_tree_no_symlinks,
    is_valid_skill_slug,
    normalize_string_list,
    parse_skill_markdown,
    validated_shared_skill_parts,
)
from yuxi.services.skills.projection import commit_skill_policy_and_refresh_projections
from yuxi.services.skills.resolved import ResolvedSkill
from yuxi.storage.postgres.models_business import Skill, User

BUILTIN_SKILL_OPERATOR = "builtin-system"
ADMIN_ROLES = {"admin", "superadmin"}
BUILTIN_SKILL_SHARE_CONFIG = {"access_level": "global", "department_ids": [], "user_uids": []}
SKILL_STORAGE_LOCK = 0x5958534B


async def get_skill_dependency_options(
    db: AsyncSession, user: User, slug: str | None = None
) -> dict[str, list[str] | list[dict]]:
    """返回当前 Skill 可选择的依赖项。"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    skill_items, tool_list, mcp_names = await asyncio.gather(
        SkillRepository(db).list_enabled_readable(user),
        asyncio.to_thread(get_tool_metadata),
        get_enabled_mcp_server_slugs(db=db),
    )
    skill_slugs = [item.slug for item in skill_items if isinstance(item.slug, str) and item.slug != slug]

    return {
        "tools": [{"slug": tool["slug"], "name": tool.get("name", tool["slug"])} for tool in tool_list],
        "mcps": mcp_names,
        "skills": skill_slugs,
    }


async def confirm_skill_install_draft(
    db: AsyncSession,
    *,
    draft_id: str,
    share_config: dict | None,
    slugs: list[str] | None = None,
    operator: User,
) -> list[dict[str, Any]]:
    """按共享授权确认并发布 Skill 草稿。"""
    draft_dir, data, draft_items = load_and_select_draft_items(draft_id, slugs, operator)
    source_type = data["source_type"]

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
        slug = draft_item.slug
        final_slug = await _generate_available_slug(repo, slug)

        temp_target = skills_root / f".{final_slug}.tmp-{uuid.uuid4().hex[:8]}"
        final_dir = skills_root / final_slug
        published = False
        try:
            parsed = copy_skill_snapshot(draft_item.source_dir, temp_target, expected_slug=slug, final_slug=final_slug)
            if final_dir.exists():
                raise ValueError("Skill slug 已被占用，请重新解析安装")
            temp_target.rename(final_dir)
            published = True
            item = await repo.create(
                slug=final_slug,
                name=parsed["name"],
                description=parsed["description"],
                source_type=source_type,
                tool_dependencies=parsed["tool_dependencies"],
                mcp_dependencies=parsed["mcp_dependencies"],
                skill_dependencies=parsed["skill_dependencies"],
                dir_path=(Path("shared") / final_slug).as_posix(),
                share_config=normalized_share_config,
                enabled=True,
                created_by=operator.uid,
            )
            await db.commit()
            results.append({"slug": item.slug, "requested_slug": slug, "success": True, "skill": item.to_dict()})
        except Exception as e:
            await db.rollback()
            if published:
                shutil.rmtree(final_dir, ignore_errors=True)
            result = {"slug": slug, "success": False, "error": str(e)}
            results.append(result)
        finally:
            shutil.rmtree(temp_target, ignore_errors=True)

    consume_installed_draft_items(draft_dir, data, {item["requested_slug"] for item in results if item["success"]})
    return results


async def delete_skill(db: AsyncSession, *, slug: str, operator: User) -> None:
    """删除一个可管理的共享 Skill。"""
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    if not user_can_manage_skill(operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    _ensure_non_builtin(item)

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
    """更新共享 Skill 授权并刷新已有投影。"""
    item = await get_manageable_skill_or_raise(db, operator, slug)
    _ensure_non_builtin(item)
    normalized = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=item.source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    repo = SkillRepository(db)
    updated = await repo.update_share_config(item, share_config=normalized, updated_by=operator.uid)
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


async def update_skill_enabled(db: AsyncSession, *, slug: str, enabled: bool, operator: User) -> Skill:
    """更新共享 Skill 启用状态并刷新已有投影。"""
    item = await get_manageable_skill_or_raise(db, operator, slug)
    repo = SkillRepository(db)
    updated = await repo.update_enabled(item, enabled=enabled, updated_by=operator.uid)
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


async def init_builtin_skills(db: AsyncSession, *, created_by: str = "system") -> list[Skill]:
    """将内置 Skill 定义同步到共享索引。"""
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
            existing.dir_path = (Path("shared") / slug).as_posix()
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
                dir_path=(Path("shared") / slug).as_posix(),
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


async def lock_accessible_shared_skill_for_file(db: AsyncSession, user: User, slug: str) -> Skill | None:
    """在读取共享源文件期间锁定一行并重新检查权限。"""
    repo = SkillRepository(db)
    visible = {item.slug for item in await repo.list_enabled_readable(user)}
    if slug not in visible:
        return None
    item = await repo.get_by_slug_for_read(slug)
    return item if item is not None and user_can_access_skill(user, item) else None


async def lock_accessible_shared_skills_for_runtime(
    db: AsyncSession,
    user: User,
    selected: list[str],
    *,
    shadowed_slugs: set[str] | None = None,
) -> list[Skill]:
    """只锁定已选共享 Skill 及其依赖，锁后重新检查权限。"""
    repo = SkillRepository(db)
    visible = {item.slug for item in await repo.list_enabled_readable(user)}
    shadowed = shadowed_slugs or set()
    pending = list(selected)
    locked: dict[str, Skill] = {}
    while pending:
        slug = pending.pop(0)
        if slug in shadowed or slug not in visible or slug in locked:
            continue
        item = await repo.get_by_slug_for_read(slug)
        if item is None or not user_can_access_skill(user, item):
            continue
        locked[slug] = item
        pending.extend(item.skill_dependencies or [])
    return list(locked.values())


async def validate_skill_dependencies(
    *,
    parent: Skill,
    tool_dependencies: list[str],
    mcp_dependencies: list[str],
    skill_dependencies: list[str],
    available_skills: dict[str, Skill],
) -> tuple[list[str], list[str], list[str]]:
    """校验工具、MCP 和共享 Skill 依赖。"""
    tools = normalize_string_list(tool_dependencies)
    mcps = normalize_string_list(mcp_dependencies)
    skills = normalize_string_list(skill_dependencies)

    # 验证所有工具（不仅仅是 buildin）
    from yuxi.agents.toolkits.service import get_tool_metadata

    available_tools = {tool["slug"] for tool in get_tool_metadata()}
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


async def get_skill_or_raise(db: AsyncSession, slug: str, *, for_update: bool = False) -> Skill:
    """读取指定共享 Skill，必要时加独占行锁。"""
    slug = slug.strip() if isinstance(slug, str) else ""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")

    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True) if for_update else await repo.get_by_slug(slug)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    return item


async def get_management_readable_skill_or_raise(db: AsyncSession, user: User, slug: str) -> Skill:
    """读取当前用户可查看的共享 Skill。"""
    item = await get_skill_or_raise(db, slug)
    if not user_can_manage_skill(user, item) and not user_can_access_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权访问")
    return item


async def get_manageable_skill_or_raise(db: AsyncSession, user: User, slug: str, *, for_update: bool = False) -> Skill:
    """读取当前用户可管理的共享 Skill。"""
    item = await get_skill_or_raise(db, slug, for_update=for_update)
    if not user_can_manage_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    return item


def resolved_shared_skill(item: Skill) -> ResolvedSkill:
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
    )


def can_skill_depend_on(parent: Skill, dependency: Skill) -> bool:
    """检查两个共享 Skill 的依赖授权是否兼容。"""
    if not dependency.enabled:
        return False
    if is_builtin_skill(dependency):
        return True

    dep_config = normalize_permission_config(dependency.share_config)
    parent_config = normalize_permission_config(parent.share_config)
    dependency_scopes = [scope for scope in (dep_config["read_scope"], dep_config["manage_scope"]) if scope]
    parent_scopes = [scope for scope in (parent_config["read_scope"], parent_config["manage_scope"]) if scope]
    owner_scope = {"access_level": "user", "department_ids": [], "user_uids": []}
    if not dependency_scopes:
        dependency_scopes = [{**owner_scope, "user_uids": [str(dependency.created_by or "")]}]
    if not parent_scopes:
        parent_scopes = [{**owner_scope, "user_uids": [str(parent.created_by or "")]}]
    return all(
        any(_scope_contains(dependency_scope, parent_scope) for dependency_scope in dependency_scopes)
        for parent_scope in parent_scopes
    )


def normalize_skill_share_config(
    share_config: dict | None,
    *,
    operator_uid: str,
    source_type: str = "upload",
    allowed_access_levels: set[str] | None = None,
) -> dict:
    """校验并标准化共享 Skill 的授权配置。"""
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


def get_allowed_skill_access_levels(user: User) -> list[str]:
    """返回操作人可设定的共享范围。"""
    if user.role in ADMIN_ROLES:
        return ["global", "department", "user"]
    return ["user"]


def user_can_access_skill(user: User, skill: Skill) -> bool:
    """检查用户是否可使用已启用的共享 Skill。"""
    if not skill.enabled:
        return False
    return resolve_skill_permission(user, skill) != ResourcePermission.NONE


def user_can_manage_skill(user: User, skill: Skill) -> bool:
    """检查用户是否可管理共享 Skill。"""
    if is_builtin_skill(skill):
        return user.role in ADMIN_ROLES
    return resolve_skill_permission(user, skill) == ResourcePermission.MANAGE


def is_builtin_skill(item: Skill | ResolvedSkill) -> bool:
    """判断共享 Skill 是否为内置来源。"""
    return item.source_type == "builtin"


def get_skills_root_dir() -> Path:
    """返回共享与内置 Skill 的持久源目录。"""
    root = get_skill_data_dir() / "shared"
    root.mkdir(parents=True, exist_ok=True)
    return root


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
        parsed_slug, parsed_name, parsed_desc, meta = parse_skill_markdown(content)
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
    """拒绝对内置 Skill 执行来源文件修改。"""
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许执行该操作")


def _compute_dir_hash(source_dir: Path) -> str:
    """计算目录树内容及执行位的摘要。"""
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
) -> None:
    """将 source_dir 复制到临时目录，再替换 target_dir。"""
    temp_target = target_dir.with_name(f".{target_dir.name}.tmp-{uuid.uuid4().hex[:8]}")
    trash_dir: Path | None = None
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)

    copy_skill_tree_no_symlinks(source_dir, temp_target)
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


async def _generate_available_slug(repo: SkillRepository, base_slug: str) -> str:
    """为共享来源分配未占用的 slug。"""
    root = get_skills_root_dir()
    if not await repo.exists_slug(base_slug) and not (root / base_slug).exists():
        return base_slug

    idx = 2
    while True:
        candidate = f"{base_slug}-v{idx}"
        if not await repo.exists_slug(candidate) and not (root / candidate).exists():
            return candidate
        idx += 1


def _resolve_skill_dir(item: Skill) -> Path:
    """将共享 Skill 数据库路径解析到持久根下。"""
    return get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, item.dir_path))
