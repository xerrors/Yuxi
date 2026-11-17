"""共享与专属 Skill 的权限及带锁读取契约。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import is_valid_skill_slug, validated_shared_skill_parts
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.extensions.skills.resolved import ResolvedSkill
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, normalize_permission_config, resolve_skill_permission
from yuxi.modules.identity.permissions.resource_permission import ADMIN_ROLES

BUILTIN_SKILL_SHARE_CONFIG = {"access_level": "global", "department_ids": [], "user_uids": []}


async def lock_accessible_shared_skill_for_file(db: AsyncSession, user: User, slug: str) -> Skill | None:
    """在读取共享源文件期间锁定一行并重新检查权限。"""
    repo = SkillRepository(db)
    item = await repo.get_by_slug_for_read(slug)
    return item if item is not None and user_can_access_skill(user, item) else None


async def lock_accessible_shared_skills_for_runtime(
    db: AsyncSession,
    user: User,
    selected: list[str],
    *,
    shadowed_slugs: set[str] | None = None,
    bound_slug: str | None = None,
) -> list[Skill]:
    """只锁定已选共享 Skill，锁后重新检查权限。"""
    repo = SkillRepository(db)
    shadowed = shadowed_slugs or set()
    locked: dict[str, Skill] = {}
    for slug in selected:
        if slug in shadowed or slug in locked:
            continue
        candidate = await repo.get_by_slug(slug)
        if candidate is None or (candidate.bound_agent_id is not None and slug != bound_slug) or not user_can_access_skill(user, candidate):
            continue
        item = await repo.get_by_slug_for_read(slug)
        if item is None or (item.bound_agent_id is not None and slug != bound_slug) or not user_can_access_skill(user, item):
            continue
        locked[slug] = item
    return list(locked.values())


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
    candidate = await get_skill_or_raise(db, slug)
    if candidate.bound_agent_id is not None:
        from sqlalchemy import select

        from yuxi.modules.agents.models.definitions import Agent
        from yuxi.modules.identity.repositories.users import UserRepository

        await db.scalar(select(Agent).where(Agent.id == candidate.bound_agent_id).with_for_update(read=True))
        user = await UserRepository(db).lock_active_human(user.uid)
        if user is None:
            raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    item = await get_skill_or_raise(db, slug, for_update=for_update)
    if not user_can_manage_skill(user, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    return item


def resolved_shared_skill(item: Skill) -> ResolvedSkill:
    """将数据库 Skill 适配为统一的有效 Skill 描述。"""
    source_scope = "agent_bound" if item.bound_agent_id is not None else ("builtin" if is_builtin_skill(item) else "shared")
    try:
        share_config = {} if item.bound_agent_id is not None else normalize_permission_config(item.share_config)
    except (TypeError, ValueError) as exc:
        # 可见性仍由 repository/resolver 校验；保留坏配置供管理者修复。
        share_config = item.share_config
        logger.warning("Invalid skill share config exposed for repair: slug={}, error={}", item.slug, str(exc))
    return ResolvedSkill(
        id=item.id,
        slug=item.slug,
        name=item.name,
        description=item.description,
        source_type=item.source_type,
        source_scope=source_scope,
        bound_agent_id=item.bound_agent_id,
        bound_agent=item.bound_agent if item.bound_agent_id is not None else None,
        source_dir=_resolve_skill_dir(item),
        enabled=bool(item.enabled),
        created_by=item.created_by,
        share_config=share_config,
        version=item.version,
        content_hash=item.content_hash,
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


def reject_bound_skill_policy_change(item: Skill) -> None:
    """绑定资产的授权、启停与生命周期只能由 Agent 管理。"""
    if item.bound_agent_id is not None:
        raise ValueError("专属 Skill 的权限和生命周期跟随智能体")


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
    return resolve_skill_permission(user, skill) == ResourcePermission.MANAGE


def is_builtin_skill(item: Skill | ResolvedSkill) -> bool:
    """判断共享 Skill 是否为内置来源。"""
    return item.source_type == "builtin"


def get_skills_root_dir() -> Path:
    """返回共享与内置 Skill 的持久源目录。"""
    root = get_skill_data_dir() / "shared"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_skill_dir(item: Skill) -> Path:
    """将共享 Skill 数据库路径解析到持久根下。"""
    return get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, item.dir_path))
