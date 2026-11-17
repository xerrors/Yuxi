"""共享 Skill 的安装确认、治理与删除用例。"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.access import (
    get_allowed_skill_access_levels,
    get_manageable_skill_or_raise,
    get_skills_root_dir,
    is_builtin_skill,
    normalize_skill_share_config,
    reject_bound_skill_policy_change,
    user_can_manage_skill,
)
from yuxi.modules.extensions.skills.content import commit_skill_content
from yuxi.modules.extensions.skills.install_draft import consume_installed_draft_items, load_and_select_draft_items
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.projection import (
    commit_skill_policy_and_refresh_projections,
    invalidate_skill_projections,
)
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions.resource_permission import ADMIN_ROLES
from yuxi.modules.identity.repositories.resource_grants import validate_shared_grants


async def confirm_skill_install_draft(
    db: AsyncSession,
    *,
    draft_id: str,
    share_config: dict | None,
    slugs: list[str] | None = None,
    operator: User,
) -> list[dict[str, Any]]:
    """按共享授权确认并发布 Skill 草稿。"""
    if operator.role not in ADMIN_ROLES or operator.is_deleted:
        raise ValueError("共享 Skill 建设需要管理员权限")
    draft_dir, data, draft_items = load_and_select_draft_items(draft_id, slugs, operator)
    source_type = data["source_type"]

    normalized_share_config = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )

    repo = SkillRepository(db)

    results: list[dict[str, Any]] = []
    for draft_item in draft_items:
        slug = draft_item.slug
        final_slug = await _generate_available_slug(repo, slug)
        try:
            normalized_share_config = await validate_shared_grants(db, normalized_share_config)
            item = Skill(
                slug=final_slug,
                source_type=source_type,
                share_config=normalized_share_config,
                enabled=True,
                created_by=operator.uid,
            )
            await commit_skill_content(
                db,
                item=item,
                operator=operator,
                expected_revision=None,
                source=draft_item.source_dir,
                source_slug=slug,
            )
            results.append({"slug": item.slug, "requested_slug": slug, "success": True, "skill": item.to_dict()})
        except Exception as exc:
            await db.rollback()
            results.append({"slug": slug, "success": False, "error": str(exc)})

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
    reject_bound_skill_policy_change(item)

    packages = get_skill_data_dir() / "packages" / slug
    owned = list(packages.iterdir()) if packages.is_dir() else []
    old_shared = get_skills_root_dir() / slug
    if old_shared.exists():
        owned.append(old_shared)
    await invalidate_skill_projections(db, slug)
    await repo.delete(item)
    await db.commit()
    # 只删除提交前已定位的内容目录，后续同 slug 安装的新目录不属于本次删除。
    for root in owned:
        try:
            await asyncio.to_thread(shutil.rmtree, root)
        except FileNotFoundError:
            pass  # 后续同 slug 提交可能已经清理无引用的旧包。


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
    reject_bound_skill_policy_change(item)
    normalized = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=item.source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    repo = SkillRepository(db)
    normalized = await validate_shared_grants(db, normalized)
    updated = await repo.update_share_config(item, share_config=normalized, updated_by=operator.uid)
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


async def update_skill_enabled(db: AsyncSession, *, slug: str, enabled: bool, operator: User) -> Skill:
    """更新共享 Skill 启用状态并刷新已有投影。"""
    item = await get_manageable_skill_or_raise(db, operator, slug)
    reject_bound_skill_policy_change(item)
    repo = SkillRepository(db)
    updated = await repo.update_enabled(item, enabled=enabled, updated_by=operator.uid)
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


def _ensure_non_builtin(item: Skill) -> None:
    """拒绝对内置 Skill 执行来源文件修改。"""
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许执行该操作")


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
