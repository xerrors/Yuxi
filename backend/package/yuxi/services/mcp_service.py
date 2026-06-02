"""MCP Service - Unified business logic and state management for MCP.

Responsibilities:
- Server configuration CRUD operations
- Built-in configuration synchronization (Code <-> Database)
- Unified entry point for Agent tool retrieval (auto-filtering disabled_tools)
- MCP Client and Tools management (formerly in agents/common/mcp.py)
"""

import asyncio
import hashlib
import httpx
import json
import os
import re
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.crypto import encrypt_credential_blob
from yuxi.services.mcp_auth.orchestrator import AuthContext, resolve_runtime_mcp_config
from yuxi.services.mcp_auth.proxy_service import (
    INTERNAL_PROXY_TOKEN_HEADER,
    build_proxy_runtime_config,
    should_use_internal_proxy,
)
from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache
from yuxi.storage.postgres.models_business import AgentConfig, MCPConnection, MCPServer, Skill
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
_VALID_MCP_CONNECTION_SCOPE_TYPES = {"system", "department", "user"}
_VALID_MCP_CONNECTION_STATUSES = {"active", "disabled", "reauth_required", "invalid"}

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

_MCP_PROXY_BASE_URL_ENV = "YUXI_INTERNAL_MCP_PROXY_BASE_URL"

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


def _get_internal_mcp_proxy_base_url() -> str | None:
    value = os.getenv(_MCP_PROXY_BASE_URL_ENV, "").strip()
    return value or None


