"""MCP Service - Facade 适配门面，保持项目的完全向下兼容。

所有的实质逻辑均已根据职责分工拆分重构至以下子服务模块中：
- yuxi.services.mcp.server_service
- yuxi.services.mcp.connection_service
- yuxi.services.mcp.tool_registry_service
- yuxi.services.mcp.client_pool
"""

from __future__ import annotations
import asyncio
from typing import Any

from yuxi.services.mcp.server_service import (
    ensure_builtin_mcp_servers_in_db,
    get_enabled_mcp_server_names,
    get_mcp_server,
    get_all_mcp_servers,
    create_mcp_server,
    update_mcp_server,
    delete_mcp_server,
    get_mcp_server_dependency_summary,
    set_server_enabled,
    get_servers_config,
)
from yuxi.services.mcp.connection_service import (
    get_mcp_connection,
    list_mcp_connections,
    create_mcp_connection,
    update_mcp_connection,
    delete_mcp_connection,
    set_mcp_connection_status,
    reauthorize_mcp_connection,
    test_mcp_connection,
    _resolve_scope_id,
)
from yuxi.services.mcp.tool_registry_service import (
    to_camel_case,
    get_tools_from_all_servers,
    clear_mcp_cache,
    clear_mcp_server_tools_cache,
    clear_mcp_connection_tools_cache,
    invalidate_mcp_server_tools_cache,
    invalidate_mcp_connection_tools_cache,
    get_mcp_tools_stats,
    get_enabled_mcp_tools,
    get_all_mcp_tools,
    toggle_tool_enabled,
)

# 兼容原导入以防万一
from yuxi.services.mcp_auth.orchestrator import AuthContext
from langchain_mcp_adapters.client import MultiServerMCPClient
from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache
from yuxi.services.mcp_tool_cache import RedisMcpToolCache
from yuxi.utils import logger

# -----------------------------------------------------------------------------
# --- 共享状态与依赖（提供给外部/子服务使用，并对单元测试 Mock 100% 兼容） ---
# -----------------------------------------------------------------------------
_mcp_tools_cache = {}
_mcp_tool_cache_store = RedisMcpToolCache()
_mcp_tools_stats = {}
_mcp_lock = asyncio.Lock()


# -----------------------------------------------------------------------------
# --- 兼容性转发入口（支持被测试 monkeypatch.setattr 覆盖） ---
# -----------------------------------------------------------------------------
async def get_enabled_mcp_server_config(server_name: str, *, db: Any = None) -> dict[str, Any] | None:
    from yuxi.services.mcp.server_service import get_enabled_mcp_server_config as _get_cfg
    return await _get_cfg(server_name, db=db)


async def get_runtime_mcp_server_config(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: Any = None,
    http_client: Any = None,
) -> dict[str, Any] | None:
    from yuxi.services.mcp.server_service import get_runtime_mcp_server_config as _get_run_cfg
    return await _get_run_cfg(server_name, auth_context=auth_context, db=db, http_client=http_client)


async def _load_enabled_mcp_server_configs(names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    from yuxi.services.mcp.server_service import _load_enabled_mcp_server_configs as _load_cfg
    return await _load_cfg(names=names)


async def get_mcp_tools(server_name: str, **kwargs: Any) -> list[Any]:
    from yuxi.services.mcp.tool_registry_service import get_mcp_tools as _get_t
    return await _get_t(server_name, **kwargs)


async def get_mcp_client(
    server_configs: dict[str, Any] | None = None,
) -> MultiServerMCPClient | None:
    """初始化并拉起 MCP 客户端。保留该底层入口以确保单元测试中的 monkeypatch 拦截顺畅传导。"""
    try:
        client = MultiServerMCPClient(server_configs)  # pyright: ignore[reportArgumentType]
        logger.info(f"Initialized MCP client with servers: {list(server_configs.keys())}")
        return client
    except Exception as e:
        logger.error(f"Failed to initialize MCP client: {e}")
        return None


async def _clear_mcp_server_runtime_auth_cache(db: Any, server_name: str) -> None:
    from yuxi.services.mcp.tool_registry_service import _clear_mcp_server_runtime_auth_cache as _clear_auth
    await _clear_auth(db, server_name)
