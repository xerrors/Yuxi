from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from yuxi.services.mcp_service import get_mcp_tools
from yuxi.utils import logger


class DynamicToolMiddleware(AgentMiddleware):
    """动态工具选择中间件 - 支持 MCP 工具的动态加载和注册

    注意：所有可能用到的 MCP 工具必须在初始化时预加载并注册到 self.tools
    运行时只是根据配置筛选工具，不能动态添加新工具
    """

    def __init__(self, base_tools: list[Any], mcp_servers: list[str] | None = None):
        """初始化中间件

        Args:
            base_tools: 基础工具列表
            mcp_servers: 需要预加载的 MCP 服务器列表（可选）
        """
        super().__init__()
        self.tools: list[Any] = base_tools
        self._all_mcp_tools: dict[str, list[Any]] = {}  # 所有已加载的 MCP 工具
        self._mcp_servers = mcp_servers or []

    async def initialize_mcp_tools(self) -> None:
        """异步初始化：预加载所有可能用到的 MCP 工具"""
        for mcp_name in self._mcp_servers:
            if mcp_name not in self._all_mcp_tools:
                logger.info(f"Pre-loading MCP tools from: {mcp_name}")
                mcp_tools = await get_mcp_tools(mcp_name)
                self._all_mcp_tools[mcp_name] = mcp_tools
                # 将 MCP 工具注册到 middleware.tools
                self.tools.extend(mcp_tools)
                logger.info(f"Registered {len(mcp_tools)} tools from {mcp_name}")

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """根据配置动态选择工具（从已注册的工具中筛选）"""
        # 从 runtime context 获取配置
        selected_tools = request.runtime.context.tools
        selected_mcps = request.runtime.context.mcps

        enabled_tools = []

        # 根据配置筛选基础工具
        if selected_tools and isinstance(selected_tools, list) and len(selected_tools) > 0:
            enabled_tools = [tool for tool in self.tools if tool.name in selected_tools]

        # 根据配置筛选 MCP 工具（从已注册的工具中选择）
        if selected_mcps and isinstance(selected_mcps, list) and len(selected_mcps) > 0:
            for mcp in selected_mcps:
                if mcp in self._all_mcp_tools:
                    enabled_tools.extend(self._all_mcp_tools[mcp])
                else:
                    logger.warning(f"MCP server '{mcp}' not pre-loaded. Please add it to mcp_servers list.")

        logger.info(
            f"Dynamic tool selection: {len(enabled_tools)} tools enabled: {[tool.name for tool in enabled_tools]}, "
            f"selected_tools: {selected_tools}, selected_mcps: {selected_mcps}"
        )

        # 更新 request 中的工具列表
        request = request.override(tools=enabled_tools)
        return await handler(request)
