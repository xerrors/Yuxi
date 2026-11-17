"""专属 Skill 的历史记录与当前内容恢复。"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.access import get_manageable_skill_or_raise, get_management_readable_skill_or_raise
from yuxi.modules.extensions.skills.content import commit_skill_content, content_path, prune_skill_content
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import compute_skill_directory_hash, validated_shared_skill_parts
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User


async def list_skill_versions(db: AsyncSession, *, slug: str, operator: User) -> dict:
    """在内容共享锁内读取历史记录与当前完整修订。"""
    candidate = await get_management_readable_skill_or_raise(db, operator, slug)
    item = await SkillRepository(db).get_by_slug_for_read(candidate.slug)
    await get_management_readable_skill_or_raise(db, operator, slug)
    require_bound_skill(item)
    revision = (await asyncio.to_thread(compute_skill_directory_hash, content_path(item))).hex()
    rows = await SkillRepository(db).list_versions(item.id)
    return {"versions": [row.to_dict() for row in rows], "revision": revision}


async def release_skill_version(db: AsyncSession, *, slug: str, expected_revision: str, operator: User) -> dict:
    """为当前已保存内容建立历史记录。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    result = await commit_skill_content(db, item=item, operator=operator, expected_revision=expected_revision, release=True)
    return result.published_version


async def restore_skill_version(
    db: AsyncSession,
    *,
    slug: str,
    version: str,
    expected_revision: str,
    operator: User,
) -> dict:
    """校验历史包与当前内容契约后，提交当前内容引用。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    require_bound_skill(item)
    row = await SkillRepository(db).get_version(item.id, version)
    if row is None:
        raise ValueError("历史版本不存在")
    source = get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, row.dir_path))
    if (await asyncio.to_thread(compute_skill_directory_hash, source)).hex() != row.content_hash:
        raise ValueError("历史版本文件已损坏，无法恢复")
    result = await commit_skill_content(
        db,
        item=item,
        operator=operator,
        expected_revision=expected_revision,
        content_ref=source,
    )
    return {"skill": result.skill.to_dict(), "revision": result.revision}


async def delete_skill_version(db: AsyncSession, *, slug: str, version: str, operator: User) -> None:
    """删除历史记录；当前内容仍引用的文件继续保留。"""
    item = await get_manageable_skill_or_raise(db, operator, slug, for_update=True)
    require_bound_skill(item)
    row = await SkillRepository(db).get_version(item.id, version)
    if row is None:
        raise ValueError("历史版本不存在")
    await db.delete(row)
    await db.commit()
    await prune_skill_content(db, slug)


def require_bound_skill(item: Skill) -> None:
    """版本入口只接受 Agent 专属 Skill。"""
    if item.bound_agent_id is None:
        raise ValueError("只有 Agent 专属 Skill 支持历史版本")
