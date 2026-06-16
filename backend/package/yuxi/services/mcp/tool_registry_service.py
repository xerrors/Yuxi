from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import httpx
from cachetools import LRUCache
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.orchestrator import AuthContext, mcp_auth_context_var
from yuxi.services.mcp_auth.proxy_service import (
    INTERNAL_PROXY_DISABLE_TOOL_OBJECT_CACHE_KEY,
    INTERNAL_PROXY_TOKEN_HEADER,
)
from yuxi.services.mcp_tool_cache import RedisMcpToolCache
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer

logger = logging.getLogger("yuxi.mcp.tool_registry_service")

# 全局共享状态（直接在本模块维护，供外部和测试使用）
_mcp_tools_cache: LRUCache = LRUCache(maxsize=128)
_mcp_tools_stats: LRUCache = LRUCache(maxsize=128)
_mcp_tools_failure_cache: LRUCache = LRUCache(maxsize=256)
_mcp_tool_cache_store = RedisMcpToolCache()
_mcp_lock = asyncio.Lock()
_MCP_TOOL_FAILURE_COOLDOWN_SECONDS = float(os.getenv("YUXI_MCP_TOOL_FAILURE_COOLDOWN_SECONDS", "30"))
_MCP_INTERNAL_CONFIG_KEYS = {
    "__yuxi_cache_partition",
    "__yuxi_allow_global_cache",
    INTERNAL_PROXY_DISABLE_TOOL_OBJECT_CACHE_KEY,
    "disabled_tools",
}


def to_camel_case(s: str) -> str:
    """转换字符串为 lowerCamelCase 命名格式"""
    import re

    s = re.sub(r"[-_]+(.)", lambda m: m.group(1).upper(), s)
    return s[:1].lower() + s[1:]


