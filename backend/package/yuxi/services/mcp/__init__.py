from __future__ import annotations

from yuxi.services.mcp.cache_policy import (
    CachePolicyFactory,
    DynamicProxyCachePolicy,
    MCPCachePolicy,
    StaticCachePolicy,
    TokenInjectedCachePolicy,
)
from yuxi.services.mcp.client_pool import (
    MCPClientPool,
    mcp_client_pool,
)
from yuxi.services.mcp.connection_service import (
    create_mcp_connection,
    delete_mcp_connection,
    get_mcp_connection,
    list_mcp_connections,
    reauthorize_mcp_connection,
    set_mcp_connection_status,
    test_mcp_connection,
    update_mcp_connection,
)
from yuxi.services.mcp.server_service import (
    create_mcp_server,
    delete_mcp_server,
    ensure_builtin_mcp_servers_in_db,
    get_all_mcp_servers,
    get_enabled_mcp_server_config,
    get_enabled_mcp_server_names,
    get_mcp_server,
    get_mcp_server_dependency_summary,
    get_runtime_mcp_server_config,
    get_servers_config,
    set_server_enabled,
    update_mcp_server,
)
from yuxi.services.mcp.tool_registry_service import (
    clear_mcp_cache,
    clear_mcp_connection_tools_cache,
    clear_mcp_server_tools_cache,
    get_all_mcp_tools,
    get_enabled_mcp_tools,
    get_mcp_tools,
    get_mcp_tools_stats,
    get_tools_from_all_servers,
    invalidate_mcp_connection_tools_cache,
    invalidate_mcp_server_tools_cache,
    to_camel_case,
)

__all__ = [
    # 策略模式与对象池
    "MCPCachePolicy",
    "StaticCachePolicy",
    "TokenInjectedCachePolicy",
    "DynamicProxyCachePolicy",
    "CachePolicyFactory",
    "mcp_client_pool",
    "MCPClientPool",
    # Server CRUD
    "ensure_builtin_mcp_servers_in_db",
    "get_enabled_mcp_server_config",
    "get_runtime_mcp_server_config",
    "get_enabled_mcp_server_names",
    "get_mcp_server",
    "get_all_mcp_servers",
    "create_mcp_server",
    "update_mcp_server",
    "delete_mcp_server",
    "get_mcp_server_dependency_summary",
    "set_server_enabled",
    "get_servers_config",
    # Connection CRUD
    "get_mcp_connection",
    "list_mcp_connections",
    "create_mcp_connection",
    "update_mcp_connection",
    "delete_mcp_connection",
    "set_mcp_connection_status",
    "reauthorize_mcp_connection",
    "test_mcp_connection",
    # Tool Registry
    "to_camel_case",
    "get_mcp_tools",
    "get_tools_from_all_servers",
    "clear_mcp_cache",
    "clear_mcp_server_tools_cache",
    "clear_mcp_connection_tools_cache",
    "invalidate_mcp_server_tools_cache",
    "invalidate_mcp_connection_tools_cache",
    "get_mcp_tools_stats",
    "get_enabled_mcp_tools",
    "get_all_mcp_tools",
]