def _extract_cache_identity(server_config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
    cache_partition = str(server_config.get("__yuxi_cache_partition") or "server")
    allow_global_cache = bool(server_config.get("__yuxi_allow_global_cache", True))
    cache_identity = {
        key: value
        for key, value in server_config.items()
        if key not in {"__yuxi_cache_partition", "__yuxi_allow_global_cache", "disabled_tools"}
    }
    headers = dict(cache_identity.get("headers") or {})
    headers.pop(INTERNAL_PROXY_TOKEN_HEADER, None)
    if headers:
        cache_identity["headers"] = headers
    elif "headers" in cache_identity:
        cache_identity["headers"] = {}
    return cache_identity, cache_partition, allow_global_cache


def _resolve_scope_id(binding_scope: str, auth_context: AuthContext | None) -> str | None:
    if binding_scope == "inline":
        return None
    if binding_scope == "system":
        return "global"
    if auth_context is None:
        raise ValueError(f"auth_context is required for MCP binding scope '{binding_scope}'")
    if binding_scope == "department":
        if not auth_context.department_id:
            raise ValueError("department_id is required for department-scoped MCP auth")
        return str(auth_context.department_id)
    if binding_scope == "user":
        if not auth_context.user_id:
            raise ValueError("user_id is required for user-scoped MCP auth")
        return str(auth_context.user_id)
    raise ValueError(f"Unsupported MCP binding scope: {binding_scope}")


def _normalize_mcp_connection_scope(scope_type: str, scope_id: str | None) -> tuple[str, str]:
    normalized_scope_type = str(scope_type or "").strip().lower()
    if normalized_scope_type not in _VALID_MCP_CONNECTION_SCOPE_TYPES:
        raise ValueError("scope_type must be one of: system, department, user")

    normalized_scope_id = str(scope_id or "").strip()
    if normalized_scope_type == "system":
        return normalized_scope_type, "global"
    if not normalized_scope_id:
        raise ValueError(f"scope_id is required for {normalized_scope_type}-scoped MCP connections")
    return normalized_scope_type, normalized_scope_id


def _normalize_mcp_connection_status(status: str) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _VALID_MCP_CONNECTION_STATUSES:
        raise ValueError("status must be one of: active, disabled, reauth_required, invalid")
    return normalized_status


async def _get_enabled_mcp_server_record(server_name: str, *, db: AsyncSession) -> MCPServer | None:
    result = await db.execute(
        select(MCPServer).where(
            MCPServer.enabled == 1,
            MCPServer.name == server_name,
        )
    )
    return result.scalar_one_or_none()


async def get_runtime_mcp_server_config(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    if db is None and auth_context is None:
        return await get_enabled_mcp_server_config(server_name)

    if db is not None:
        server = await _get_enabled_mcp_server_record(server_name, db=db)
        if server is None:
            return None
        if not server.auth_config_json:
            return server.to_mcp_config()

        auth_config = MCPAuthConfig.model_validate(server.auth_config_json)
        scope_id = _resolve_scope_id(auth_config.binding_scope, auth_context)
        if scope_id is None:
            return server.to_mcp_config()

        result = await db.execute(
            select(MCPConnection).where(
                MCPConnection.server_name == server_name,
                MCPConnection.scope_type == auth_config.binding_scope,
                MCPConnection.scope_id == scope_id,
                MCPConnection.status == "active",
            )
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise ValueError(
                f"Active MCP connection not found for server '{server_name}' and scope "
                f"{auth_config.binding_scope}:{scope_id}"
            )
        proxy_base_url = _get_internal_mcp_proxy_base_url()
        if should_use_internal_proxy(server, auth_config, proxy_base_url):
            return build_proxy_runtime_config(
                server,
                auth_config=auth_config,
                auth_context=auth_context or AuthContext(),
                proxy_base_url=proxy_base_url or "",
            )
        return await resolve_runtime_mcp_config(
            server,
            auth_context=auth_context or AuthContext(),
            connection=connection,
            http_client=http_client,
        )

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await get_runtime_mcp_server_config(
            server_name,
            auth_context=auth_context,
            db=session,
            http_client=http_client,
        )


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
    cache_identity, cache_partition, allow_global_cache = _extract_cache_identity(server_config)
    config_payload = json.dumps(cache_identity, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
    if allow_global_cache:
        cache_key = f"{server_name}:{config_hash}"
    else:
        cache_key = f"{server_name}:{cache_partition}:{config_hash}"

    all_processed_tools: list[Callable[..., Any]] = []

    async with _mcp_lock:
        if not force_refresh and cache and cache_key in _mcp_tools_cache:
            all_processed_tools = _mcp_tools_cache[cache_key]

    if not all_processed_tools:
        try:
            # disabled_tools 只影响返回值过滤，不参与 MCP client 建连参数。
            client_config = {
                k: v
                for k, v in server_config.items()
                if k not in ("disabled_tools", "__yuxi_cache_partition", "__yuxi_allow_global_cache")
            }

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


async def _clear_mcp_connection_runtime_auth_cache(connection_id: int | None) -> None:
    if connection_id is None:
        return

    cache = RedisTokenCache()
    try:
        await cache.delete_access_token(connection_id)
    except Exception as exc:
        logger.warning(f"Failed to clear MCP token cache for connection {connection_id}: {exc}")
    try:
        await cache.release_refresh_lock(connection_id)
    except Exception as exc:
        logger.warning(f"Failed to clear MCP refresh lock for connection {connection_id}: {exc}")


async def _clear_mcp_server_runtime_auth_cache(db: AsyncSession, server_name: str) -> None:
    connections = await list_mcp_connections(db, server_name=server_name)
    for connection in connections:
        await _clear_mcp_connection_runtime_auth_cache(getattr(connection, "id", None))


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


async def get_mcp_connection(db: AsyncSession, connection_id: int) -> MCPConnection | None:
    result = await db.execute(select(MCPConnection).where(MCPConnection.id == connection_id))
    return result.scalar_one_or_none()


def _auth_context_from_connection(connection: MCPConnection) -> AuthContext:
    if connection.scope_type == "department":
        return AuthContext(department_id=connection.scope_id)
    if connection.scope_type == "user":
        return AuthContext(user_id=connection.scope_id)
    return AuthContext()


async def list_mcp_connections(
    db: AsyncSession,
    *,
    server_name: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> list[MCPConnection]:
    stmt = select(MCPConnection)
    if server_name is not None:
        stmt = stmt.where(MCPConnection.server_name == server_name)
    if scope_type is not None:
        stmt = stmt.where(MCPConnection.scope_type == scope_type)
    if scope_id is not None:
        stmt = stmt.where(MCPConnection.scope_id == scope_id)
    stmt = stmt.order_by(MCPConnection.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_mcp_connection(
    db: AsyncSession,
    *,
    server_name: str,
    scope_type: str,
    scope_id: str,
    display_name: str | None = None,
    external_subject: str | None = None,
    status: str = "active",
    credential_blob: str | None = None,
    meta_json: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> MCPConnection:
    server = await get_mcp_server(db, server_name)
    if server is None:
        raise ValueError(f"Server '{server_name}' does not exist")
    normalized_scope_type, normalized_scope_id = _normalize_mcp_connection_scope(scope_type, scope_id)
    normalized_status = _normalize_mcp_connection_status(status)

    encrypted_credential_blob = (
        encrypt_credential_blob(credential_blob)
        if isinstance(credential_blob, str) and credential_blob.strip()
        else credential_blob
    )

    connection = MCPConnection(
        server_name=server_name,
        scope_type=normalized_scope_type,
        scope_id=normalized_scope_id,
        display_name=display_name,
        external_subject=external_subject,
        status=normalized_status,
        credential_blob=encrypted_credential_blob,
        meta_json=meta_json or {},
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def update_mcp_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    display_name: str | None = None,
    external_subject: str | None = None,
    credential_blob: Any = _UNSET,
    meta_json: dict[str, Any] | None = None,
    status: str | None = None,
    updated_by: str | None = None,
) -> MCPConnection:
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    should_clear_runtime_auth_cache = False
    if display_name is not None:
        connection.display_name = display_name
    if external_subject is not None:
        connection.external_subject = external_subject
    if credential_blob is not _UNSET:
        if isinstance(credential_blob, str) and credential_blob.strip():
            connection.credential_blob = encrypt_credential_blob(credential_blob)
        else:
            connection.credential_blob = credential_blob
        should_clear_runtime_auth_cache = True
    if meta_json is not None:
        connection.meta_json = meta_json
    if status is not None:
        connection.status = _normalize_mcp_connection_status(status)
        should_clear_runtime_auth_cache = True
    if updated_by is not None:
        connection.updated_by = updated_by

    await db.commit()
    await db.refresh(connection)
    if should_clear_runtime_auth_cache:
        await _clear_mcp_connection_runtime_auth_cache(connection.id)
    return connection


async def delete_mcp_connection(db: AsyncSession, connection_id: int) -> bool:
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        return False
    deleted_connection_id = connection.id
    await db.delete(connection)
    await db.commit()
    await _clear_mcp_connection_runtime_auth_cache(deleted_connection_id)
    return True


async def set_mcp_connection_status(
    db: AsyncSession,
    connection_id: int,
    *,
    status: str,
    updated_by: str | None = None,
) -> MCPConnection:
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    connection.status = _normalize_mcp_connection_status(status)
    if updated_by is not None:
        connection.updated_by = updated_by
    await db.commit()
    await db.refresh(connection)
    await _clear_mcp_connection_runtime_auth_cache(connection.id)
    return connection


async def reauthorize_mcp_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    updated_by: str | None = None,
) -> MCPConnection:
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    cache = RedisTokenCache()
    if getattr(connection, "id", None) is not None:
        try:
            await cache.delete_access_token(connection.id)
        except Exception as exc:
            logger.warning(f"Failed to clear MCP token cache for connection {connection.id}: {exc}")
        try:
            await cache.release_refresh_lock(connection.id)
        except Exception as exc:
            logger.warning(f"Failed to clear MCP refresh lock for connection {connection.id}: {exc}")

    connection.status = "active"
    meta_json = dict(connection.meta_json or {})
    meta_json.pop("last_error", None)
    connection.meta_json = meta_json
    if updated_by is not None:
        connection.updated_by = updated_by
    await db.commit()
    await db.refresh(connection)
    return connection


async def test_mcp_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    server = await get_mcp_server(db, connection.server_name)
    if server is None:
        raise ValueError(f"Server '{connection.server_name}' does not exist")

    auth_context = _auth_context_from_connection(connection)
    config = await get_runtime_mcp_server_config(server.name, auth_context=auth_context, db=db)
    if config is None:
        raise ValueError(f"MCP server '{server.name}' runtime config unavailable")

    tools = await get_mcp_tools(
        server.name,
        additional_servers={server.name: config},
        disabled_tools=[],
        cache=False,
        force_refresh=True,
    )

    meta_json = dict(connection.meta_json or {})
    meta_json["last_success_at"] = datetime.now(tz=UTC).isoformat()
    meta_json.pop("last_error", None)
    connection.meta_json = meta_json
    connection.status = "active"
    if updated_by is not None:
        connection.updated_by = updated_by
    await db.commit()
    await db.refresh(connection)
    return {"tool_count": len(tools), "connection": connection}


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
    auth_config: dict | None = None,
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
        auth_config_json=auth_config,
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

    await _clear_mcp_server_runtime_auth_cache(db, name)
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
    auth_config: Any = _UNSET,
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
    if auth_config is not _UNSET:
        server.auth_config_json = auth_config
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

    connection_ids = [item.id for item in await list_mcp_connections(db, server_name=name)]
    await db.delete(server)
    await db.commit()

    for connection_id in connection_ids:
        await _clear_mcp_connection_runtime_auth_cache(connection_id)
    clear_mcp_server_tools_cache(name)

    logger.info(f"Deleted MCP server '{name}'")
    return True


async def get_mcp_server_dependency_summary(db: AsyncSession, name: str) -> dict[str, Any]:
    connections = await list_mcp_connections(db, server_name=name)

    skill_rows = (await db.execute(select(Skill))).scalars().all()
    matched_skills = [
        {"slug": item.slug, "name": item.name}
        for item in skill_rows
        if name in (item.mcp_dependencies or [])
    ]

    agent_config_rows = (await db.execute(select(AgentConfig))).scalars().all()
    matched_agent_configs = []
    for item in agent_config_rows:
        config_json = item.config_json or {}
        if name in (config_json.get("mcps") or []):
            matched_agent_configs.append({"id": item.id, "name": item.name, "agent_id": item.agent_id})

    connection_refs = [
        {"scope_type": item.scope_type, "scope_id": item.scope_id, "status": item.status}
        for item in connections
    ]

    return {
        "has_references": bool(connection_refs or matched_skills or matched_agent_configs),
        "connections": connection_refs,
        "skills": matched_skills,
        "agent_configs": matched_agent_configs,
    }


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
    if not is_enabled:
        await _clear_mcp_server_runtime_auth_cache(db, name)
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


async def get_enabled_mcp_tools(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list:
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
    config = await get_runtime_mcp_server_config(
        server_name,
        auth_context=auth_context,
        db=db,
        http_client=http_client,
    )
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
