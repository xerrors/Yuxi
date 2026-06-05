from __future__ import annotations

import logging
import os
import traceback
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.orchestrator import AuthContext, resolve_runtime_mcp_config
from yuxi.services.mcp_auth.proxy_service import (
    build_proxy_runtime_config,
    should_use_internal_proxy,
)
from yuxi.storage.postgres.models_business import AgentConfig, MCPConnection, MCPServer, Skill

logger = logging.getLogger("yuxi.mcp.server_service")

_UNSET = object()
_MCP_PROXY_BASE_URL_ENV = "YUXI_INTERNAL_MCP_PROXY_BASE_URL"

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


async def ensure_builtin_mcp_servers_in_db() -> None:
    """同步代码预置的内置 MCP 服务器至数据库中"""
    from yuxi.storage.postgres.manager import pg_manager

    try:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(func.count(MCPServer.name)))
            count = result.scalar()

            if count == 0:
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
                if session.new:
                    await session.commit()
                elif session.dirty:
                    await session.commit()

    except Exception as e:
        logger.error(f"Failed to ensure builtin MCP servers in database: {e}, traceback: {traceback.format_exc()}")


async def _load_enabled_mcp_server_configs(
    *,
    names: list[str] | None = None,
    db: AsyncSession | None = None,
) -> dict[str, dict[str, Any]]:
    """从数据库中加载已启用的服务器 MCP 配置"""
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
    """获取最新启用的指定服务器的 MCP 配置"""
    configs = await _load_enabled_mcp_server_configs(names=[server_name], db=db)
    return configs.get(server_name)


def _get_internal_mcp_proxy_base_url() -> str | None:
    value = os.getenv(_MCP_PROXY_BASE_URL_ENV, "").strip()
    return value or None


async def _get_enabled_mcp_server_record(server_name: str, *, db: AsyncSession) -> MCPServer | None:
    result = await db.execute(
        select(MCPServer).where(
            MCPServer.enabled == 1,
            MCPServer.name == server_name,
        )
    )
    return result.scalar_one_or_none()


def _apply_runtime_tool_cache_policy(
    config: dict[str, Any],
    *,
    auth_config: MCPAuthConfig,
    auth_context: AuthContext | None,
    connection: MCPConnection | None,
) -> dict[str, Any]:
    """利用 CachePolicy 模式获取缓存 key 的隔离区划并应用"""
    from yuxi.services.mcp.cache_policy import CachePolicyFactory

    policy = CachePolicyFactory.get_policy(auth_config.provider)
    partition, is_shared = policy.resolve_cache_partition(
        auth_context or AuthContext(),
        connection,
    )
    config["__yuxi_cache_partition"] = partition
    config["__yuxi_allow_global_cache"] = is_shared
    return config


