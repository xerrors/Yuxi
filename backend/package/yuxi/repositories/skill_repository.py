from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.permissions import ResourcePermission, resolve_skill_permission
from yuxi.storage.postgres.models_business import Skill, User
from yuxi.utils.datetime_utils import utc_now_naive


class SkillRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_enabled_readable(self, user: User) -> list[Skill]:
        """只返回当前用户可使用的已启用共享索引。"""
        return [
            item
            for item in await self.list_enabled()
            if resolve_skill_permission(user, item) != ResourcePermission.NONE
        ]

    async def list_authorized_for_projection(self, user: User) -> list[Skill]:
        """为投影锁定阶段包含当前用户可读的停用项。"""
        return [
            item for item in await self.list_all() if resolve_skill_permission(user, item) != ResourcePermission.NONE
        ]

    async def list_visible_for_management(self, user: User) -> list[Skill]:
        """返回可管理项和可读取的已启用项。"""
        visible = []
        for item in await self.list_all():
            permission = resolve_skill_permission(user, item)
            can_manage_builtin = item.source_type == "builtin" and user.role in {"admin", "superadmin"}
            if (
                permission == ResourcePermission.MANAGE
                or can_manage_builtin
                or (item.enabled and permission != ResourcePermission.NONE)
            ):
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
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug_for_read(self, slug: str) -> Skill | None:
        """读取文件期间取得单个共享 Skill 的共享行锁。"""
        stmt = (
            select(Skill).where(Skill.slug == slug).with_for_update(read=True).execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_builtin(self) -> list[Skill]:
        """在数据库中过滤内置来源，保持索引的更新时间排序。"""
        stmt = select(Skill).where(Skill.source_type == "builtin").order_by(Skill.updated_at.desc(), Skill.id.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Skill]:
        stmt = select(Skill).order_by(Skill.updated_at.desc(), Skill.id.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_enabled(self) -> list[Skill]:
        stmt = select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.updated_at.desc(), Skill.id.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str, *, for_update: bool = False) -> Skill | None:
        stmt = select(Skill).where(Skill.slug == slug)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
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
