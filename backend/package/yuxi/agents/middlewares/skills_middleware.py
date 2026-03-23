"""Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Annotated, Any, NotRequired, TypedDict

from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.toolkits import get_all_tool_instances
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.mcp_service import get_enabled_mcp_tools
from yuxi.services.skill_service import _normalize_string_list, is_valid_skill_slug
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

# =============================================================================
# 类型定义
# =============================================================================


class SkillPromptMetadata(TypedDict):
    name: str
    description: str
    path: str


class SkillDependencyNode(TypedDict):
    tools: list[str]
    mcps: list[str]
    skills: list[str]


# =============================================================================
# 运行时数据加载函数
# =============================================================================


async def _list_skills_from_db(db: AsyncSession | None = None) -> list:
    """从数据库加载 skills 列表"""
    if db is not None:
        repo = SkillRepository(db)
        return await repo.list_all()

    async with pg_manager.get_async_session_context() as session:
        repo = SkillRepository(session)
        return await repo.list_all()


async def get_prompt_metadata(db: AsyncSession | None = None) -> dict[str, SkillPromptMetadata]:
    """获取提示词元数据（直接从数据库加载）"""
    skills = await _list_skills_from_db(db)
    return {
        item.slug: {
            "name": item.name,
            "description": item.description,
            "path": f"/mnt/skills/{item.slug}/SKILL.md",
        }
        for item in skills
    }


async def get_dependency_map(db: AsyncSession | None = None) -> dict[str, SkillDependencyNode]:
    """获取依赖关系映射（直接从数据库加载）"""
    skills = await _list_skills_from_db(db)
    result: dict[str, SkillDependencyNode] = {}
    for item in skills:
        result[item.slug] = {
            "tools": normalize_selected_skills(item.tool_dependencies or []),
            "mcps": normalize_selected_skills(item.mcp_dependencies or []),
            "skills": normalize_selected_skills(item.skill_dependencies or []),
        }
    return result


def normalize_selected_skills(selected_skills: list[str] | None) -> list[str]:
    """规范化 skills 列表，去重并过滤无效值"""
    return _normalize_string_list(selected_skills)


def expand_skill_closure(
    slugs: list[str] | None,
    dependency_map: dict[str, SkillDependencyNode],
) -> list[str]:
    """展开 skills 依赖闭包，返回包含所有依赖的列表"""
    ordered_roots = normalize_selected_skills(slugs)
    if not ordered_roots:
        return []

    result: list[str] = []
    seen: set[str] = set()

    def dfs(slug: str, stack: set[str]) -> None:
        if slug in stack:
            logger.warning(f"Cycle detected in skill dependencies, skip: {' -> '.join([*stack, slug])}")
            return
        if slug in seen:
            return

        node = dependency_map.get(slug)
        if not node:
            logger.warning(f"Skill dependency target not found in DB, skip: {slug}")
            return

        seen.add(slug)
        result.append(slug)
        next_stack = set(stack)
        next_stack.add(slug)
        for dep in node.get("skills", []):
            dfs(dep, next_stack)

    for root in ordered_roots:
        dfs(root, set())
    return result


def _activated_skills_reducer(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并 activated_skills 列表"""
    merged: list[str] = []
    seen: set[str] = set()
    for group in (left or [], right or []):
        for value in group:
            if not isinstance(value, str):
                continue
            slug = value.strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            merged.append(slug)
    return merged


class SkillsState(AgentState):
    """Skills 状态定义"""

    activated_skills: NotRequired[Annotated[list[str], _activated_skills_reducer]]


