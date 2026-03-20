from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from yuxi.agents import load_chat_model
from yuxi.agents.toolkits import get_all_tool_instances
from yuxi.services.mcp_service import get_enabled_mcp_tools
from yuxi.utils.datetime_utils import shanghai_now
from yuxi.utils.logging_config import logger


class RuntimeConfigMiddleware(AgentMiddleware):
    """运行时配置中间件 - 应用模型/工具/MCP/提示词配置

    知识库工具已移至独立的 KnowledgeBaseMiddleware
    Skills 功能已移至独立的 SkillsMiddleware

    支持自定义上下文字段名称，以便在不同场景（如主智能体/子智能体）使用不同的配置字段
    """

    def __init__(
        self,
        *,
        extra_tools: list[Any] | None = None,
        model_context_name: str = "model",
        system_prompt_context_name: str = "system_prompt",
        tools_context_name: str = "tools",
        knowledges_context_name: str = "knowledges",
        mcps_context_name: str = "mcps",
        enable_model_override: bool = True,
        enable_system_prompt_override: bool = True,
        enable_tools_override: bool = True,
    ):
        """初始化中间件

        Args:
            extra_tools: 额外工具列表（从 create_agent 的 tools 参数传入）
            model_context_name: 上下文中的模型字段名称（默认 "model"）
            system_prompt_context_name: 上下文中的系统提示词字段名称（默认 "system_prompt"）
            tools_context_name: 上下文中的工具列表字段名称（默认 "tools"）
            knowledges_context_name: 上下文中的知识库列表字段名称（默认 "knowledges"）
            mcps_context_name: 上下文中的 MCP 服务器列表字段名称（默认 "mcps"）
            enable_model_override: 是否允许覆盖模型配置（默认 True）
            enable_system_prompt_override: 是否允许覆盖系统提示词（默认 True）
            enable_tools_override: 是否允许覆盖工具列表（默认 True）
        """
        super().__init__()
        # 存储自定义字段名称
        self.model_context_name = model_context_name
        self.system_prompt_context_name = system_prompt_context_name
        self.tools_context_name = tools_context_name
        self.knowledges_context_name = knowledges_context_name
        self.mcps_context_name = mcps_context_name
        # 存储覆盖配置
        self.enable_model_override = enable_model_override
        self.enable_system_prompt_override = enable_system_prompt_override
        self.enable_tools_override = enable_tools_override

        self.tools: list[Any] = []
        # 预加载工具列表（仅当启用工具覆盖时）
        # 注意：知识库工具已移至独立的 KnowledgeBaseMiddleware
        if self.enable_tools_override:
            self.base_tools = get_all_tool_instances()
            self.tools = self.base_tools + (extra_tools or [])
        elif extra_tools:
            logger.warning(
                "RuntimeConfigMiddleware: extra_tools 参数已提供，但 enable_tools_override=False，"
                "将忽略 extra_tools 并不会应用任何工具覆盖。"
            )

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        runtime_context = request.runtime.context
        overrides: dict[str, Any] = {}

        # 1. 模型覆盖（可选）
        if self.enable_model_override:
            model = load_chat_model(getattr(runtime_context, self.model_context_name, None))
            overrides["model"] = model

        # 2. 工具覆盖（可选）
        # 注意：Skills 依赖的工具加载已移至 SkillsMiddleware
        if self.enable_tools_override:
            # 获取上下文配置的工具
            enabled_tools = await self.get_tools_from_context(runtime_context)
            existing_tools = list(request.tools or [])
            enabled_tool_names = {t.name for t in enabled_tools}
            managed_tool_names = {t.name for t in self.tools}
            merged_tools = []
            for t_bind in existing_tools:
                # (1) 已启用的工具保留
                # (2) 非本中间件管理的工具保留
                if t_bind.name in enabled_tool_names or t_bind.name not in managed_tool_names:
                    merged_tools.append(t_bind)
            overrides["tools"] = merged_tools
            logger.debug(f"RuntimeConfigMiddleware selected tools: {[t.name for t in merged_tools]}")

        # 3. 系统提示词覆盖（可选）
        if self.enable_system_prompt_override:
            cur_datetime = f"当前时间：{shanghai_now().strftime('%Y-%m-%d %H:%M:%S')} UTC"
            system_prompt = getattr(runtime_context, self.system_prompt_context_name, "") or ""
            merged_system_prompt = f"{cur_datetime}\n\n{system_prompt}"

            content_blocks = list(request.system_message.content_blocks) if request.system_message else []
            new_content = content_blocks + [{"type": "text", "text": merged_system_prompt}]
            new_system_message = SystemMessage(content=new_content)
            overrides["system_message"] = new_system_message

        if overrides:
            request = request.override(**overrides)

        return await handler(request)

    async def get_tools_from_context(self, context) -> list:
        """从上下文配置中获取工具列表"""
        selected_tools = []
        selected_tool_names: set[str] = set()

        # 1. 基础工具 (从 context.tools 中筛选)
        tools = getattr(context, self.tools_context_name, None) or []
        all_tool_names = []
        for tool_name in tools:
            if isinstance(tool_name, str):
                all_tool_names.append(tool_name)

        tools_map = {t.name: t for t in self.tools}
        for tool_name in all_tool_names:
            if tool_name in selected_tool_names:
                continue
            if tool_name in tools_map:
                selected_tools.append(tools_map[tool_name])
                selected_tool_names.add(tool_name)
                continue
            logger.warning(f"RuntimeConfigMiddleware: tool dependency not found, skip: {tool_name}")

        # 2. MCP 工具（使用统一入口，自动过滤 disabled_tools）
        mcps = getattr(context, self.mcps_context_name, None) or []
        all_mcp_names: list[str] = []
        for server_name in mcps:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)

        selected_mcp_servers: set[str] = set()
        for server_name in all_mcp_names:
            if server_name in selected_mcp_servers:
                continue
            selected_mcp_servers.add(server_name)
            try:
                mcp_tools = await get_enabled_mcp_tools(server_name)
                if not mcp_tools:
                    logger.warning(f"RuntimeConfigMiddleware: mcp dependency unavailable, skip: {server_name}")
                selected_tools.extend(mcp_tools)
            except Exception as e:
                logger.warning(f"RuntimeConfigMiddleware: failed to load mcp dependency '{server_name}': {e}")

        return selected_tools
