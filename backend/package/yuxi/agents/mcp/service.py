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
from collections.abc import Callable
from typing import Any, cast

from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import MCPServer
from yuxi.utils import logger

# =============================================================================
# === Global Cache & State ===
# =============================================================================

# Global Lock for MCP state
_mcp_lock = asyncio.Lock()

# 本地仅缓存工具对象。配置始终以数据库为准，每次按 server_slug 现查。
# cache key 使用 server_slug:config_hash，当配置变化时会自然失效。
_mcp_tools_cache: dict[str, list[Callable[..., Any]]] = {}

# MCP tools statistics (for reporting enabled/disabled counts)
_mcp_tools_stats: dict[str, dict[str, int]] = {}
_USER_CONFIGURABLE_TRANSPORTS = ("sse", "streamable_http")

# Default MCP Server configurations (Imported to DB on first run)
_DEFAULT_MCP_SERVERS = {
    "mcp-server-chart": {
        "command": "npx",
        "args": ["-y", "@antv/mcp-server-chart"],
        "transport": "stdio",
        "description": "图表生成工具，支持生成各类图表（柱状图、折线图、饼图等）",
        "icon": "📊",
        "tags": ["内置", "图表"],
    },
}
_BUILTIN_MCP_SERVER_SLUGS = tuple(_DEFAULT_MCP_SERVERS)

_RETIRED_BUILTIN_MCP_SERVER_SLUGS = ("sequentialthinking",)

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


class MCPServerNotFoundError(ValueError):
    """表示指定的 MCP 服务器不存在。"""


def is_builtin_mcp_server(server: MCPServer) -> bool:
    """判断 MCP 是否由代码中的内置定义管理。"""
    return server.slug in _BUILTIN_MCP_SERVER_SLUGS


def requires_mcp_stdio_migration(server: MCPServer) -> bool:
    """判断 MCP 是否为升级后需要迁移的用户 stdio 配置。"""
    return server.transport == "stdio" and not is_builtin_mcp_server(server)


def _to_runtime_mcp_config(server: MCPServer) -> dict[str, Any]:
    """生成运行时 MCP 配置，内置连接字段始终以代码定义为准。"""
    if not is_builtin_mcp_server(server):
        return server.to_mcp_config()

    builtin = _DEFAULT_MCP_SERVERS[server.slug]
    config = {
        key: builtin[key]
        for key in ("transport", "url", "command", "args", "env", "headers", "timeout", "sse_read_timeout")
        if builtin.get(key) is not None
    }
    if server.disabled_tools:
        config["disabled_tools"] = server.disabled_tools
    return config


# =============================================================================
# === Core Logic (Moved from agents/common/mcp.py) ===
# =============================================================================


