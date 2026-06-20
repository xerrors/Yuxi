"""MCP tool discovery and cache service.

Responsibilities:
- Unified entry point for Agent tool retrieval (auto-filtering disabled_tools)
- MCP Client and Tools management (formerly in agents/common/mcp.py)
"""

import asyncio
import hashlib
import json
import re
import traceback
from collections.abc import Callable
from typing import Any, cast

from langchain_mcp_adapters.client import MultiServerMCPClient
from yuxi.services.mcp.server_service import _load_enabled_mcp_server_configs, get_enabled_mcp_server_config
from yuxi.utils import logger

# =============================================================================
# === Global Cache & State ===
# =============================================================================

# Per-server locks to prevent concurrent duplicate initialization (Cache Stampede protection)
_server_locks: dict[str, asyncio.Lock] = {}


def _get_server_lock(server_name: str) -> asyncio.Lock:
    """Get or create a lock for the given server name."""
    if server_name not in _server_locks:
        _server_locks[server_name] = asyncio.Lock()
    return _server_locks[server_name]


# 本地仅缓存工具对象。配置始终以数据库为准，每次按 server_name 现查。
# cache key 使用 server_name:config_hash，当配置变化时会自然失效。
_mcp_tools_cache: dict[str, list[Callable[..., Any]]] = {}

# MCP tools statistics (for reporting enabled/disabled counts)
_mcp_tools_stats: dict[str, dict[str, int]] = {}

MCP_TOOLS_DISCOVERY_TIMEOUT_SECONDS = 10

# =============================================================================
# === Core Logic (Moved from agents/common/mcp.py) ===
# =============================================================================


async def get_mcp_client(
    server_configs: dict[str, Any] | None = None,
) -> MultiServerMCPClient | None:
    """Initializes an MCP client with the given server configurations."""
    try:
        client = MultiServerMCPClient(server_configs)  # pyright: ignore[reportArgumentType]
        logger.info(f"Initialized MCP client with servers: {list(server_configs.keys())}")
        return client
    except Exception as e:
        logger.error("Failed to initialize MCP client: {}", e)
        return None


def to_camel_case(s: str) -> str:
    """Convert string to lowerCamelCase."""

    # Handle - and _
    s = re.sub(r"[-_]+(.)", lambda m: m.group(1).upper(), s)
    # Lowercase first letter
    if len(s) > 0:
        s = s[0].lower() + s[1:]
    return s


