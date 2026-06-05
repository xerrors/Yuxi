from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.orchestrator import AuthContext, resolve_runtime_mcp_config
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer

from server.utils.auth_utils import AuthUtils

_proxy_http_client: httpx.AsyncClient | None = None


def get_shared_proxy_client() -> httpx.AsyncClient:
    global _proxy_http_client
    if _proxy_http_client is None:
        _proxy_http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, pool=120.0, read=120.0, write=30.0))
    return _proxy_http_client


async def close_shared_proxy_client() -> None:
    global _proxy_http_client
    if _proxy_http_client is not None:
        await _proxy_http_client.aclose()
        _proxy_http_client = None


INTERNAL_PROXY_TOKEN_HEADER = "X-Yuxi-MCP-Proxy-Token"
INTERNAL_PROXY_DISABLE_TOOL_OBJECT_CACHE_KEY = "__yuxi_disable_tool_object_cache"
_PROXY_TOKEN_TYPE = "mcp_proxy"
_DYNAMIC_HTTP_PROVIDERS = {"custom_http_token", "client_credentials", "authorization_code"}
_HTTP_TRANSPORTS = {"streamable_http", "sse"}
_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def should_use_internal_proxy(server: MCPServer, auth_config: MCPAuthConfig, proxy_base_url: str | None) -> bool:
    return bool(
        proxy_base_url and server.transport in _HTTP_TRANSPORTS and auth_config.provider in _DYNAMIC_HTTP_PROVIDERS
    )


def create_proxy_access_token(server_name: str, auth_context: AuthContext) -> str:
    return AuthUtils.create_access_token(
        {
            "sub": f"mcp-proxy:{server_name}",
            "token_type": _PROXY_TOKEN_TYPE,
            "server_name": server_name,
            "user_id": auth_context.user_id,
            "department_id": auth_context.department_id,
            "work_id": auth_context.work_id,
        },
        expires_delta=timedelta(minutes=15),
    )


def decode_proxy_access_token(token: str, *, server_name: str) -> AuthContext:
    payload = AuthUtils.decode_token(token)
    if not payload:
        raise ValueError("invalid internal proxy token")
    if payload.get("token_type") != _PROXY_TOKEN_TYPE:
        raise ValueError("invalid internal proxy token type")
    if payload.get("server_name") != server_name:
        raise ValueError("internal proxy token server mismatch")
    return AuthContext(
        user_id=payload.get("user_id"),
        department_id=payload.get("department_id"),
        work_id=payload.get("work_id"),
    )


def build_internal_proxy_url(proxy_base_url: str, server_name: str) -> str:
    return f"{proxy_base_url.rstrip('/')}/api/internal/mcp-proxy/{server_name}"


def build_proxy_runtime_config(
    server: MCPServer,
    *,
    auth_context: AuthContext,
    proxy_base_url: str,
) -> dict[str, Any]:
    config = server.to_mcp_config()
    config.pop("auth_config", None)
    headers = dict(config.get("headers") or {})
    headers[INTERNAL_PROXY_TOKEN_HEADER] = create_proxy_access_token(server.name, auth_context)
    config["headers"] = headers
    config["url"] = build_internal_proxy_url(proxy_base_url, server.name)
    config[INTERNAL_PROXY_DISABLE_TOOL_OBJECT_CACHE_KEY] = True
    return config


def _merge_upstream_headers(
    base_headers: dict[str, Any],
    request_headers: dict[str, str] | None,
) -> dict[str, Any]:
    merged = dict(base_headers or {})
    _PROTECTED_HEADERS = {
        INTERNAL_PROXY_TOKEN_HEADER.lower(),
        "authorization",
    }
    for key, value in (request_headers or {}).items():
        if key.lower() in _HOP_BY_HOP_HEADERS or key.lower() in _PROTECTED_HEADERS:
            continue
        merged[key] = value
    return merged


def _build_target_url(base_url: str, path: str = "", query_params: dict[str, Any] | None = None) -> str:
    if not path:
        target = base_url
    else:
        target = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query_params:
        return f"{target}?{urlencode(query_params, doseq=True)}"
    return target


def _mark_reauth_required(connection: MCPConnection | None, message: str) -> None:
    if connection is None:
        return
    connection.status = "reauth_required"
    meta_json = dict(connection.meta_json or {})
    meta_json["last_error"] = {
        "code": "unauthorized",
        "message": message,
    }
    connection.meta_json = meta_json


def _record_scope_error(connection: MCPConnection | None, message: str) -> None:
    if connection is None:
        return
    meta_json = dict(connection.meta_json or {})
    meta_json["last_error"] = {
        "code": "insufficient_scope",
        "message": message,
    }
    connection.meta_json = meta_json


