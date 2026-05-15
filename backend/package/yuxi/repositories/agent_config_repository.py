from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentConfig
from yuxi.utils.datetime_utils import utc_now_naive

# 默认配置名称
DEFAULT_CONFIG_NAME = "初始配置"


def _merge_skill_slugs(current_slugs: object, new_slugs: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*(current_slugs if isinstance(current_slugs, list) else []), *new_slugs]:
        if not isinstance(value, str):
            continue
        slug = value.strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        merged.append(slug)
    return merged


class AgentConfigRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_by_department_agent(self, *, department_id: int, agent_id: str) -> list[AgentConfig]:
        result = await self.db.execute(
            select(AgentConfig)
            .where(AgentConfig.department_id == department_id, AgentConfig.agent_id == agent_id)
            .order_by(AgentConfig.is_default.desc(), AgentConfig.id.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, config_id: int) -> AgentConfig | None:
        result = await self.db.execute(select(AgentConfig).where(AgentConfig.id == config_id))
        return result.scalar_one_or_none()

    async def _get_by_id_for_update(self, config_id: int) -> AgentConfig | None:
        result = await self.db.execute(select(AgentConfig).where(AgentConfig.id == config_id).with_for_update())
        return result.scalar_one_or_none()

    async def set_default(self, *, config: AgentConfig, updated_by: str | None = None) -> AgentConfig:
        if config.is_default:
            return config

        now = utc_now_naive()

        # 先清空该部门+智能体的所有默认配置
        await self.db.execute(
            update(AgentConfig)
            .where(
                AgentConfig.department_id == config.department_id,
                AgentConfig.agent_id == config.agent_id,
            )
            .values(is_default=False, updated_at=now, updated_by=updated_by)
        )

        # 再设置目标配置为默认
        config.is_default = True
        config.updated_at = now
        config.updated_by = updated_by

        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def get_default(self, *, department_id: int, agent_id: str) -> AgentConfig | None:
        result = await self.db.execute(
            select(AgentConfig).where(
                AgentConfig.department_id == department_id,
                AgentConfig.agent_id == agent_id,
                AgentConfig.is_default.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_default(
        self,
        *,
        department_id: int,
        agent_id: str,
        created_by: str | None = None,
    ) -> AgentConfig:
        existing = await self.get_default(department_id=department_id, agent_id=agent_id)
        if existing:
            return existing

        items = await self.list_by_department_agent(department_id=department_id, agent_id=agent_id)
        if items:
            return items[0]

        config = AgentConfig(
            department_id=department_id,
            agent_id=agent_id,
            name=DEFAULT_CONFIG_NAME,
            description=None,
            icon=None,
            pics=[],
            examples=[],
            config_json={},
            is_default=True,
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def _name_exists(self, *, department_id: int, agent_id: str, name: str, exclude_id: int | None) -> bool:
        stmt = select(AgentConfig.id).where(
            AgentConfig.department_id == department_id,
            AgentConfig.agent_id == agent_id,
            AgentConfig.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(AgentConfig.id != exclude_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def ensure_unique_name(
        self,
        *,
        department_id: int,
        agent_id: str,
        desired_name: str,
        exclude_id: int | None = None,
    ) -> str:
        candidate = desired_name.strip() or "未命名配置"
        if not await self._name_exists(
            department_id=department_id, agent_id=agent_id, name=candidate, exclude_id=exclude_id
        ):
            return candidate

        base = f"{candidate}-副本"
        if not await self._name_exists(
            department_id=department_id, agent_id=agent_id, name=base, exclude_id=exclude_id
        ):
            return base

        idx = 2
        while True:
            candidate2 = f"{base}{idx}"
            if not await self._name_exists(
                department_id=department_id, agent_id=agent_id, name=candidate2, exclude_id=exclude_id
            ):
                return candidate2
            idx += 1

    async def create(
        self,
        *,
        department_id: int,
        agent_id: str,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        pics: list[str] | None = None,
        examples: list[str] | None = None,
        config_json: dict | None = None,
        is_default: bool = False,
        created_by: str | None = None,
    ) -> AgentConfig:
        unique_name = await self.ensure_unique_name(
            department_id=department_id,
            agent_id=agent_id,
            desired_name=name,
            exclude_id=None,
        )
        config = AgentConfig(
            department_id=department_id,
            agent_id=agent_id,
            name=unique_name,
            description=description,
            icon=icon,
            pics=pics or [],
            examples=examples or [],
            config_json=config_json or {},
            is_default=bool(is_default),
            created_by=created_by,
            updated_by=created_by,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def update(
        self,
        config: AgentConfig,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        pics: list[str] | None = None,
        examples: list[str] | None = None,
        config_json: dict | None = None,
        updated_by: str | None = None,
    ) -> AgentConfig:
        if name is not None:
            config.name = await self.ensure_unique_name(
                department_id=config.department_id,
                agent_id=config.agent_id,
                desired_name=name,
                exclude_id=config.id,
            )
        if description is not None:
            config.description = description
        if icon is not None:
            config.icon = icon
        if pics is not None:
            config.pics = pics
        if examples is not None:
            config.examples = examples
        if config_json is not None:
            config.config_json = config_json

        config.updated_by = updated_by
        config.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def delete(self, *, config: AgentConfig, updated_by: str | None = None) -> None:
        department_id = config.department_id
        agent_id = config.agent_id
        was_default = bool(config.is_default)

        await self.db.delete(config)
        await self.db.commit()

        remaining = await self.list_by_department_agent(department_id=department_id, agent_id=agent_id)
        if not remaining:
            await self.get_or_create_default(department_id=department_id, agent_id=agent_id, created_by=updated_by)
            return

        if was_default:
            await self.set_default(config=remaining[0], updated_by=updated_by)

    async def add_skills_to_config_json(self, *, agent_config_id: int, new_slugs: list[str]) -> bool:
        """在 config_json.context.skills 中追加 skills，并保持顺序去重。

        Args:
            agent_config_id: AgentConfig 的 ID
            new_slugs: 要追加的技能 slug 列表，会自动去重

        Returns:
            是否找到并更新了配置
        """
        config = await self._get_by_id_for_update(agent_config_id)
        if not config:
            return False

        config_json = dict(config.config_json or {})
        context = dict(config_json.get("context") or {})
        context["skills"] = _merge_skill_slugs(context.get("skills"), new_slugs)
        config_json["context"] = context

        config.config_json = config_json
        config.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(config)
        return True