async def ensure_builtin_mcp_servers_in_db() -> None:
    """Ensure built-in MCP server definitions exist in the database."""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        any_changed = False

        result = await session.execute(
            select(MCPServer).where(
                MCPServer.transport == "stdio",
                ~MCPServer.slug.in_(_BUILTIN_MCP_SERVER_SLUGS),
                MCPServer.enabled == 1,
            )
        )
        for server in result.scalars().all():
            server.enabled = 0
            server.updated_by = "system"
            clear_mcp_server_tools_cache(server.slug)
            any_changed = True
            logger.warning(f"Disabled legacy user stdio MCP server '{server.slug}'")

        for slug in _RETIRED_BUILTIN_MCP_SERVER_SLUGS:
            result = await session.execute(
                select(MCPServer).filter(MCPServer.slug == slug, MCPServer.created_by == "system")
            )
            retired = result.scalar_one_or_none()
            if retired:
                await session.delete(retired)
                clear_mcp_server_tools_cache(slug)
                any_changed = True
                logger.info(f"Removed retired built-in MCP server '{slug}' from database")

        for slug, config in _DEFAULT_MCP_SERVERS.items():
            result = await session.execute(select(MCPServer).filter(MCPServer.slug == slug))
            existing = result.scalar_one_or_none()
            if not existing:
                session.add(
                    MCPServer(
                        slug=slug,
                        name=config.get("name", slug),
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
                )
                any_changed = True
                logger.info(f"Added built-in MCP server '{slug}' to database")
                continue

            server_changed = False
            for field in _SYNCED_MCP_FIELDS:
                next_value = config.get(field)
                if getattr(existing, field) != next_value:
                    setattr(existing, field, next_value)
                    server_changed = True
            if existing.created_by != "system":
                existing.created_by = "system"
                server_changed = True
            if server_changed:
                existing.updated_by = "system"
                any_changed = True

        if any_changed:
            await session.commit()


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
        stmt = select(MCPServer).where(
            MCPServer.enabled == 1,
            or_(MCPServer.transport != "stdio", MCPServer.slug.in_(_BUILTIN_MCP_SERVER_SLUGS)),
        )
        if names:
            stmt = stmt.where(MCPServer.slug.in_(names))
        result = await db.execute(stmt)
        servers = result.scalars().all()
        return {server.slug: _to_runtime_mcp_config(server) for server in servers}

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await _load_enabled_mcp_server_configs(names=names, db=session)


async def get_enabled_mcp_server_config(server_slug: str, *, db: AsyncSession | None = None) -> dict[str, Any] | None:
    """Get the latest enabled MCP server config from the database."""
    configs = await _load_enabled_mcp_server_configs(names=[server_slug], db=db)
    return configs.get(server_slug)


async def get_enabled_mcp_server_slugs(*, db: AsyncSession | None = None) -> list[str]:
    """Get enabled MCP server slugs from the database."""
    if db is not None:
        result = await db.execute(
            select(MCPServer.slug).where(
                MCPServer.enabled == 1,
                or_(MCPServer.transport != "stdio", MCPServer.slug.in_(_BUILTIN_MCP_SERVER_SLUGS)),
            )
        )
        return [name for name in result.scalars().all() if isinstance(name, str)]

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await get_enabled_mcp_server_slugs(db=session)


async def get_mcp_tools(
    server_slug: str,
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
        server_slug: Server slug
        additional_servers: Additional server configurations
        disabled_tools: List of tool names to filter out from the RETURN value (does not affect cache)
        cache: Whether to use/update the cache (default: True)
        force_refresh: Whether to force a refresh from the server (default: False)
    """
    if additional_servers and server_slug in additional_servers:
        server_config = additional_servers[server_slug]
    else:
        server_config = await get_enabled_mcp_server_config(server_slug)

    if server_config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    # 配置 hash 直接基于完整配置生成。只要数据库中的配置发生变化，
    # 本地工具缓存 key 就会变化，从而自然触发重建。
    config_payload = json.dumps(server_config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
    cache_key = f"{server_slug}:{config_hash}"

    all_processed_tools: list[Callable[..., Any]] = []

    async with _mcp_lock:
        if not force_refresh and cache and cache_key in _mcp_tools_cache:
            all_processed_tools = _mcp_tools_cache[cache_key]

    if not all_processed_tools:
        try:
            # disabled_tools 只影响返回值过滤，不参与 MCP client 建连参数。
            client_config = {k: v for k, v in server_config.items() if k not in ("disabled_tools",)}

            client = await get_mcp_client({server_slug: client_config})
            if client is None:
                return []

            raw_tools = cast(list[Any], await client.get_tools())

            server_cc = to_camel_case(server_slug)
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
                        key for key in _mcp_tools_cache if key.startswith(f"{server_slug}:") and key != cache_key
                    ]
                    for stale_key in stale_keys:
                        _mcp_tools_cache.pop(stale_key, None)
                    _mcp_tools_cache[cache_key] = all_processed_tools

                global_config_disabled = server_config.get("disabled_tools") or []
                enabled_count = len([t for t in all_processed_tools if t.name not in global_config_disabled])
                _mcp_tools_stats[server_slug] = {
                    "total": len(all_processed_tools),
                    "enabled": enabled_count,
                    "disabled": len(all_processed_tools) - enabled_count,
                }

                logger.info(
                    f"Refreshed MCP tools cache for '{server_slug}' with key '{cache_key}': "
                    f"{len(all_processed_tools)} tools loaded."
                )

        except ExceptionGroup as e:
            logger.warning(f"MCP server '{server_slug}' failed with group error: {e}")
            return []
        except Exception as e:
            logger.exception(f"Failed to load tools from MCP server '{server_slug}': {e}")
            return []

    # 3. Filtering (Apply to Return Value Only)
    if disabled_tools:
        filtered_tools = [t for t in all_processed_tools if t.name not in disabled_tools]
        logger.debug(
            f"Returning {len(filtered_tools)}/{len(all_processed_tools)} tools for '{server_slug}' "
            f"(filtered {len(disabled_tools)} by argument)"
        )
        return filtered_tools

    return all_processed_tools


async def get_tools_from_all_servers() -> list[Callable[..., Any]]:
    """Get all tools from all configured MCP servers."""
    server_configs = await _load_enabled_mcp_server_configs()
    all_tools = []
    for server_slug in server_configs:
        tools = await get_mcp_tools(server_slug, additional_servers=server_configs)
        all_tools.extend(tools)
    return all_tools


def clear_mcp_cache() -> None:
    """Clear the MCP tools cache (useful for testing)."""
    global _mcp_tools_cache, _mcp_tools_stats
    _mcp_tools_cache = {}
    _mcp_tools_stats = {}


def clear_mcp_server_tools_cache(server_slug: str) -> None:
    """Clear the tools cache for a specific MCP server."""
    global _mcp_tools_cache, _mcp_tools_stats
    server_prefix = f"{server_slug}:"
    stale_keys = [key for key in _mcp_tools_cache if key.startswith(server_prefix)]
    for stale_key in stale_keys:
        _mcp_tools_cache.pop(stale_key, None)
    _mcp_tools_stats.pop(server_slug, None)
    logger.info(f"Cleared tools cache for MCP server '{server_slug}'")


def get_mcp_tools_stats(server_slug: str) -> dict[str, int] | None:
    """Get tools statistics for a MCP server.

    Returns:
        dict with 'total', 'enabled', 'disabled' counts, or None if not available
    """
    return _mcp_tools_stats.get(server_slug)


# =============================================================================
# === Server Config CRUD ===
# =============================================================================


async def get_mcp_server(db: AsyncSession, slug: str) -> MCPServer | None:
    """Get single server configuration by slug."""
    result = await db.execute(select(MCPServer).filter(MCPServer.slug == slug))
    return result.scalar_one_or_none()


async def get_all_mcp_servers(db: AsyncSession) -> list[MCPServer]:
    """Get all server configurations."""
    result = await db.execute(select(MCPServer))
    return list(result.scalars().all())


async def create_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str,
    transport: str,
    url: str = None,
    description: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    created_by: str = None,
) -> MCPServer:
    """Create server."""
    if slug in _BUILTIN_MCP_SERVER_SLUGS:
        raise ValueError("系统内置 MCP 的 slug 由代码保留，无法通过接口创建")
    if transport not in _USER_CONFIGURABLE_TRANSPORTS:
        raise ValueError("用户创建的 MCP 仅支持 sse 或 streamable_http，不允许启动 stdio 本地进程")

    existing = await get_mcp_server(db, slug)
    if existing:
        raise ValueError(f"Server slug '{slug}' already exists")

    server = MCPServer(
        slug=slug,
        name=name,
        description=description,
        transport=transport,
        url=url,
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

    clear_mcp_server_tools_cache(slug)

    logger.info(f"Created MCP server '{slug}'")
    return server


async def update_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str = None,
    description: str = None,
    transport: str = None,
    url: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    updated_by: str = None,
) -> MCPServer:
    """Update server configuration."""
    server = await get_mcp_server(db, slug)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    if is_builtin_mcp_server(server):
        raise PermissionError("系统内置 MCP 的连接配置由代码管理，无法通过接口修改")

    next_transport = transport or server.transport
    if next_transport not in _USER_CONFIGURABLE_TRANSPORTS:
        raise ValueError("用户创建的 MCP 仅支持 sse 或 streamable_http，不允许启动 stdio 本地进程")

    next_url = url if url is not None else server.url
    if not next_url or not next_url.strip():
        raise ValueError(f"传输类型为 {next_transport} 时，url 必填")

    if name is not None:
        server.name = name
    if description is not None:
        server.description = description
    if transport is not None:
        server.transport = transport
    if url is not None:
        server.url = url
    server.command = None
    server.args = None
    server.env = None
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

    clear_mcp_server_tools_cache(slug)

    logger.info(f"Updated MCP server '{slug}'")
    return server


async def delete_mcp_server(db: AsyncSession, slug: str) -> bool:
    """Delete server."""
    server = await get_mcp_server(db, slug)
    if not server:
        return False

    await db.delete(server)
    await db.commit()

    clear_mcp_server_tools_cache(slug)

    logger.info(f"Deleted MCP server '{slug}'")
    return True


# =============================================================================
# === Tool Management ===
# =============================================================================


async def set_server_enabled(
    db: AsyncSession, slug: str, enabled: bool, updated_by: str = None
) -> tuple[bool, MCPServer]:
    """Set server enabled status."""
    server = await get_mcp_server(db, slug)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    if enabled and requires_mcp_stdio_migration(server):
        raise ValueError("历史 stdio MCP 已被禁用，请改为 sse 或 streamable_http 后再启用")

    server.enabled = 1 if enabled else 0
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    is_enabled = bool(server.enabled)
    clear_mcp_server_tools_cache(slug)

    logger.info(f"Set MCP server '{slug}' enabled={is_enabled}")
    return is_enabled, server


async def toggle_tool_enabled(
    db: AsyncSession,
    server_slug: str,
    tool_name: str,
    updated_by: str = None,
) -> tuple[bool, MCPServer]:
    """Toggle single tool enabled status.

    Args:
        db: Database session
        server_slug: Server slug
        tool_name: Tool name
        updated_by: Updater

    Returns:
        (enabled, server): Tool enabled status and updated server object
    """
    server = await get_mcp_server(db, server_slug)
    if not server:
        raise MCPServerNotFoundError(f"Server '{server_slug}' does not exist")

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
    clear_mcp_server_tools_cache(server_slug)

    logger.info(f"Toggled tool '{tool_name}' for server '{server_slug}' enabled={enabled}")
    return enabled, server


# =============================================================================
# === Unified Entry Points (Wrappers) ===
# =============================================================================


async def get_enabled_mcp_tools(server_slug: str) -> list:
    """Get MCP server tools (auto-filtering disabled_tools).

    Unified entry point for Agents, automatically:
    1. Gets the latest server config from database
    2. Gets all tools
    3. Filters out disabled_tools

    Args:
        server_slug: Server slug

    Returns:
        List of enabled tools
    """
    config = await get_enabled_mcp_server_config(server_slug)
    if config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    disabled_tools = config.get("disabled_tools") or []
    return await get_mcp_tools(server_slug, additional_servers={server_slug: config}, disabled_tools=disabled_tools)


async def get_servers_config(names: list[str]) -> dict[str, dict[str, Any]]:
    """Batch get server configurations.

    Args:
        names: List of server names

    Returns:
        {name: config} dictionary, containing only found servers
    """
    return await _load_enabled_mcp_server_configs(names=names)


async def get_all_mcp_tools(server_slug: str) -> list:
    """Get all tools of an MCP server (no filtering).

    For management UI to display tool list, supports viewing all tools and their enabled status.
    Does NOT update the global tools cache to avoid polluting agent's filtered view.

    Args:
        server_slug: Server slug

    Returns:
        List of all tools (unfiltered)
    """
    config = await get_enabled_mcp_server_config(server_slug)
    if config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    # Get all tools (no filtering, force refresh, no cache update)
    return await get_mcp_tools(
        server_slug,
        additional_servers={server_slug: config},
        disabled_tools=[],
        cache=False,
        force_refresh=True,
    )


async def inspect_mcp_server_tools(server: MCPServer) -> list:
    """管理端严格建连，包括停用服务；不吞异常、不修改状态或运行缓存。"""
    if requires_mcp_stdio_migration(server):
        raise ValueError("历史自定义 stdio MCP 须迁移为远程服务后再测试")
    config = {key: value for key, value in _to_runtime_mcp_config(server).items() if key != "disabled_tools"}
    client = MultiServerMCPClient({server.slug: config})
    tools = await client.get_tools()
    for tool in tools:
        if tool.metadata is None:
            tool.metadata = {}
        tool.metadata["id"] = f"mcp__{to_camel_case(server.slug)}__{to_camel_case(tool.name)}"
    return tools
