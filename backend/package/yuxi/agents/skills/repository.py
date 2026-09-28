from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import Skill
from yuxi.utils.datetime_utils import utc_now_naive


class SkillRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_all(self, *, for_share: bool = False) -> list[Skill]:
        stmt = select(Skill).order_by(Skill.updated_at.desc(), Skill.id.desc())
        if for_share:
            stmt = stmt.with_for_update(read=True).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_enabled(self, *, for_share: bool = False) -> list[Skill]:
        stmt = select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.updated_at.desc(), Skill.id.desc())
        if for_share:
            stmt = stmt.with_for_update(read=True).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str, *, for_update: bool = False) -> Skill | None:
        stmt = select(Skill).where(Skill.slug == slug)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_slug(self, slug: str) -> bool:
        return (await self.get_by_slug(slug)) is not None

    async def create(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        source_type: str,
        tool_dependencies: list[str] | None,
        mcp_dependencies: list[str] | None,
        skill_dependencies: list[str] | None,
        dir_path: str,
        share_config: dict,
        enabled: bool = True,
        version: str | None = None,
        content_hash: str | None = None,
        created_by: str | None,
    ) -> Skill:
        now = utc_now_naive()
        item = Skill(
            slug=slug,
            name=name,
            description=description,
            source_type=source_type,
            tool_dependencies=tool_dependencies or [],
            mcp_dependencies=mcp_dependencies or [],
            skill_dependencies=skill_dependencies or [],
            dir_path=dir_path,
            version=version,
            content_hash=content_hash,
            share_config=share_config,
            enabled=enabled,
            created_by=created_by,
            updated_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.db.add(item)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_builtin_install(
        self,
        item: Skill,
        *,
        version: str,
        content_hash: str,
        updated_by: str | None,
    ) -> Skill:
        item.version = version
        item.content_hash = content_hash
        item.source_type = "builtin"
        item.share_config = {
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        }
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_dependencies(
        self,
        item: Skill,
        *,
        tool_dependencies: list[str],
        mcp_dependencies: list[str],
        skill_dependencies: list[str],
        updated_by: str | None,
    ) -> Skill:
        item.tool_dependencies = tool_dependencies
        item.mcp_dependencies = mcp_dependencies
        item.skill_dependencies = skill_dependencies
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_metadata(
        self,
        item: Skill,
        *,
        name: str,
        description: str,
        updated_by: str | None,
    ) -> Skill:
        item.name = name
        item.description = description
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_share_config(self, item: Skill, *, share_config: dict, updated_by: str | None) -> Skill:
        item.share_config = share_config
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_enabled(self, item: Skill, *, enabled: bool, updated_by: str | None) -> Skill:
        item.enabled = enabled
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete(self, item: Skill) -> None:
        await self.db.delete(item)
        await self.db.flush()