class SkillsMiddleware(AgentMiddleware):
    """Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活

    职责：
    - Skills 提示词注入（直接从数据库加载）
    - 依赖展开（用户配置 + 动态激活）
    - 工具/MCP 动态加载
    """

    state_schema = SkillsState

    def __init__(
        self,
        *,
        skills_context_name: str = "skills",
        enable_skills_prompt: bool = True,
        skills_sources_for_prompt: list[str] | None = None,
    ):
        """初始化中间件

        Args:
            skills_context_name: 上下文中的 skills 列表字段名称（默认 "skills"）
            enable_skills_prompt: 是否启用 skills 提示段注入（默认 True）
            skills_sources_for_prompt: skills 来源路径（用于提示词展示，默认 ["/mnt/skills/"]）
        """
        super().__init__()
        self.skills_context_name = skills_context_name
        self.enable_skills_prompt = enable_skills_prompt
        self.skills_sources_for_prompt = skills_sources_for_prompt or ["/mnt/skills/"]
        # 实例级缓存：避免每次模型调用都查数据库
        self._dependency_map_cache: dict[str, SkillDependencyNode] | None = None
        self._prompt_metadata_cache: dict[str, SkillPromptMetadata] | None = None

    async def _get_dependency_map_cached(self) -> dict[str, SkillDependencyNode]:
        """获取依赖映射（带缓存）"""
        if self._dependency_map_cache is None:
            self._dependency_map_cache = await get_dependency_map()
        return self._dependency_map_cache

    async def _get_prompt_metadata_cached(self) -> dict[str, SkillPromptMetadata]:
        """获取提示词元数据（带缓存）"""
        if self._prompt_metadata_cache is None:
            self._prompt_metadata_cache = await get_prompt_metadata()
        return self._prompt_metadata_cache

    async def abefore_agent(self, state: SkillsState, runtime) -> dict[str, Any] | None:
        """在 agent 执行前注入 skills 提示词"""
        runtime_context = runtime.context

        # 检查是否需要注入
        if not self.enable_skills_prompt:
            return None
        if getattr(runtime_context, "_skills_prompt_injected", False):
            return None

        # 从数据库加载 skills 数据（使用缓存）
        dependency_map = await self._get_dependency_map_cached()

        # 获取配置的 skills
        configured_skills = getattr(runtime_context, self.skills_context_name, None) or []
        selected_skills = normalize_selected_skills(configured_skills)

        if not selected_skills:
            return None

        # 计算 visible_skills
        visible_skills = expand_skill_closure(selected_skills, dependency_map)

        if not visible_skills:
            return None

        # 收集提示词元数据并构建提示段
        skills_meta = await self._collect_prompt_metadata(visible_skills)
        skills_section = self._build_skills_section(skills_meta)

        # 注入提示词
        base_prompt = getattr(runtime_context, "system_prompt", "") or ""
        merged_prompt = f"{base_prompt}\n\n{skills_section}" if base_prompt else skills_section
        setattr(runtime_context, "system_prompt", merged_prompt)
        setattr(runtime_context, "_skills_prompt_injected", True)

        # 存储 visible_skills 供后续使用
        setattr(runtime_context, "_visible_skills", visible_skills)

        return None

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """包装模型调用，处理动态激活和依赖展开"""
        runtime_context = request.runtime.context

        # 从缓存加载 skills 数据
        dependency_map = await self._get_dependency_map_cached()

        # 1. 获取配置的 skills
        configured_skills = getattr(runtime_context, self.skills_context_name, None) or []
        configured = normalize_selected_skills(configured_skills)

        # 2. 获取运行时动态激活的 skills
        state = request.state if isinstance(request.state, dict) else {}
        activated = state.get("activated_skills", []) or []
        if not isinstance(activated, list):
            activated = []

        # 3. 合并并展开闭包
        all_skills = normalize_selected_skills(configured + activated)
        visible_skills = expand_skill_closure(all_skills, dependency_map)

        # 4. 更新 runtime_context 中的 visible_skills
        setattr(runtime_context, "_visible_skills", visible_skills)

        # 5. 构建依赖包（只从直接激活的 skills 获取依赖，不包含闭包展开的依赖）
        deps_bundle = await self._build_dependency_bundle(activated)

        # 6. 加载依赖的工具（普通工具 + MCP 工具）
        enabled_tools = []

        # 6.1 从 toolkits 获取普通工具
        if deps_bundle["tools"]:
            all_tools = get_all_tool_instances()
            required_tool_names = set(deps_bundle["tools"])
            enabled_tools = [t for t in all_tools if t.name in required_tool_names]

        # 6.2 获取 MCP 工具
        if deps_bundle["mcps"]:
            mcp_tools = await self._get_mcp_tools_from_context(
                runtime_context,
                extra_mcps=deps_bundle["mcps"],
            )
            enabled_tools.extend(mcp_tools)

        # 合并工具：保留原有工具 + 追加依赖的新工具
        if enabled_tools:
            existing_tool_names = {t.name for t in request.tools or []}
            merged_tools = list(request.tools or [])
            for t in enabled_tools:
                if t.name not in existing_tool_names:
                    merged_tools.append(t)
            request = request.override(tools=merged_tools)

        return await handler(request)

    async def _build_dependency_bundle(self, activated_skills: list[str]) -> dict[str, list[str]]:
        """根据直接激活的 skills 构建依赖包（不包含闭包展开的依赖）"""
        dependency_map = await self._get_dependency_map_cached()

        tools: list[str] = []
        mcps: list[str] = []
        seen_tools: set[str] = set()
        seen_mcps: set[str] = set()

        for slug in activated_skills:
            dep = dependency_map.get(slug, {})
            for tool_name in dep.get("tools", []):
                if tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
            for mcp_name in dep.get("mcps", []):
                if mcp_name in seen_mcps:
                    continue
                seen_mcps.add(mcp_name)
                mcps.append(mcp_name)

        return {"tools": tools, "mcps": mcps, "skills": activated_skills}

    async def _collect_prompt_metadata(self, slugs: list[str]) -> list[SkillPromptMetadata]:
        """收集指定 slugs 的提示词元数据"""
        prompt_metadata = await self._get_prompt_metadata_cached()

        result: list[SkillPromptMetadata] = []
        seen: set[str] = set()

        for slug in slugs:
            if not isinstance(slug, str):
                continue
            normalized = slug.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            item = prompt_metadata.get(normalized)
            if not item:
                logger.debug(f"Skill slug not found in prompt metadata, skip: {normalized}")
                continue
            result.append(dict(item))

        return result

    async def _get_mcp_tools_from_context(
        self,
        context,
        *,
        extra_mcps: list[str] | None = None,
    ) -> list:
        """从上下文配置中获取 MCP 工具列表"""
        import asyncio

        # MCP 工具（并行加载）
        mcps = getattr(context, "mcps", None) or []
        all_mcp_names: list[str] = []
        for server_name in mcps:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)
        for server_name in extra_mcps or []:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)

        # 去重
        unique_mcp_names = list(dict.fromkeys(all_mcp_names))

        async def load_mcp_tools(server_name: str) -> list:
            """加载单个 MCP 服务器的工具"""
            try:
                mcp_tools = await get_enabled_mcp_tools(server_name)
                if not mcp_tools:
                    logger.warning(f"SkillsMiddleware: mcp dependency unavailable, skip: {server_name}")
                return mcp_tools
            except Exception as e:
                logger.warning(f"SkillsMiddleware: failed to load mcp dependency '{server_name}': {e}")
                return []

        # 并行加载所有 MCP 工具
        results = await asyncio.gather(*[load_mcp_tools(name) for name in unique_mcp_names])
        selected_tools = []
        for tools in results:
            selected_tools.extend(tools)

        return selected_tools

    def _process_tool_call_result(self, result: Any, request: ToolCallRequest) -> Any:
        """处理工具调用结果，检查并处理 skill 动态激活"""
        if request.tool_call.get("name") != "read_file":
            return result

        args = request.tool_call.get("args") or {}
        file_path = args.get("file_path") if isinstance(args, dict) else None
        slug = self._extract_skill_slug_from_skill_md_path(file_path)

        if not slug:
            return result

        if not self._is_visible_skill_slug(request, slug):
            logger.warning(f"SkillsMiddleware: deny skill activation for invisible slug: {slug}")
            return result

        logger.debug(f"SkillsMiddleware: activated skill by read_file: {slug}")
        return self._merge_activated_skill_update(result, slug)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """包装工具调用，处理 skill 动态激活"""
        result = await handler(request)
        return self._process_tool_call_result(result, request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """同步版本的工具调用包装"""
        result = handler(request)
        return self._process_tool_call_result(result, request)

    def _extract_skill_slug_from_skill_md_path(self, file_path: Any) -> str | None:
        """从文件路径中提取 skill slug"""
        if not isinstance(file_path, str):
            return None
        raw = file_path.strip()
        if not raw:
            return None
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        parts = [p for p in pure.parts if p not in ("/", "")]
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            slug = parts[1]
        elif len(parts) == 4 and parts[0] == "mnt" and parts[1] == "skills" and parts[3] == "SKILL.md":
            slug = parts[2]
        else:
            return None
        if not is_valid_skill_slug(slug):
            return None
        return slug

    def _is_visible_skill_slug(self, request: ToolCallRequest, slug: str) -> bool:
        """检查 slug 是否可见"""
        runtime_context = request.runtime.context
        visible_skills = getattr(runtime_context, "_visible_skills", None)

        if isinstance(visible_skills, list):
            return slug in visible_skills

        # 后备：从配置的 skills 检查
        configured_skills = getattr(runtime_context, self.skills_context_name, None) or []
        normalized = normalize_selected_skills(configured_skills)
        return slug in normalized

    def _merge_activated_skill_update(self, result: Any, slug: str):
        """合并动态激活的 skill 更新"""
        from langchain_core.messages import ToolMessage

        if isinstance(result, Command):
            update = dict(result.update or {})
            current = update.get("activated_skills") or []
            update["activated_skills"] = _activated_skills_reducer(current, [slug])
            return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], "activated_skills": [slug]})

        return result

    def _format_skills_locations(self, sources: list[str]) -> str:
        """格式化 skills 位置信息"""
        locations = []
        for i, source_path in enumerate(sources):
            name = PurePosixPath(source_path.rstrip("/")).name.capitalize()
            suffix = " (higher priority)" if i == len(sources) - 1 else ""
            locations.append(f"**{name} Skills**: `{source_path}`{suffix}")
        return "\n".join(locations)

    def _format_skills_list(self, skills_meta: list[dict[str, str]]) -> str:
        """格式化 skills 列表"""
        if not skills_meta:
            return f"(No skills available yet. You can create skills in {' or '.join(self.skills_sources_for_prompt)})"

        lines = []
        for skill in skills_meta:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> Read `{skill['path']}` for full instructions")
        return "\n".join(lines)

    def _build_skills_section(self, skills_meta: list[dict[str, str]]) -> str:
        """构建 skills 提示段"""
        skills_locations = self._format_skills_locations(self.skills_sources_for_prompt)
        skills_list = self._format_skills_list(skills_meta)
        return SKILLS_SYSTEM_PROMPT.format(
            skills_locations=skills_locations,
            skills_list=skills_list,
        )
