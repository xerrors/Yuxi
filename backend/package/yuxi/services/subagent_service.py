"""SubAgent 服务层"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.subagent_repository import SubAgentRepository
from yuxi.services.mcp_service import get_tools_from_all_servers
from yuxi.storage.postgres.manager import pg_manager

# SubAgent specs cache for get_subagent_specs
_subagent_specs_cache: list[dict[str, Any]] | None = None
_subagent_specs_lock = asyncio.Lock()


@asynccontextmanager
async def _get_session(db: AsyncSession | None = None):
    """获取数据库会话的上下文管理器"""
    if db is not None:
        yield db
    else:
        async with pg_manager.get_async_session_context() as session:
            yield session


# 内置 SubAgent 配置
_DEFAULT_SUBAGENTS = [
    {
        "name": "research-agent",
        "description": "利用搜索工具，用于研究更深入的问题。将调研结果写入到主题研究文件中。",
        "system_prompt": (
            "你是一位专注的研究员。你的工作是根据用户的问题进行研究。"
            "进行彻底的研究，然后用详细的答案回复用户的问题，只有你的最终答案会被传递给用户。"
            "除了你的最终信息，他们不会知道任何其他事情，所以你的最终报告应该就是你的最终信息！"
            "将调研结果保存到主题研究文件中 /sub_research/xxx.md 中。"
        ),
        "tools": ["tavily_search"],
        "is_builtin": True,
    },
    {
        "name": "critique-agent",
        "description": "用于评论最终报告。给这个代理一些关于你希望它如何评论报告的信息。",
        "system_prompt": (
            "你是一位专注的编辑。你的任务是评论一份报告。\n\n"
            "你可以在 `final_report.md` 找到这份报告。\n\n"
            "你可以在 `question.txt` 找到这份报告的问题/主题。\n\n"
            "用户可能会要求评论报告的特定方面。请用详细的评论回复用户，指出报告中可以改进的地方。\n\n"
            "如果有助于你评论报告，你可以使用搜索工具来搜索信息\n\n"
            "不要自己写入 `final_report.md`。\n\n"
            "需要检查的事项：\n"
            "- 检查每个部分的标题是否恰当\n"
            "- 检查报告的写法是否像论文或教科书——它应该是以文本为主，不要只是一个项目符号列表！\n"
            "- 检查报告是否全面。如果任何段落或部分过短，或缺少重要细节，请指出来。\n"
            "- 检查文章是否涵盖了行业的关键领域，确保了整体理解，并且没有遗漏重要部分。\n"
            "- 检查文章是否深入分析了原因、影响和趋势，提供了有价值的见解\n"
            "- 检查文章是否紧扣研究主题并直接回答问题\n"
            "- 检查文章是否结构清晰、语言流畅、易于理解。"
        ),
        "tools": [],
        "is_builtin": True,
    },
]


async def init_builtin_subagents() -> None:
    """初始化内置 SubAgent（仅创建不存在的）"""
    async with pg_manager.get_async_session_context() as session:
        repo = SubAgentRepository(session)
        for data in _DEFAULT_SUBAGENTS:
            if not await repo.exists_name(data["name"]):
                await repo.create(
                    name=data["name"],
                    description=data["description"],
                    system_prompt=data["system_prompt"],
                    tools=data.get("tools", []),
                    model=None,
                    is_builtin=data.get("is_builtin", False),
                    created_by="system",
                )


async def get_subagent_specs(db: AsyncSession | None = None) -> list[dict[str, Any]]:
    """获取所有 subagent specs，用于 SubAgentMiddleware（工具名称未解析）"""
    global _subagent_specs_cache
    if _subagent_specs_cache is not None:
        return deepcopy(_subagent_specs_cache)
    async with _subagent_specs_lock:
        if _subagent_specs_cache is not None:
            return deepcopy(_subagent_specs_cache)
        async with _get_session(db) as session:
            repo = SubAgentRepository(session)
            subagents = await repo.list_all()
        _subagent_specs_cache = [sa.to_subagent_spec() for sa in subagents]
    return deepcopy(_subagent_specs_cache)


def invalidate_subagent_specs_cache() -> None:
    """清除 subagent specs 缓存"""
    global _subagent_specs_cache
    _subagent_specs_cache = None


def resolve_subagent_tools(specs: list[dict[str, Any]], available_tools: list[Any]) -> list[dict[str, Any]]:
    """将 subagent specs 中的工具名称解析为实际工具实例"""
    available_by_name = {tool.name: tool for tool in available_tools if hasattr(tool, "name")}
    resolved_specs = []
    for spec in specs:
        resolved_spec = dict(spec)
        tool_names = spec.get("tools", [])
        resolved_spec["tools"] = [
            available_by_name[name] for name in tool_names if isinstance(name, str) and name in available_by_name
        ]
        resolved_specs.append(resolved_spec)
    return resolved_specs


async def _get_available_tools() -> list[Any]:
    """获取所有可用的工具实例"""
    from yuxi.agents.common.toolkits.buildin.tools import _create_tavily_search

    tools = []
    # 添加 tavily_search 工具
    tavily = _create_tavily_search()
    if tavily:
        tools.append(tavily)
    # 添加 MCP 工具
    mcp_tools = await get_tools_from_all_servers()
    tools.extend(mcp_tools)
    return tools


async def get_all_subagents(db: AsyncSession | None = None) -> list[dict[str, Any]]:
    """获取所有 SubAgent（含禁用的）"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        items = await repo.list_all()
    return [item.to_dict() for item in items]


async def get_subagent(name: str, db: AsyncSession | None = None) -> dict[str, Any] | None:
    """获取单个 SubAgent"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.get_by_name(name)
    return item.to_dict() if item else None


async def create_subagent(
    data: dict[str, Any],
    created_by: str | None,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """创建 SubAgent"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.create(
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            tools=data.get("tools"),
            model=data.get("model"),
            is_builtin=False,
            created_by=created_by,
        )
    invalidate_subagent_specs_cache()
    return item.to_dict()


async def update_subagent(
    name: str,
    data: dict[str, Any],
    updated_by: str | None,
    db: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """更新 SubAgent"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.get_by_name(name)
        if not item:
            return None
        if item.is_builtin:
            raise ValueError("内置 SubAgent 不可编辑")
        item = await repo.update(
            item,
            description=data.get("description"),
            system_prompt=data.get("system_prompt"),
            tools=data.get("tools"),
            model=data.get("model"),
            model_provided="model" in data,
            updated_by=updated_by,
        )
    invalidate_subagent_specs_cache()
    return item.to_dict()


async def delete_subagent(name: str, db: AsyncSession | None = None) -> bool:
    """删除 SubAgent"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.get_by_name(name)
        if not item:
            return False
        if item.is_builtin:
            raise ValueError("内置 SubAgent 不可删除")
        await repo.delete(item)
    invalidate_subagent_specs_cache()
    return True
