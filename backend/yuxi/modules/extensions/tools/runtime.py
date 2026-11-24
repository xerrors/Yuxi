"""装配当前运行可执行的本地、插件 与 MCP 工具及来源。"""

from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.mcp.builtin import supports_direct_mcp_selection
from yuxi.modules.extensions.tools.builtin import BASE_TOOL_NAMES
from yuxi.modules.extensions.tools.catalog import get_tool_instances_by_category


async def resolve_configured_runtime_tools(context) -> list[Any]:
    """构图前装配工具并记录授权检查使用的真实来源。"""
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    context.mcp_tool_servers = {}
    context.mcp_tools = {}
    context.registered_builtin_tool_names = set()
    selected_tools = []
    selected_tool_names: set[str] = set()
    selected_tool_sources: dict[str, str] = {}
    builtin_tools = {tool.name: tool for tool in get_tool_instances_by_category("builtin")}
    from yuxi.modules.extensions.web.services import available_web_tools

    # 工具注册稳定，凭据决定本轮可绑定能力；执行时再次读取有效配置。
    requested_web = {"web_search", "web_fetch"} & set(getattr(context, "tools", None) or [])
    available_web = await available_web_tools() if requested_web else set()
    for name in {"web_search", "web_fetch"} - available_web:
        builtin_tools.pop(name, None)

    for tool_name in (*BASE_TOOL_NAMES, *(getattr(context, "tools", None) or [])):
        if not isinstance(tool_name, str) or tool_name in selected_tool_names:
            continue
        tool = builtin_tools.get(tool_name)
        if tool is None:
            logger.warning(f"Configured builtin tool not found, skip: {tool_name}")
            continue
        selected_tools.append(tool)
        selected_tool_names.add(tool_name)
        selected_tool_sources[tool_name] = "local"
        context.registered_builtin_tool_names.add(tool_name)

    loaded = await resolve_runtime_mcp_tools(
        [slug for slug in (getattr(context, "mcps", None) or []) if supports_direct_mcp_selection(slug)],
        uid=getattr(context, "uid", None),
        run_tools=context.mcp_tools,
        unavailable=getattr(context, "mcp_unavailable", None),
        context=context,
    )

    for server_name, mcp_tools in loaded.items():
        if not mcp_tools:
            logger.info(f"Configured MCP returned no enabled tools: {server_name}")
            continue
        for tool in mcp_tools:
            if tool.name in selected_tool_names:
                raise RuntimeError(f"工具名冲突：MCP '{server_name}' 的 '{tool.name}' 与 {selected_tool_sources[tool.name]} 工具同名")
            selected_tools.append(tool)
            selected_tool_names.add(tool.name)
            selected_tool_sources[tool.name] = f"MCP '{server_name}'"
            context.mcp_tool_servers[tool.name] = server_name

    return selected_tools
