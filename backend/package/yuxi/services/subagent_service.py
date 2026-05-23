"""SubAgent 服务层"""

import json
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.subagent_repository import SubAgentRepository
from yuxi.services.run_queue_service import get_redis_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger
from yuxi.utils.paths import OUTPUTS_DIR_NAME

# ----------------------------------------------------
# SubAgent 双级缓存控制变量
# ----------------------------------------------------
_REDIS_SPECS_KEY = "yuxi:subagent_cache:specs"
_REDIS_KEY_TTL = 3600  # 1小时全局 Redis 缓存

_local_specs_cache: list[dict[str, Any]] | None = None
_local_specs_cache_at: float = 0.0
_L2_CACHE_TTL_SECONDS = 5.0  # L2 进程级缓存生存时间 5 秒


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
            f"将调研结果保存到主题研究文件中 {OUTPUTS_DIR_NAME}/sub_research/xxx.md 中。"
        ),
        "tools": ["tavily_search"],
        "mcps": [],
        "skills": [],
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
        "mcps": [],
        "skills": [],
        "is_builtin": True,
    },
]

_SYNCED_SUBAGENT_FIELDS = ("description", "system_prompt", "tools", "mcps", "skills", "model", "is_builtin")


async def init_builtin_subagents() -> None:
    """初始化内置 SubAgent，并以代码定义覆盖展示字段。"""
    async with pg_manager.get_async_session_context() as session:
        repo = SubAgentRepository(session)
        for data in _DEFAULT_SUBAGENTS:
            item = await repo.get_by_name(data["name"])
            if item is None:
                await repo.create(
                    name=data["name"],
                    description=data["description"],
                    system_prompt=data["system_prompt"],
                    tools=data.get("tools", []),
                    mcps=data.get("mcps", []),
                    skills=data.get("skills", []),
                    model=None,
                    is_builtin=data.get("is_builtin", False),
                    created_by="system",
                )
                continue

            changed = False
            for field in _SYNCED_SUBAGENT_FIELDS:
                next_value = data.get(field)
                current_value = getattr(item, field)
                if current_value != next_value:
                    setattr(item, field, deepcopy(next_value))
                    changed = True
            if changed:
                item.updated_by = "system"
        await session.commit()
    await clear_specs_cache()


async def clear_specs_cache() -> None:
    """清除全局子智能体规格缓存（包括当前进程 L2 内存缓存与分布式 Redis 缓存）"""
    global _local_specs_cache, _local_specs_cache_at
    # 1. 废弃当前进程本地 L2 缓存
    _local_specs_cache = None
    _local_specs_cache_at = 0.0

    # 2. 异步删除全局 Redis L1 缓存
    try:
        redis = await get_redis_client()
        await redis.delete(_REDIS_SPECS_KEY)
        logger.debug("SubAgent specs cache invalidated in Redis")
    except Exception as e:
        logger.warning(f"Failed to invalidate SubAgent specs cache in Redis: {e}")


async def get_subagent_specs(db: AsyncSession | None = None) -> list[dict[str, Any]]:
    """获取所有已启用的 subagent specs，支持 L1 Redis + L2 本地短 TTL 内存双级缓存"""
    global _local_specs_cache, _local_specs_cache_at
    now = time.monotonic()

    # 1. 尝试 L2 本地内存缓存命中
    if _local_specs_cache is not None and (now - _local_specs_cache_at) < _L2_CACHE_TTL_SECONDS:
        return deepcopy(_local_specs_cache)

    # 2. 尝试 L1 Redis 缓存命中
    try:
        redis = await get_redis_client()
        raw = await redis.get(_REDIS_SPECS_KEY)
        if raw:
            specs = json.loads(raw)
            _local_specs_cache = specs
            _local_specs_cache_at = now
            logger.debug("SubAgent specs loaded from Redis L1 cache")
            return deepcopy(specs)
    except Exception as e:
        logger.warning(f"Failed to load SubAgent specs from Redis cache (falling back to DB): {e}")

    # 3. 缓存均未命中，回退到数据库查询
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        specs = await repo.list_all_specs()

    # 4. 异步回写 Redis，同步更新 L2
    _local_specs_cache = specs
    _local_specs_cache_at = now

    try:
        redis = await get_redis_client()
        await redis.setex(_REDIS_SPECS_KEY, _REDIS_KEY_TTL, json.dumps(specs, ensure_ascii=False))
        logger.debug("SubAgent specs cache populated to Redis L1")
    except Exception as e:
        logger.warning(f"Failed to populate SubAgent specs cache to Redis: {e}")

    return deepcopy(specs)