def _extract_cache_identity(server_config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
    """提取用于缓存 key 比较的标识配置"""
    cache_partition = str(server_config.get("__yuxi_cache_partition") or "server")
    allow_global_cache = bool(server_config.get("__yuxi_allow_global_cache", True))

    cache_identity = {
        key: value
        for key, value in server_config.items()
        if key not in _MCP_INTERNAL_CONFIG_KEYS
    }

    headers = dict(cache_identity.get("headers") or {})
    headers.pop(INTERNAL_PROXY_TOKEN_HEADER, None)
    if headers:
        cache_identity["headers"] = headers
    elif "headers" in cache_identity:
        cache_identity["headers"] = {}
    return cache_identity, cache_partition, allow_global_cache


async def _build_mcp_tool_cache_descriptor(server_name: str, server_config: dict[str, Any]) -> dict[str, Any]:
    """生成缓存 Key 描述信息字典"""
    cache_identity, cache_partition, allow_global_cache = _extract_cache_identity(server_config)
    config_payload = json.dumps(cache_identity, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]

    server_revision = await _mcp_tool_cache_store.get_server_revision(server_name)
    partition_revision = 0
    if not allow_global_cache:
        partition_revision = await _mcp_tool_cache_store.get_partition_revision(server_name, cache_partition)
    revision_token = f"s{server_revision}:p{partition_revision}"
    partition_key = f"{cache_partition}:{revision_token}"
    cache_prefix = f"{server_name}:{partition_key}:"

    return {
        "cache_identity": cache_identity,
        "cache_partition": cache_partition,
        "allow_global_cache": allow_global_cache,
        "config_hash": config_hash,
        "cache_prefix": cache_prefix,
        "cache_key": f"{cache_prefix}{config_hash}",
        "partition_key": partition_key,
        "server_revision": server_revision,
        "partition_revision": partition_revision,
    }


def _serialize_mcp_tools_manifest(
    *,
    server_name: str,
    cache_partition: str,
    cache_key: str,
    tools: list[Callable[..., Any]],
) -> dict[str, Any]:
    """将 Langchain 运行态 Tool 转换为 Manifest 字典以缓存到 Redis 中"""
    entries = []
    for tool in tools:
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.schema() if hasattr(tool.args_schema, "schema") else {}
            parameters = schema.get("properties", {})
            required = schema.get("required", [])
        else:
            parameters = {}
            required = []
        metadata = dict(getattr(tool, "metadata", {}) or {})
        entries.append(
            {
                "name": tool.name,
                "id": metadata.get("id") or tool.name,
                "description": getattr(tool, "description", ""),
                "parameters": parameters,
                "required": required,
            }
        )
    return {
        "server_name": server_name,
        "cache_partition": cache_partition,
        "cache_key": cache_key,
        "tools": entries,
    }


def _deserialize_mcp_tool_manifest(manifest: dict[str, Any]) -> list[Callable[..., Any]]:
    """反序列化 Redis 中的 Manifest 字典还原为本地 Tool 对象结构"""
    tools: list[Callable[..., Any]] = []
    for entry in manifest.get("tools", []):
        args_schema = None
        parameters = entry.get("parameters") or {}
        required = entry.get("required") or []
        if parameters or required:
            args_schema = SimpleNamespace(
                schema=lambda parameters=parameters, required=required: {
                    "properties": parameters,
                    "required": required,
                }
            )
        tools.append(
            SimpleNamespace(
                name=entry.get("name") or "",
                description=entry.get("description") or "",
                metadata={"id": entry.get("id") or entry.get("name") or ""},
                args_schema=args_schema,
            )
        )
    return tools


def _get_mcp_auth_config(server_config: dict[str, Any]) -> MCPAuthConfig | None:
    auth_payload = server_config.get("auth_config") or {}
    if not auth_payload:
        return None
    try:
        return MCPAuthConfig.model_validate(auth_payload)
    except Exception as exc:
        logger.warning(f"Invalid MCP auth config while resolving tool preload strategy: {exc}")
        return None


def _can_preload_mcp_server_tools_without_runtime_auth(server_config: dict[str, Any]) -> bool:
    if not (server_config.get("auth_config") or {}):
        return True
    auth_config = _get_mcp_auth_config(server_config)
    if auth_config is None:
        return False
    return auth_config.provider == "legacy_static"


def _get_cached_mcp_tool_failure(cache_key: str) -> dict[str, Any] | None:
    entry = _mcp_tools_failure_cache.get(cache_key)
    if not entry:
        return None
    retry_at = float(entry.get("retry_at") or 0)
    if retry_at <= time.monotonic():
        _mcp_tools_failure_cache.pop(cache_key, None)
        return None
    return entry


def _record_mcp_tool_failure(cache_key: str, exc: BaseException) -> None:
    if _MCP_TOOL_FAILURE_COOLDOWN_SECONDS <= 0:
        return
    _mcp_tools_failure_cache[cache_key] = {
        "retry_at": time.monotonic() + _MCP_TOOL_FAILURE_COOLDOWN_SECONDS,
        "message": str(exc) or exc.__class__.__name__,
    }


def _clear_mcp_tool_failure(cache_key: str) -> None:
    _mcp_tools_failure_cache.pop(cache_key, None)


def _pop_matching_cache_keys(cache: LRUCache, predicate: Callable[[str], bool]) -> None:
    for key in [key for key in cache if predicate(key)]:
        cache.pop(key, None)


def _clear_mcp_tool_failure_cache_for_server(server_name: str) -> None:
    prefix = f"{server_name}:"
    _pop_matching_cache_keys(_mcp_tools_failure_cache, lambda key: key.startswith(prefix))


def _clear_resolved_headers_for_server(server_name: str) -> None:
    try:
        from yuxi.services.mcp.client_pool import clear_server_resolved_headers_cache

        clear_server_resolved_headers_cache(server_name)
    except Exception:
        pass


async def get_mcp_tools(
    server_name: str,
    additional_servers: dict[str, dict[str, Any]] | None = None,
    disabled_tools: list[str] = None,
    cache: bool = True,
    force_refresh: bool = False,
) -> list[Callable[..., Any]]:
    """
    获取指定 MCP 服务器的工具列表。

    优化生命周期：
    - 集成缓存策略模式 (CachePolicy)，动态决策是否在进程内容许缓存 Tool 对象。
    - 集成客户端连接池 (MCPClientPool)，复用 Stdio 长期子进程及 HTTP Keep-Alive 连接。
    """
    if additional_servers and server_name in additional_servers:
        server_config = additional_servers[server_name]
    else:
        from yuxi.services.mcp.server_service import get_enabled_mcp_server_config

        server_config = await get_enabled_mcp_server_config(server_name)

    if server_config is None:
        logger.warning(f"MCP server '{server_name}' not found in database or disabled")
        return []

    cache_descriptor = await _build_mcp_tool_cache_descriptor(server_name, server_config)
    cache_partition = cache_descriptor["cache_partition"]
    cache_prefix = cache_descriptor["cache_prefix"]
    cache_key = cache_descriptor["cache_key"]

    # 策略模式：根据 AuthProvider 确认是否容许内存缓存 Tool 实例对象
    from yuxi.services.mcp.cache_policy import CachePolicyFactory

    auth_config = _get_mcp_auth_config(server_config)
    policy = CachePolicyFactory.get_policy(auth_config.provider if auth_config else None)
    use_tool_object_cache = (
        cache
        and policy.should_cache_tool_object()
        and not bool(server_config.get(INTERNAL_PROXY_DISABLE_TOOL_OBJECT_CACHE_KEY))
    )

    all_processed_tools: list[Callable[..., Any]] = []

    async with _mcp_lock:
        if not force_refresh and use_tool_object_cache and cache_key in _mcp_tools_cache:
            all_processed_tools = _mcp_tools_cache[cache_key]

    if not all_processed_tools:
        if not force_refresh:
            failure_entry = _get_cached_mcp_tool_failure(cache_key)
            if failure_entry is not None:
                retry_in = max(0.0, float(failure_entry.get("retry_at") or 0) - time.monotonic())
                logger.debug(
                    f"Skip loading MCP tools for '{server_name}' during failure cooldown "
                    f"({retry_in:.1f}s left): {failure_entry.get('message')}"
                )
                return []

        try:
            client_config = {
                k: v
                for k, v in server_config.items()
                if k not in _MCP_INTERNAL_CONFIG_KEYS
            }

            # NOTE: 从长连接池中提取 ClientSession 实例
            # （对 Stdio 而言子进程被挂起复用，避免频繁启停；HTTP 协议亦保持 Keep-Alive）
            from yuxi.services.mcp.client_pool import mcp_client_pool

            session = await mcp_client_pool.get_session(
                server_name,
                partition_key=cache_descriptor["partition_key"],
                runtime_config=client_config,
            )

            # 如果 session 是 Fake Client (有 get_tools 方法)，我们直接调用它获取工具列表，避免 load_mcp_tools 报错
            if hasattr(session, "get_tools"):
                raw_tools = cast(list[Any], await session.get_tools())
            else:
                # 调用 langchain 官方加载工具，直接传入已预备并建立好的 session
                from langchain_mcp_adapters.tools import load_mcp_tools

                raw_tools = cast(list[Any], await load_mcp_tools(session, server_name=server_name))

            server_cc = to_camel_case(server_name)
            for tool in raw_tools:
                original_name = tool.name
                tool_cc = to_camel_case(original_name)
                unique_id = f"mcp__{server_cc}__{tool_cc}"

                if tool.metadata is None:
                    tool.metadata = {}
                tool.metadata["id"] = unique_id
                tool.handle_tool_error = True
                all_processed_tools.append(tool)

            if cache:
                if use_tool_object_cache:
                    async with _mcp_lock:
                        _pop_matching_cache_keys(
                            _mcp_tools_cache, lambda key: key.startswith(cache_prefix) and key != cache_key
                        )
                        _mcp_tools_cache[cache_key] = all_processed_tools

                await _mcp_tool_cache_store.set_manifest(
                    cache_key,
                    _serialize_mcp_tools_manifest(
                        server_name=server_name,
                        cache_partition=cache_partition,
                        cache_key=cache_key,
                        tools=all_processed_tools,
                    ),
                )

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

            _clear_mcp_tool_failure(cache_key)

        except Exception as e:
            _record_mcp_tool_failure(cache_key, e)
            logger.warning(
                f"MCP server '{server_name}' temporarily unavailable; "
                f"suppress retries for {_MCP_TOOL_FAILURE_COOLDOWN_SECONDS:.0f}s: {e}"
            )
            logger.debug(f"Failed to load tools from MCP server '{server_name}'", exc_info=True)
            try:
                from yuxi.services.mcp.client_pool import mcp_client_pool

                await mcp_client_pool.remove_session(server_name, cache_descriptor["partition_key"])
            except Exception as pool_err:
                logger.warning(f"Failed to remove stale session for {server_name}: {pool_err}")
            return []

    return [t for t in all_processed_tools if t.name not in disabled_tools] if disabled_tools else all_processed_tools


async def get_tools_from_all_servers(server_names: list[str] | None = None) -> list[Callable[..., Any]]:
    """批量载入指定或所有可用服务的工具（用于系统初始化及预热）"""
    from yuxi.services.mcp.server_service import _load_enabled_mcp_server_configs

    names: list[str] | None = None
    if server_names is not None:
        names = []
        seen: set[str] = set()
        for value in server_names:
            if not isinstance(value, str):
                continue
            name = value.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        if not names:
            return []

    server_configs = await _load_enabled_mcp_server_configs(names=names)
    all_tools = []
    for server_name, server_config in server_configs.items():
        if not _can_preload_mcp_server_tools_without_runtime_auth(server_config):
            logger.info(f"Skip MCP tool preload for '{server_name}' because runtime auth context is required")
            continue
        tools = await get_mcp_tools(server_name, additional_servers={server_name: server_config})
        all_tools.extend(tools)
    return all_tools


async def clear_mcp_cache() -> None:
    """清空本地内存工具缓存"""
    global _mcp_tools_cache, _mcp_tools_failure_cache
    _mcp_tools_cache = LRUCache(maxsize=128)
    _mcp_tools_failure_cache = LRUCache(maxsize=256)

    try:
        from yuxi.services.mcp.client_pool import clear_resolved_headers_cache, mcp_client_pool

        await mcp_client_pool.shutdown()
        clear_resolved_headers_cache()
    except Exception:
        pass


def clear_mcp_server_tools_cache(server_name: str) -> None:
    """清空指定服务器下的所有本地缓存"""
    global _mcp_tools_cache
    prefix = f"{server_name}:"
    _pop_matching_cache_keys(_mcp_tools_cache, lambda key: key.startswith(prefix))
    _clear_mcp_tool_failure_cache_for_server(server_name)
    _clear_resolved_headers_for_server(server_name)


def clear_mcp_connection_tools_cache(server_name: str, connection_id: int | None) -> None:
    """清空指定连接下的本地内存缓存"""
    if connection_id is None:
        return
    global _mcp_tools_cache
    prefix = f"{server_name}:"
    suffix = f":connection:{connection_id}:"
    _pop_matching_cache_keys(_mcp_tools_cache, lambda key: key.startswith(prefix) and suffix in key)
    _pop_matching_cache_keys(_mcp_tools_failure_cache, lambda key: key.startswith(prefix) and suffix in key)
    _clear_resolved_headers_for_server(server_name)


async def invalidate_mcp_server_tools_cache(server_name: str) -> None:
    """全局失效指定服务器的全部二级缓存"""
    clear_mcp_server_tools_cache(server_name)
    await _mcp_tool_cache_store.bump_server_revision(server_name)


async def invalidate_mcp_connection_tools_cache(server_name: str, connection_id: int | None) -> None:
    """失效指定连接下的二级缓存区划"""
    if connection_id is None:
        return
    clear_mcp_connection_tools_cache(server_name, connection_id)
    await _mcp_tool_cache_store.bump_partition_revision(server_name, f"connection:{connection_id}")


async def _invalidate_mcp_tools_cache_for_connection(connection: MCPConnection) -> None:
    """依据 Scope 类别自动刷新并失效缓存"""
    if connection.scope_type == "system":
        await invalidate_mcp_server_tools_cache(connection.server_name)
    else:
        await invalidate_mcp_connection_tools_cache(connection.server_name, connection.id)


async def _clear_mcp_connection_runtime_auth_cache(connection_id: int | None) -> None:
    """清理 Redis 中缓存的 Access Token 与锁状态"""
    if connection_id is None:
        return
    from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache

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
    """清理服务器下所有关联连接的 Token 缓存"""
    from yuxi.services.mcp.connection_service import list_mcp_connections

    connections = await list_mcp_connections(db, server_name=server_name)
    for connection in connections:
        await _clear_mcp_connection_runtime_auth_cache(getattr(connection, "id", None))


def get_mcp_tools_stats(server_name: str) -> dict[str, int] | None:
    return _mcp_tools_stats.get(server_name)


async def get_enabled_mcp_tools(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list:
    from yuxi.services.mcp.server_service import get_runtime_mcp_server_config

    token = mcp_auth_context_var.set(auth_context) if auth_context else None

    try:
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
        return await get_mcp_tools(
            server_name,
            additional_servers={server_name: config},
            disabled_tools=disabled_tools,
        )
    finally:
        if token is not None:
            mcp_auth_context_var.reset(token)


async def get_all_mcp_tools(
    server_name: str,
    *,
    auth_context: AuthContext | None = None,
    db: AsyncSession | None = None,
    http_client: httpx.AsyncClient | None = None,
    force_refresh: bool = False,
) -> list:
    from yuxi.services.mcp.server_service import get_enabled_mcp_server_config, get_runtime_mcp_server_config

    token = mcp_auth_context_var.set(auth_context) if auth_context else None

    try:
        if auth_context is None and db is None:
            config = await get_enabled_mcp_server_config(server_name)
        else:
            config = await get_runtime_mcp_server_config(
                server_name,
                auth_context=auth_context,
                db=db,
                http_client=http_client,
            )
        if config is None:
            logger.warning(f"MCP server '{server_name}' not found in database or disabled")
            return []

        if not force_refresh:
            cache_descriptor = await _build_mcp_tool_cache_descriptor(server_name, config)
            manifest = await _mcp_tool_cache_store.get_manifest(cache_descriptor["cache_key"])
            if manifest is not None:
                return _deserialize_mcp_tool_manifest(manifest)

        return await get_mcp_tools(
            server_name,
            additional_servers={server_name: config},
            disabled_tools=[],
            cache=True,
            force_refresh=force_refresh,
        )
    finally:
        if token is not None:
            mcp_auth_context_var.reset(token)


async def toggle_tool_enabled(
    db: AsyncSession,
    server_name: str,
    tool_name: str,
    updated_by: str | None = None,
) -> tuple[bool, MCPServer]:
    """切换单个工具的启用状态"""
    from yuxi.services.mcp.server_service import get_mcp_server

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

    # 清除内存工具缓存
    clear_mcp_server_tools_cache(server_name)

    logger.info(f"Toggled tool '{tool_name}' for server '{server_name}' enabled={enabled}")
    return enabled, server