async def get_runtime_mcp_server_config(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """解析获取附带运行时鉴权与租户范围的 MCP 服务配置"""
    if db is None and auth_context is None:
        return await get_enabled_mcp_server_config(server_name)

    if db is not None:
        server = await _get_enabled_mcp_server_record(server_name, db=db)
        if server is None:
            return None
        if not server.auth_config_json:
            return server.to_mcp_config()

        auth_config = MCPAuthConfig.model_validate(server.auth_config_json)
        from yuxi.services.mcp.connection_service import _resolve_scope_id

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
            if auth_config.get_secret_fields():
                raise ValueError(
                    f"Active MCP connection not found for server '{server_name}' and scope "
                    f"{auth_config.binding_scope}:{scope_id}"
                )
            # 无需长期密钥的鉴权机制无需强制绑定连接即可生成运行时配置
        proxy_base_url = _get_internal_mcp_proxy_base_url()
        if should_use_internal_proxy(server, auth_config, proxy_base_url):
            config = build_proxy_runtime_config(
                server,
                auth_context=auth_context or AuthContext(),
                proxy_base_url=proxy_base_url or "",
            )
        else:
            config = await resolve_runtime_mcp_config(
                server,
                auth_context=auth_context or AuthContext(),
                connection=connection,
                http_client=http_client,
            )
        return _apply_runtime_tool_cache_policy(
            config,
            auth_config=auth_config,
            auth_context=auth_context,
            connection=connection,
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
    """获取所有已启用的服务器名称"""
    configs = await _load_enabled_mcp_server_configs(db=db)
    return list(configs.keys())


async def get_mcp_server(db: AsyncSession, name: str) -> MCPServer | None:
    """获取单个服务器对象记录"""
    result = await db.execute(select(MCPServer).filter(MCPServer.name == name))
    return result.scalar_one_or_none()


async def get_all_mcp_servers(db: AsyncSession) -> list[MCPServer]:
    """获取所有配置的服务器对象列表"""
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
    auth_config: dict | None = None,
    created_by: str = None,
) -> MCPServer:
    """创建 MCP 服务器配置"""
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

    from yuxi.services.mcp.tool_registry_service import (
        _clear_mcp_server_runtime_auth_cache,
        invalidate_mcp_server_tools_cache,
    )

    await _clear_mcp_server_runtime_auth_cache(db, name)
    await invalidate_mcp_server_tools_cache(name)

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
    """更新服务器配置"""
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

    from yuxi.services.mcp.tool_registry_service import (
        _clear_mcp_server_runtime_auth_cache,
        invalidate_mcp_server_tools_cache,
    )

    if auth_config is not _UNSET:
        await _clear_mcp_server_runtime_auth_cache(db, name)
    await invalidate_mcp_server_tools_cache(name)

    logger.info(f"Updated MCP server '{name}'")
    return server


async def delete_mcp_server(db: AsyncSession, name: str) -> bool:
    """删除服务器"""
    server = await get_mcp_server(db, name)
    if not server:
        return False

    from yuxi.services.mcp.tool_registry_service import (
        _clear_mcp_server_runtime_auth_cache,
        invalidate_mcp_server_tools_cache,
    )

    # NOTE: 必须在级联删除前执行 Redis 缓存清理，否则关联的 connection 行被删除后将无法提取 ID
    await _clear_mcp_server_runtime_auth_cache(db, name)

    await db.delete(server)
    await db.commit()

    await invalidate_mcp_server_tools_cache(name)

    logger.info(f"Deleted MCP server '{name}'")
    return True


async def get_mcp_server_dependency_summary(db: AsyncSession, name: str) -> dict[str, Any]:
    """获取依赖于该 MCP 服务器的智能体、技能和连接概要"""
    from yuxi.services.mcp.connection_service import list_mcp_connections

    connections = await list_mcp_connections(db, server_name=name)

    skill_rows = (await db.execute(select(Skill))).scalars().all()
    matched_skills = [
        {"slug": item.slug, "name": item.name} for item in skill_rows if name in (item.mcp_dependencies or [])
    ]

    agent_config_rows = (await db.execute(select(AgentConfig))).scalars().all()
    matched_agent_configs = []
    for item in agent_config_rows:
        config_json = item.config_json or {}
        if name in (config_json.get("mcps") or []):
            matched_agent_configs.append({"id": item.id, "name": item.name, "agent_id": item.agent_id})

    connection_refs = [
        {"scope_type": item.scope_type, "scope_id": item.scope_id, "status": item.status} for item in connections
    ]

    return {
        "has_references": bool(connection_refs or matched_skills or matched_agent_configs),
        "connections": connection_refs,
        "skills": matched_skills,
        "agent_configs": matched_agent_configs,
    }


async def set_server_enabled(
    db: AsyncSession, name: str, enabled: bool, updated_by: str = None
) -> tuple[bool, MCPServer]:
    """设置服务器的启用状态"""
    server = await get_mcp_server(db, name)
    if not server:
        raise ValueError(f"Server '{name}' does not exist")

    server.enabled = 1 if enabled else 0
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    is_enabled = bool(server.enabled)
    from yuxi.services.mcp.tool_registry_service import (
        _clear_mcp_server_runtime_auth_cache,
        invalidate_mcp_server_tools_cache,
    )

    if not is_enabled:
        await _clear_mcp_server_runtime_auth_cache(db, name)
    await invalidate_mcp_server_tools_cache(name)

    logger.info(f"Set MCP server '{name}' enabled={is_enabled}")
    return is_enabled, server


async def get_servers_config(names: list[str]) -> dict[str, dict[str, Any]]:
    """批量获取服务器配置"""
    return await _load_enabled_mcp_server_configs(names=names)
