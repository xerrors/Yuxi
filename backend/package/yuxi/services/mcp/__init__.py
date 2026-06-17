"""MCP service package."""

from . import server_service, tool_registry_service
from yuxi.services.mcp.server_service import (
    create_mcp_server,
    delete_mcp_server,
    ensure_builtin_mcp_servers_in_db,
    get_all_mcp_servers,
    get_enabled_mcp_server_config,
    get_enabled_mcp_server_names,
    get_mcp_server,
    get_servers_config,
    set_server_enabled,
    toggle_tool_enabled,
    update_mcp_server,
)
from yuxi.services.mcp.tool_registry_service import (
    clear_mcp_cache,
    clear_mcp_server_tools_cache,
    get_all_mcp_tools,
    get_enabled_mcp_tools,
    get_mcp_client,
    get_mcp_tools,
    get_mcp_tools_stats,
    get_tools_from_all_servers,
    to_camel_case,
)

__all__ = [
    "clear_mcp_cache",
    "clear_mcp_server_tools_cache",
    "create_mcp_server",
    "delete_mcp_server",
    "ensure_builtin_mcp_servers_in_db",
    "get_all_mcp_servers",
    "get_all_mcp_tools",
    "get_enabled_mcp_server_config",
    "get_enabled_mcp_server_names",
    "get_enabled_mcp_tools",
    "get_mcp_client",
    "get_mcp_server",
    "get_mcp_tools",
    "get_mcp_tools_stats",
    "get_servers_config",
    "get_tools_from_all_servers",
    "server_service",
    "set_server_enabled",
    "to_camel_case",
    "toggle_tool_enabled",
    "tool_registry_service",
    "update_mcp_server",
]