async def get_mcp_tools(
    server_name: str,
    additional_servers: dict[str, dict[str, Any]] | None = None,
    disabled_tools: list[str] | None = None,
    cache: bool = True,
    force_refresh: bool = False,
) -> list[Callable[..., Any]]:
    """Get MCP tools for a specific server.

    Architecture:
    1. Fetching: Connects to MCP server to get ALL tools.
    2. Caching: Stores the FULL, UNFILTERED list of tools in `_mcp_tools_cache`.
    3. Filtering: Filters the return value based on `disabled_tools` argument.

    Args:
        server_name: Server name
        additional_servers: Additional server configurations
        disabled_tools: List of tool names to filter out from the RETURN value (does not affect cache)
        cache: Whether to use/update the cache (default: True)
        force_refresh: Whether to force a refresh from the server (default: False)
    """
    if additional_servers and server_name in additional_servers:
        server_config = additional_servers[server_name]
    else:
        server_config = await get_enabled_mcp_server_config(server_name)

    if server_config is None:
        logger.warning(f"MCP server '{server_name}' not found in database or disabled")
        return []

    # 配置 hash 直接基于完整配置生成。只要数据库中的配置发生变化，
    # 本地工具缓存 key 就会变化，从而自然触发重建。
    config_payload = json.dumps(server_config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
    cache_key = f"{server_name}:{config_hash}"

    all_processed_tools: list[Callable[..., Any]] = []

    # 使用 per-server lock + double-check 模式，仅阻塞同一 server 的并发请求
    server_lock = _get_server_lock(server_name)
    async with server_lock:
        if not force_refresh and cache and cache_key in _mcp_tools_cache:
            all_processed_tools = _mcp_tools_cache[cache_key]

        if not all_processed_tools:
            try:
                # disabled_tools 只影响返回值过滤，不参与 MCP client 建连参数。
                client_config = {k: v for k, v in server_config.items() if k not in ("disabled_tools",)}

                client = await get_mcp_client({server_name: client_config})
                if client is None:
                    return []

                raw_tools = cast(list[Any], await client.get_tools())

                server_cc = to_camel_case(server_name)
                for tool in raw_tools:
                    original_name = tool.name
                    tool_cc = to_camel_case(original_name)
                    unique_id = f"mcp__{server_cc}__{tool_cc}"

                    if tool.metadata is None:
                        tool.metadata = {}
                    tool.metadata["id"] = unique_id
                    # 开启错误处理，防止工具调用抛出 ToolException 时击穿服务
                    tool.handle_tool_error = True
                    all_processed_tools.append(tool)

                if cache:
                    stale_keys = [
                        key for key in _mcp_tools_cache if key.startswith(f"{server_name}:") and key != cache_key
                    ]
                    for stale_key in stale_keys:
                        _mcp_tools_cache.pop(stale_key, None)
                    _mcp_tools_cache[cache_key] = all_processed_tools

                    global_config_disabled = server_config.get("disabled_tools") or []
                    enabled_count = len([t for t in all_processed_tools if t.name not in global_config_disabled])
                    _mcp_tools_stats[server_name] = {
                        "total": len(all_processed_tools),
                        "enabled": enabled_count,
                        "disabled": len(all_processed_tools) - enabled_count,
                    }

                    logger.info(
                        f"Refreshed MCP tools cache for '{server_name}' with key '{cache_key}': "
                        f"{len(all_processed_tools)} tools loaded."
                    )

            except Exception as e:
                logger.error(
                    f"Failed to load tools from MCP server '{server_name}': {e}, traceback: {traceback.format_exc()}"
                )
                return []

    # 3. Filtering (Apply to Return Value Only)
    if disabled_tools:
        filtered_tools = [t for t in all_processed_tools if t.name not in disabled_tools]
        logger.debug(
            f"Returning {len(filtered_tools)}/{len(all_processed_tools)} tools for '{server_name}' "
            f"(filtered {len(disabled_tools)} by argument)"
        )
        return filtered_tools

    return all_processed_tools


async def get_tools_from_all_servers() -> list[Callable[..., Any]]:
    """Get all tools from all configured MCP servers."""
    server_configs = await _load_enabled_mcp_server_configs()
    all_tools = []
    for server_name in server_configs:
        tools = await get_mcp_tools(server_name, additional_servers=server_configs)
        all_tools.extend(tools)
    return all_tools


def clear_mcp_cache() -> None:
    """Clear the MCP tools cache (useful for testing)."""
    global _mcp_tools_cache, _mcp_tools_stats, _server_locks
    _mcp_tools_cache = {}
    _mcp_tools_stats = {}
    _server_locks = {}


def clear_mcp_server_tools_cache(server_name: str) -> None:
    """Clear the tools cache for a specific MCP server."""
    global _mcp_tools_cache, _mcp_tools_stats
    server_prefix = f"{server_name}:"
    stale_keys = [key for key in _mcp_tools_cache if key.startswith(server_prefix)]
    for stale_key in stale_keys:
        _mcp_tools_cache.pop(stale_key, None)
    _mcp_tools_stats.pop(server_name, None)
    _server_locks.pop(server_name, None)
    logger.info(f"Cleared tools cache for MCP server '{server_name}'")


def get_mcp_tools_stats(server_name: str) -> dict[str, int] | None:
    """Get tools statistics for a MCP server.

    Returns:
        dict with 'total', 'enabled', 'disabled' counts, or None if not available
    """
    return _mcp_tools_stats.get(server_name)


# =============================================================================
# === Unified Entry Points (Wrappers) ===
# =============================================================================


async def get_enabled_mcp_tools(server_name: str) -> list:
    """Get MCP server tools (auto-filtering disabled_tools).

    Unified entry point for Agents, automatically:
    1. Gets the latest server config from database
    2. Gets all tools
    3. Filters out disabled_tools

    Args:
        server_name: Server name

    Returns:
        List of enabled tools
    """
    config = await get_enabled_mcp_server_config(server_name)
    if config is None:
        logger.warning(f"MCP server '{server_name}' not found in database or disabled")
        return []

    disabled_tools = config.get("disabled_tools") or []
    try:
        return await asyncio.wait_for(
            get_mcp_tools(server_name, additional_servers={server_name: config}, disabled_tools=disabled_tools),
            timeout=MCP_TOOLS_DISCOVERY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            f"MCP server '{server_name}' tool discovery timed out after {MCP_TOOLS_DISCOVERY_TIMEOUT_SECONDS}s, skip"
        )
        return []


async def get_all_mcp_tools(server_name: str) -> list:
    """Get all tools of an MCP server (no filtering).

    For management UI to display tool list, supports viewing all tools and their enabled status.
    Does NOT update the global tools cache to avoid polluting agent's filtered view.

    Args:
        server_name: Server name

    Returns:
        List of all tools (unfiltered)
    """
    config = await get_enabled_mcp_server_config(server_name)
    if config is None:
        logger.warning(f"MCP server '{server_name}' not found in database or disabled")
        return []

    # Get all tools (no filtering, force refresh, no cache update)
    return await get_mcp_tools(
        server_name,
        additional_servers={server_name: config},
        disabled_tools=[],
        cache=False,
        force_refresh=True,
    )
