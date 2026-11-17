from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.extensions.skills.models import Skill, SkillVersion
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_skill_permission
from yuxi.shared.datetime import utc_now


class SkillRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_enabled_readable(self, user: User) -> list[Skill]:
        """只返回当前用户可使用的已启用共享索引。"""
        return [item for item in await self.list_enabled() if resolve_skill_permission(user, item) != ResourcePermission.NONE]

    async def list_authorized_for_projection(self, user: User) -> list[Skill]:
        """为投影锁定阶段包含当前用户可读的停用项。"""
        return [
            item
            for item in await self.list_all(include_agent_bound=True)
            if resolve_skill_permission(user, item) != ResourcePermission.NONE
        ]

    async def list_visible_for_management(self, user: User) -> list[Skill]:
        """返回可管理项和可读取的已启用项。"""
        visible = []
        for item in await self.list_all():
            permission = resolve_skill_permission(user, item)
            if permission == ResourcePermission.MANAGE or (item.enabled and permission != ResourcePermission.NONE):
                visible.append(item)
        return visible

    async def lock_rows_for_read(self, ids: list[int]) -> list[Skill]:
        """只锁定调用方已筛出的共享 Skill，并刷新会话内旧值。"""
        if not ids:
            return []
        stmt = (
            select(Skill)
            .where(Skill.id.in_(ids))
            .order_by(Skill.id)
            .with_for_update(read=True, of=Skill)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug_for_read(self, slug: str) -> Skill | None:
        """读取文件期间取得单个共享 Skill 的共享行锁。"""
        stmt = select(Skill).where(Skill.slug == slug).with_for_update(read=True, of=Skill).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_builtin(self) -> list[Skill]:
        """在数据库中过滤内置来源，保持索引的更新时间排序。"""
        stmt = select(Skill).where(Skill.source_type == "builtin").order_by(Skill.updated_at.desc(), Skill.id.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, *, include_agent_bound: bool = False) -> list[Skill]:
        stmt = select(Skill)
        if not include_agent_bound:
            stmt = stmt.where(Skill.bound_agent_id.is_(None))
        stmt = stmt.order_by(Skill.updated_at.desc(), Skill.id.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_enabled(self) -> list[Skill]:
        stmt = (
            select(Skill).where(Skill.enabled.is_(True), Skill.bound_agent_id.is_(None)).order_by(Skill.updated_at.desc(), Skill.id.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str, *, for_update: bool = False) -> Skill | None:
        stmt = select(Skill).where(Skill.slug == slug)
        if for_update:
            stmt = stmt.with_for_update(of=Skill).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_bound_agent_id(self, agent_id: int, *, for_update: bool = False) -> Skill | None:
        """读取稳定 Agent 绑定，内容变更使用同一 Skill 行锁。"""
        stmt = select(Skill).where(Skill.bound_agent_id == agent_id)
        if for_update:
            stmt = stmt.with_for_update(of=Skill).execution_options(populate_existing=True)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions(self, skill_id: int) -> list[SkillVersion]:
        """按创建顺序返回所属 Skill 的历史版本。"""
        result = await self.db.scalars(select(SkillVersion).where(SkillVersion.skill_id == skill_id).order_by(SkillVersion.id.desc()))
        return list(result.all())

    async def get_version(self, skill_id: int, version: str) -> SkillVersion | None:
        """版本定位同时约束所属 Skill，阻止跨资源操作。"""
        return await self.db.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id, SkillVersion.version == version))

    async def exists_slug(self, slug: str) -> bool:
        return (await self.get_by_slug(slug)) is not None

    async def create(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        source_type: str,
        dir_path: str,
        share_config: dict,
        bound_agent_id: int | None = None,
        enabled: bool = True,
        version: str | None = None,
        content_hash: str | None = None,
        created_by: str | None,
    ) -> Skill:
        now = utc_now()
        item = Skill(
            slug=slug,
            bound_agent_id=bound_agent_id,
            name=name,
            description=description,
            source_type=source_type,
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
        item.updated_at = utc_now()
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
        item.updated_at = utc_now()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_share_config(self, item: Skill, *, share_config: dict, updated_by: str | None) -> Skill:
        item.share_config = share_config
        item.updated_by = updated_by
        item.updated_at = utc_now()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def update_enabled(self, item: Skill, *, enabled: bool, updated_by: str | None) -> Skill:
        item.enabled = enabled
        item.updated_by = updated_by
        item.updated_at = utc_now()
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete(self, item: Skill) -> None:
        await self.db.delete(item)
        await self.db.flush()
