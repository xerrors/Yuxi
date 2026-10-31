"""个人助理绑定的唯一身份查询。"""

from sqlalchemy import select

from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.personal_assistants import PersonalAssistant


async def is_personal_assistant(db, agent_slug: str) -> bool:
    """判断 Agent 是否登记为个人助理。"""
    return (
        await db.scalar(select(PersonalAssistant.uid).join(Agent, Agent.id == PersonalAssistant.agent_id).where(Agent.slug == agent_slug))
        is not None
    )


async def is_personal_main_thread(db, thread_id: str) -> bool:
    """判断会话是否为个人助理主会话。"""
    return await db.scalar(select(PersonalAssistant.uid).where(PersonalAssistant.main_thread_id == thread_id)) is not None
