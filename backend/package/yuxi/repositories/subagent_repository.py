"""SubAgent 数据访问层"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import SubAgent
from yuxi.utils.datetime_utils import utc_now_naive


class SubAgentRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_all(self) -> list[SubAgent]:
        """获取所有 SubAgent，按 updated_at 降序"""
        result = await self.db.execute(select(SubAgent).order_by(SubAgent.updated_at.desc()))
        return list(result.scalars().all())

    async def list_all_specs(self) -> list[dict[str, Any]]:
        """获取所有 SubAgent 运行规格，按 updated_at 降序"""
        items = await self.list_all()
        return [item.to_subagent_spec() for item in items]

    async def get_by_name(self, name: str) -> SubAgent | None:
        """根据名称获取 SubAgent"""
        result = await self.db.execute(select(SubAgent).where(SubAgent.name == name))
        return result.scalar_one_or_none()

    async def exists_name(self, name: str) -> bool:
        """检查名称是否存在（仅查询计数，不获取完整数据）"""
        from sqlalchemy import func, select

        result = await self.db.execute(select(func.count()).select_from(SubAgent).where(SubAgent.name == name))
        return result.scalar() > 0

    async def create(
        self,
        *,
        name: str,
        description: str,
        system_prompt: str,
        tools: list[str] | None,
        model: str | None,
        is_builtin: bool,
        created_by: str | None,
    ) -> SubAgent:
        now = utc_now_naive()
        item = SubAgent(
            name=name,
            description=description,
            system_prompt=system_prompt,
            tools=tools or [],
            model=model,
            is_builtin=is_builtin,
            created_by=created_by,
            updated_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def update(
        self,
        item: SubAgent,
        *,
        description: str | None,
        system_prompt: str | None,
        tools: list[str] | None,
        model: str | None,
        model_provided: bool = False,
        updated_by: str | None,
    ) -> SubAgent:
        # 批量更新非空字段
        updates = {
            "description": description,
            "system_prompt": system_prompt,
            "tools": tools,
        }
        for field, value in updates.items():
            if value is not None:
                setattr(item, field, value)
        # model_provided=True 时显式设置 model 值（包括 None），用于清空字段
        if model_provided:
            item.model = model
        item.updated_by = updated_by
        item.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def delete(self, item: SubAgent) -> None:
        """删除 SubAgent"""
        await self.db.delete(item)
        await self.db.commit()
