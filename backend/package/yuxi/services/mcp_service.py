"""MCP Service - Unified business logic and state management for MCP.

Responsibilities:
- Server configuration CRUD operations
- Built-in configuration synchronization (Code <-> Database)
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import MCPServer
from yuxi.utils import logger

# =============================================================================
# === Global Cache & State ===
# =============================================================================

# Global Lock for MCP state
_mcp_lock = asyncio.Lock()

# 本地仅缓存工具对象。配置始终以数据库为准，每次按 server_name 现查。
# cache key 使用 server_name:config_hash，当配置变化时会自然失效。
_mcp_tools_cache: dict[str, list[Callable[..., Any]]] = {}

# MCP tools statistics (for reporting enabled/disabled counts)
_mcp_tools_stats: dict[str, dict[str, int]] = {}
_UNSET = object()

# Default MCP Server configurations (Imported to DB on first run)
_DEFAULT_MCP_SERVERS = {
    "sequentialthinking": {
        "url": "https://remote.mcpservers.org/sequentialthinking/mcp",
        "transport": "streamable_http",
        "description": "顺序思考工具，帮助 AI 将复杂问题分解为多个步骤",
        "icon": "🧠",
        "tags": ["内置", "AI"],
    },
    "mcp-server-chart": {
        "command": "npx",
        "args": ["-y", "@antv/mcp-server-chart"],
        "transport": "stdio",
        "description": "图表生成工具，支持生成各类图表（柱状图、折线图、饼图等）",
        "icon": "📊",
        "tags": ["内置", "图表"],
    },
}

_SYNCED_MCP_FIELDS = (
    "description",
    "transport",
    "url",
    "command",
    "args",
    "env",
    "headers",
    "timeout",
    "sse_read_timeout",
    "tags",
    "icon",
)

# =============================================================================
# === Core Logic (Moved from agents/common/mcp.py) ===
# =============================================================================


async def ensure_builtin_mcp_servers_in_db() -> None:
    """Ensure built-in MCP server definitions exist in the database.

    This function only synchronizes code-defined built-ins to the database.
    It does not preload runtime configuration into memory.
    """
    # Delayed import to avoid circular references
    from yuxi.storage.postgres.manager import pg_manager

    try:
        async with pg_manager.get_async_session_context() as session:
            # Check if database has MCP configurations
            result = await session.execute(select(func.count(MCPServer.name)))
            count = result.scalar()

            if count == 0:
                # Database is empty, import default configurations
                logger.info("No MCP servers in database, importing default configurations...")
                for name, config in _DEFAULT_MCP_SERVERS.items():
                    server = MCPServer(
                        name=name,
                        description=config.get("description"),
                        transport=config["transport"],
                        url=config.get("url"),
                        command=config.get("command"),
                        args=config.get("args"),
                        env=config.get("env"),
                        headers=config.get("headers"),
                        timeout=config.get("timeout"),
                        sse_read_timeout=config.get("sse_read_timeout"),
                        tags=config.get("tags"),
                        icon=config.get("icon"),
                        enabled=0,
                        created_by="system",
                        updated_by="system",
                    )
                    session.add(server)
                await session.commit()
                logger.info(f"Imported {len(_DEFAULT_MCP_SERVERS)} default MCP servers to database")
            else:
                # Ensure all built-in MCP servers exist in database
                for name, config in _DEFAULT_MCP_SERVERS.items():
                    result = await session.execute(select(MCPServer).filter(MCPServer.name == name))
                    existing = result.scalar_one_or_none()
                    if not existing:
                        server = MCPServer(
                            name=name,
                            description=config.get("description"),
                            transport=config["transport"],
                            url=config.get("url"),
                            command=config.get("command"),
                            args=config.get("args"),
                            env=config.get("env"),
                            headers=config.get("headers"),
                            timeout=config.get("timeout"),
                            sse_read_timeout=config.get("sse_read_timeout"),
                            tags=config.get("tags"),
                            icon=config.get("icon"),
                            enabled=0,
                            created_by="system",
                            updated_by="system",
                        )
                        session.add(server)
                        logger.info(f"Added built-in MCP server '{name}' to database")
                    else:
                        changed = False
                        for field in _SYNCED_MCP_FIELDS:
                            next_value = config.get(field)
                            if getattr(existing, field) != next_value:
                                setattr(existing, field, next_value)
                                changed = True
                        if changed:
                            existing.updated_by = "system"
                # Commit if any new servers were added (check session state)
                if session.new:
                    await session.commit()
                elif session.dirty:
                    await session.commit()

    except Exception as e:
        logger.error(f"Failed to ensure builtin MCP servers in database: {e}, traceback: {traceback.format_exc()}")


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


async def _load_enabled_mcp_server_configs(
    *,
    names: list[str] | None = None,
    db: AsyncSession | None = None,
) -> dict[str, dict[str, Any]]:
    """Load enabled MCP server configs directly from the database."""
    if db is not None:
        stmt = select(MCPServer).where(MCPServer.enabled == 1)
        if names:
            stmt = stmt.where(MCPServer.name.in_(names))
        result = await db.execute(stmt)
        servers = result.scalars().all()
        return {server.name: server.to_mcp_config() for server in servers}

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await _load_enabled_mcp_server_configs(names=names, db=session)


async def get_enabled_mcp_server_config(server_name: str, *, db: AsyncSession | None = None) -> dict[str, Any] | None:
    """Get the latest enabled MCP server config from the database."""
    configs = await _load_enabled_mcp_server_configs(names=[server_name], db=db)
    return configs.get(server_name)


async def get_enabled_mcp_server_names(*, db: AsyncSession | None = None) -> list[str]:
    """Get enabled MCP server names from the database."""
    configs = await _load_enabled_mcp_server_configs(db=db)
    return list(configs.keys())


async def get_mcp_tools(
    server_name: str,
    additional_servers: dict[str, dict[str, Any]] | None = None,
    disabled_tools: list[str] = None,
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

    async with _mcp_lock:
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
                async with _mcp_lock:
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
    global _mcp_tools_cache, _mcp_tools_stats
    _mcp_tools_cache = {}
    _mcp_tools_stats = {}


def clear_mcp_server_tools_cache(server_name: str) -> None:
    """Clear the tools cache for a specific MCP server."""
    global _mcp_tools_cache, _mcp_tools_stats
    server_prefix = f"{server_name}:"
    stale_keys = [key for key in _mcp_tools_cache if key.startswith(server_prefix)]
    for stale_key in stale_keys:
        _mcp_tools_cache.pop(stale_key, None)
    _mcp_tools_stats.pop(server_name, None)
    logger.info(f"Cleared tools cache for MCP server '{server_name}'")


def get_mcp_tools_stats(server_name: str) -> dict[str, int] | None:
    """Get tools statistics for a MCP server.

    Returns:
        dict with 'total', 'enabled', 'disabled' counts, or None if not available
    """
    return _mcp_tools_stats.get(server_name)


# =============================================================================
# === Server Config CRUD (Existing in mcp_service.py) ===
# =============================================================================


async def get_mcp_server(db: AsyncSession, name: str) -> MCPServer | None:
    """Get single server configuration."""
    result = await db.execute(select(MCPServer).filter(MCPServer.name == name))
    return result.scalar_one_or_none()


async def get_all_mcp_servers(db: AsyncSession) -> list[MCPServer]:
    """Get all server configurations."""
    result = await db.execute(select(MCPServer))
    return list(result.scalars().all())


async def create_mcp_server(
    db: AsyncSession,
    name: str,
    transport: str,
    url: str = None,
    command: str = None,
    args: list = None,
    env: dict = None,
    description: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    created_by: str = None,
) -> MCPServer:
    """Create server."""
    # Check if name exists
    existing = await get_mcp_server(db, name)
    if existing:
        raise ValueError(f"Server name '{name}' already exists")

    server = MCPServer(
        name=name,
        description=description,
        transport=transport,
        url=url,
        command=command,
        args=args,
        env=env,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        tags=tags,
        icon=icon,
        enabled=1,
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    clear_mcp_server_tools_cache(name)

    logger.info(f"Created MCP server '{name}'")
    return server


async def update_mcp_server(
    db: AsyncSession,
    name: str,
    description: str = None,
    transport: str = None,
    url: str = None,
    command: str = None,
    args: list = None,
    env: Any = _UNSET,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    updated_by: str = None,
) -> MCPServer:
    """Update server configuration."""
    server = await get_mcp_server(db, name)
    if not server:
        raise ValueError(f"Server '{name}' does not exist")

    if description is not None:
        server.description = description
    if transport is not None:
        server.transport = transport
    if url is not None:
        server.url = url
    if command is not None:
        server.command = command
    if args is not None:
        server.args = args
    if env is not _UNSET:
        server.env = env
    if headers is not None:
        server.headers = headers
    if timeout is not None:
        server.timeout = timeout
    if sse_read_timeout is not None:
        server.sse_read_timeout = sse_read_timeout
    if tags is not None:
        server.tags = tags
    if icon is not None:
        server.icon = icon
    if updated_by is not None:
        server.updated_by = updated_by

    await db.commit()
    await db.refresh(server)

    clear_mcp_server_tools_cache(name)

    logger.info(f"Updated MCP server '{name}'")
    return server


async def delete_mcp_server(db: AsyncSession, name: str) -> bool:
    """Delete server."""
    server = await get_mcp_server(db, name)
    if not server:
        return False

    await db.delete(server)
    await db.commit()

    clear_mcp_server_tools_cache(name)

    logger.info(f"Deleted MCP server '{name}'")
    return True


# =============================================================================
# === Tool Management ===
# =============================================================================


async def set_server_enabled(
    db: AsyncSession, name: str, enabled: bool, updated_by: str = None
) -> tuple[bool, MCPServer]:
    """Set server enabled status."""
    server = await get_mcp_server(db, name)
    if not server:
        raise ValueError(f"Server '{name}' does not exist")

    server.enabled = 1 if enabled else 0
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    is_enabled = bool(server.enabled)
    clear_mcp_server_tools_cache(name)

    logger.info(f"Set MCP server '{name}' enabled={is_enabled}")
    return is_enabled, server


async def toggle_tool_enabled(
    db: AsyncSession,
    server_name: str,
    tool_name: str,
    updated_by: str = None,
) -> tuple[bool, MCPServer]:
    """Toggle single tool enabled status.

    Args:
        db: Database session
        server_name: Server name
        tool_name: Tool name
        updated_by: Updater

    Returns:
        (enabled, server): Tool enabled status and updated server object
    """
    server = await get_mcp_server(db, server_name)
    if not server:
        raise ValueError(f"Server '{server_name}' does not exist")

    disabled_tools = list(server.disabled_tools or [])

    if tool_name in disabled_tools:
        disabled_tools.remove(tool_name)
        enabled = True
    else:
        disabled_tools.append(tool_name)
        enabled = False

    server.disabled_tools = disabled_tools
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    # Clear tool cache (re-filtered on next fetch)
    clear_mcp_server_tools_cache(server_name)

    logger.info(f"Toggled tool '{tool_name}' for server '{server_name}' enabled={enabled}")
    return enabled, server


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
    return await get_mcp_tools(server_name, additional_servers={server_name: config}, disabled_tools=disabled_tools)


async def get_servers_config(names: list[str]) -> dict[str, dict[str, Any]]:
    """Batch get server configurations.

    Args:
        names: List of server names

    Returns:
        {name: config} dictionary, containing only found servers
    """
    return await _load_enabled_mcp_server_configs(names=names)


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
