"""面向用户的个人与共享 Skill 组合查询。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.personal import list_personal_skills
from yuxi.services.skills.resolved import ResolvedSkill
from yuxi.services.skills.shared import resolved_shared_skill
from yuxi.storage.postgres.models_business import User


async def list_accessible_skills(
    db: AsyncSession,
    user: User,
) -> list[ResolvedSkill]:
    """返回当前用户最终生效的共享与个人 Skill。"""
    shared_items, personal_items = await asyncio.gather(
        SkillRepository(db).list_enabled_readable(user),
        list_personal_skills(str(user.uid)),
    )
    effective = {item.slug: resolved_shared_skill(item) for item in shared_items}
    for item in personal_items:
        effective[item.slug] = replace(item, overrides_shared=item.slug in effective)
    return list(effective.values())


async def list_skill_cards_for_user(
    db: AsyncSession,
    user: User,
) -> list[ResolvedSkill]:
    """返回管理页所需的共享与个人 Skill 卡片。"""
    shared_items, personal_items = await asyncio.gather(
        SkillRepository(db).list_visible_for_management(user),
        list_personal_skills(str(user.uid)),
    )
    personal_slugs = {item.slug for item in personal_items}
    shared_slugs = {item.slug for item in shared_items}

    personal_cards = [replace(item, overrides_shared=item.slug in shared_slugs) for item in personal_items]
    shared_cards = [
        replace(resolved_shared_skill(item), shadowed_by_personal=item.slug in personal_slugs) for item in shared_items
    ]
    return [*personal_cards, *shared_cards]