async def get_subagents_from_names(selected_names: Any, *, db: AsyncSession | None = None) -> list[dict[str, Any]]:
    """根据名称获取 subagent specs（含工具解析 + MCP 工具异步解析）。"""
    specs = await get_subagent_specs(db)

    if not selected_names:
        return []

    selected_set = set(selected_names)
    available = {spec["name"] for spec in specs if isinstance(spec.get("name"), str)}

    matched = [spec for spec in specs if spec.get("name") in selected_set]
    missing = [n for n in selected_names if n not in available]
    if missing:
        logger.warning(f"Configured subagents not found, skip: {missing}")

    from yuxi.agents.toolkits import get_all_tool_instances
    from yuxi.services.mcp_service import get_enabled_mcp_tools

    all_tools = get_all_tool_instances()
    all_tool_names = {tool.name: tool for tool in all_tools}
    resolved_specs = []
    for spec in matched:
        resolved_spec = dict(spec)

        # 1. 解析普通工具
        tool_names = spec.get("tools", [])
        resolved_tools = [all_tool_names[name] for name in tool_names if name in all_tool_names]

        # 2. 异步解析 MCP 工具并合并
        mcp_names = spec.get("mcps", [])
        mcp_tools = []
        for server_name in mcp_names:
            if not isinstance(server_name, str):
                continue
            try:
                tools = await get_enabled_mcp_tools(server_name)
                mcp_tools.extend(tools)
            except Exception as e:
                logger.warning(f"SubAgent '{spec['name']}': failed to load MCP '{server_name}': {e}")
        resolved_tools.extend(mcp_tools)

        # 3. 传递 skills 配置并构建 middleware
        resolved_spec["skills"] = spec.get("skills", [])
        if resolved_spec["skills"]:
            resolved_spec["middleware"] = _build_subagent_skills_middleware(
                resolved_spec["skills"], system_prompt=spec.get("system_prompt")
            )

            # 4. 异步解析技能（Skills）依赖的普通工具和 MCP 工具，并在 Graph 编译期并入 resolved_tools
            dep_tools = await _resolve_skill_dependencies(resolved_spec["skills"], all_tool_names, spec["name"])
            for t in dep_tools:
                if t not in resolved_tools:
                    resolved_tools.append(t)

        resolved_spec["tools"] = resolved_tools
        resolved_specs.append(resolved_spec)

    return resolved_specs


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
    await _validate_subagent_capabilities(data, db)
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.create(
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            tools=data.get("tools"),
            mcps=data.get("mcps"),
            skills=data.get("skills"),
            model=data.get("model"),
            is_builtin=False,
            created_by=created_by,
        )
    await clear_specs_cache()
    return item.to_dict()


async def update_subagent(
    name: str,
    data: dict[str, Any],
    updated_by: str | None,
    db: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """更新 SubAgent"""
    await _validate_subagent_capabilities(data, db)
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
            mcps=data.get("mcps"),
            skills=data.get("skills"),
            model=data.get("model"),
            model_provided="model" in data,
            updated_by=updated_by,
        )
    await clear_specs_cache()
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
    await clear_specs_cache()
    return True


async def set_subagent_enabled(
    name: str,
    enabled: bool,
    *,
    updated_by: str | None,
    db: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """更新 SubAgent 启用状态。"""
    async with _get_session(db) as session:
        repo = SubAgentRepository(session)
        item = await repo.get_by_name(name)
        if not item:
            return None
        item.enabled = enabled
        item.updated_by = updated_by
        await session.commit()
        await session.refresh(item)
    await clear_specs_cache()
    return item.to_dict()


async def _resolve_skill_dependencies(
    skills: list[str],
    all_tool_names: dict[str, Any],
    subagent_name: str,
) -> list[Any]:
    """解析技能（Skills）依赖的普通工具和 MCP 工具。"""
    from yuxi.agents.middlewares.skills_middleware import expand_skill_closure, get_dependency_map
    from yuxi.services.mcp_service import get_enabled_mcp_tools

    resolved_dep_tools = []
    try:
        dependency_map = await get_dependency_map()
        visible_skills = expand_skill_closure(skills, dependency_map)

        dep_tool_names = []
        dep_mcp_names = []
        for s_slug in visible_skills:
            node = dependency_map.get(s_slug)
            if node:
                dep_tool_names.extend(node.get("tools", []))
                dep_mcp_names.extend(node.get("mcps", []))

        # 去重
        dep_tool_names = list(dict.fromkeys(dep_tool_names))
        dep_mcp_names = list(dict.fromkeys(dep_mcp_names))

        # 合并依赖的普通工具到 resolved_dep_tools
        for t_name in dep_tool_names:
            if t_name in all_tool_names:
                resolved_dep_tools.append(all_tool_names[t_name])

        # 合并依赖的 MCP 工具到 resolved_dep_tools
        for server_name in dep_mcp_names:
            if not isinstance(server_name, str):
                continue
            try:
                tools = await get_enabled_mcp_tools(server_name)
                for t in tools:
                    if t not in resolved_dep_tools:
                        resolved_dep_tools.append(t)
            except Exception as e:
                logger.warning(f"SubAgent '{subagent_name}': failed to load Skill dependent MCP '{server_name}': {e}")
    except Exception as e:
        logger.error(f"Failed to resolve skill dependencies for subagent '{subagent_name}': {e}")

    return resolved_dep_tools


def _build_subagent_skills_middleware(skills: list[str], system_prompt: str | None = None) -> list:
    """为有 Skills 配置的子代理构建专属 middleware 列表。"""
    from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware

    return [SkillsMiddleware(static_skills=skills, subagent_system_prompt=system_prompt)]


async def _validate_subagent_capabilities(data: dict[str, Any], db: AsyncSession | None = None) -> None:
    """校验 SubAgent 配置的工具/MCP/Skills 是否有效"""
    from yuxi.agents.toolkits import get_all_tool_instances
    from yuxi.services.mcp_service import get_enabled_mcp_server_config
    from yuxi.services.skill_service import list_skills

    # 校验工具
    if tools := data.get("tools"):
        all_tools = get_all_tool_instances()
        valid_names = {t.name for t in all_tools}
        invalid = [t for t in tools if t not in valid_names]
        if invalid:
            raise ValueError(f"无效的工具: {invalid}")

    # 校验 MCP（必须存在且已启用）
    if mcps := data.get("mcps"):
        for name in mcps:
            config = await get_enabled_mcp_server_config(name)
            if not config:
                raise ValueError(f"MCP 服务 '{name}' 不存在或未启用")

    # 校验 Skills（必须存在于 skills 表）
    if skills := data.get("skills"):
        async with _get_session(db) as session:
            all_skills = await list_skills(session)
            valid_slugs = {s.slug for s in all_skills}
            invalid = [s for s in skills if s not in valid_slugs]
            if invalid:
                raise ValueError(f"无效的 Skill: {invalid}")
