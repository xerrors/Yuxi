"""停写后显式执行 business v1 到 v2 的个人助理表升级。"""

import asyncio

from sqlalchemy import or_, select, text, update

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.migrations.schema import schema_migration_lock
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.personal_assistants import PersonalAssistant
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES, AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn


async def upgrade(manager) -> None:
    """在初始化锁内原子建表并更新版本；已完成的升级可安全重放。"""
    load_models()
    async with schema_migration_lock(manager):
        async with manager.async_engine.begin() as conn:
            versions = dict((await conn.execute(text("SELECT domain, version FROM yuxi_schema_migrations"))).all())
            exists = await conn.scalar(text("SELECT to_regclass('personal_assistants') IS NOT NULL"))
            if versions == {"business": 2, "knowledge": 2}:
                if not exists:
                    raise RuntimeError("business v2 缺少个人助理表")
                registered_slugs = (
                    select(Agent.slug)
                    .join(PersonalAssistant, PersonalAssistant.agent_id == Agent.id)
                    .where(Agent.backend_id != "PersonalAssistantAgent")
                )
                threads = select(Session.thread_id).where(Session.agent_id.in_(registered_slugs))
                unfinished = await conn.scalar(
                    select(
                        or_(
                            select(AgentInput.id).where(AgentInput.thread_id.in_(threads), AgentInput.status == "pending").exists(),
                            select(AgentTurn.id)
                            .where(AgentTurn.thread_id.in_(threads), AgentTurn.status.in_(("running", "waiting", "cancelling")))
                            .exists(),
                            select(AgentRun.id)
                            .where(
                                AgentRun.agent_slug.in_(registered_slugs),
                                or_(AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
                            )
                            .exists(),
                        )
                    )
                )
                if unfinished:
                    raise RuntimeError("助理仍有待处理输入、未结束轮次或清理责任，请停写并处理后升级")
                await conn.execute(update(Agent).where(Agent.slug.in_(registered_slugs)).values(backend_id="PersonalAssistantAgent"))
                return
            if versions != {"business": 1, "knowledge": 2} or exists:
                raise RuntimeError(f"不支持的 Schema 版本或已有个人助理表: {versions}")
            await conn.run_sync(lambda sync: PersonalAssistant.__table__.create(sync, checkfirst=False))
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 2, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """运行独立的离线升级入口。"""
    pg_manager.initialize()
    try:
        await upgrade(pg_manager)
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