async def handle_mcp_proxy_request(
    server_name: str,
    request: Request,
    path: str,
    internal_token: str,
    db: AsyncSession,
) -> Response:
    """内部网关主入口：鉴权解析、查库拦截与流式代理"""
    from yuxi.services.mcp.server_service import get_mcp_server

    try:
        auth_context = decode_proxy_access_token(internal_token, server_name=server_name)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    server = await get_mcp_server(db, server_name)
    if server is None:
        raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在")
    if not bool(getattr(server, "enabled", True)):
        raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在或已停用")

    auth_config = MCPAuthConfig.model_validate(server.auth_config_json or {})

    from yuxi.services.mcp.connection_service import _resolve_scope_id

    scope_id = _resolve_scope_id(auth_config.binding_scope, auth_context)
    connection = None
    if scope_id is not None:
        result = await db.execute(
            select(MCPConnection).where(
                MCPConnection.server_name == server.name,
                MCPConnection.scope_type == auth_config.binding_scope,
                MCPConnection.scope_id == scope_id,
                MCPConnection.status == "active",
            )
        )
        connection = result.scalar_one_or_none()

    if auth_config.binding_scope != "inline" and connection is None:
        raise HTTPException(status_code=403, detail="当前用户没有该 MCP 的有效连接")

    # 注意：我们读取整个 request body，因为 MCP 请求参数通常极小，
    # 但由于可能有 401 重试，我们需要保存下 body 来实现背压重发。
    body = await request.body()
    return await _proxy_mcp_request_stream(
        server=server,
        connection=connection,
        auth_context=auth_context,
        request=request,
        body=body,
        path=path,
        db=db,
    )


async def _proxy_mcp_request_stream(
    server: MCPServer,
    *,
    connection: MCPConnection | None,
    auth_context: AuthContext,
    request: Request,
    body: bytes,
    path: str = "",
    db: AsyncSession,
    _http_client: httpx.AsyncClient | None = None,
    _token_cache: Any | None = None,
) -> Response:
    """底层流式转发逻辑：处理 HTTPX 透传、SSE 和 401 重试闭环事务"""
    auth_config = MCPAuthConfig.model_validate(server.auth_config_json or {})
    if server.transport not in _HTTP_TRANSPORTS:
        raise HTTPException(
            status_code=400, detail=f"Internal proxy only supports HTTP MCP transports, got: {server.transport}"
        )

    connect_timeout = server.timeout or 60.0
    read_timeout = server.sse_read_timeout or connect_timeout
    request_timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=connect_timeout,
        pool=connect_timeout,
    )
    http_client = _http_client or get_shared_proxy_client()

    if _token_cache is not None:
        token_cache = _token_cache
    else:
        from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache

        token_cache = RedisTokenCache()

    max_attempts = 2 if auth_config.refresh_policy.retry_once_on_401 else 1

    for attempt in range(max_attempts):
        runtime_config = await resolve_runtime_mcp_config(
            server,
            auth_context=auth_context,
            connection=connection,
            http_client=http_client,
            token_cache=token_cache,
        )
        target_url = _build_target_url(runtime_config["url"], path=path, query_params=dict(request.query_params))
        upstream_headers = _merge_upstream_headers(runtime_config.get("headers") or {}, dict(request.headers))

        request_obj = http_client.build_request(
            method=request.method.upper(),
            url=target_url,
            headers=upstream_headers,
            content=body,
            timeout=request_timeout,
        )

        # 使用 send(stream=True) 获取异步可迭代响应而不会阻塞 SSE 长链接
        response = await http_client.send(request_obj, stream=True)

        if response.status_code == 403:
            await response.aclose()
            _record_scope_error(connection, "MCP upstream rejected request due to insufficient scope")
            if connection is not None:
                await db.commit()
            return Response(
                content='{"error": "insufficient_scope", "message": "当前授权范围不足"}',
                status_code=403,
                media_type="application/json",
                background=None,
            )

        if response.status_code != 401:
            # 正常响应，此时直接闭环提交事务，防止污染外层
            if connection is not None and hasattr(db, "commit"):
                await db.commit()

            async def proxy_stream_generator():
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                finally:
                    await response.aclose()

            resp_headers = {}
            for k, v in response.headers.items():
                if k.lower() not in _HOP_BY_HOP_HEADERS and k.lower() not in ("content-encoding", "content-length"):
                    resp_headers[k] = v

            return StreamingResponse(
                proxy_stream_generator(), status_code=response.status_code, headers=resp_headers, background=None
            )

        # 如果是 401，回收流连接并准备重试
        await response.aclose()
        if attempt + 1 >= max_attempts:
            break
        if connection is not None and getattr(connection, "id", None) is not None:
            await token_cache.delete_access_token(connection.id)

    _mark_reauth_required(connection, "MCP upstream returned 401 after retry")
    if connection is not None:
        await db.commit()
    return Response(
        content='{"error": "reauth_required", "message": "连接失效，请重新连接"}',
        status_code=424,
        media_type="application/json",
        background=None,
    )
