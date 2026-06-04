from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from server.utils.auth_utils import AuthUtils
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.orchestrator import AuthContext, resolve_runtime_mcp_config
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer

INTERNAL_PROXY_TOKEN_HEADER = "X-Yuxi-MCP-Proxy-Token"
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
        proxy_base_url
        and server.transport in _HTTP_TRANSPORTS
        and auth_config.provider in _DYNAMIC_HTTP_PROVIDERS
    )


def create_proxy_access_token(server_name: str, auth_context: AuthContext) -> str:
    return AuthUtils.create_access_token(
        {
            "sub": f"mcp-proxy:{server_name}",
            "token_type": _PROXY_TOKEN_TYPE,
            "server_name": server_name,
            "user_id": auth_context.user_id,
            "department_id": auth_context.department_id,
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
    return config


def _merge_upstream_headers(
    base_headers: dict[str, Any],
    request_headers: dict[str, str] | None,
) -> dict[str, Any]:
    merged = dict(base_headers or {})
    for key, value in (request_headers or {}).items():
        if key.lower() in _HOP_BY_HOP_HEADERS or key.lower() == INTERNAL_PROXY_TOKEN_HEADER.lower():
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


async def proxy_mcp_request(
    server: MCPServer,
    *,
    connection: MCPConnection | None,
    auth_context: AuthContext,
    method: str,
    headers: dict[str, str] | None,
    query_params: dict[str, Any] | None,
    body: bytes,
    path: str = "",
    http_client: httpx.AsyncClient | None = None,
    token_cache: Any | None = None,
) -> httpx.Response:
    auth_config = MCPAuthConfig.model_validate(server.auth_config_json or {})
    if server.transport not in _HTTP_TRANSPORTS:
        raise ValueError(f"Internal proxy only supports HTTP MCP transports, got: {server.transport}")

    if http_client is None:
        http_client = httpx.AsyncClient()
        should_close = True
    else:
        should_close = False

    try:
        max_attempts = 2 if auth_config.refresh_policy.retry_once_on_401 else 1
        for attempt in range(max_attempts):
            runtime_config = await resolve_runtime_mcp_config(
                server,
                auth_context=auth_context,
                connection=connection,
                http_client=http_client,
                token_cache=token_cache,
            )
            target_url = _build_target_url(runtime_config["url"], path=path, query_params=query_params)
            upstream_headers = _merge_upstream_headers(runtime_config.get("headers") or {}, headers)
            response = await http_client.request(
                method=method.upper(),
                url=target_url,
                headers=upstream_headers,
                content=body,
            )
            if response.status_code == 403:
                _record_scope_error(connection, "MCP upstream rejected request due to insufficient scope")
                return httpx.Response(
                    403,
                    json={
                        "error": "insufficient_scope",
                        "message": "当前授权范围不足，请联系管理员或重新授权",
                    },
                )
            if response.status_code != 401:
                return response
            if attempt + 1 >= max_attempts:
                break
            if token_cache is not None and connection is not None and getattr(connection, "id", None) is not None:
                await token_cache.delete_access_token(connection.id)

        _mark_reauth_required(connection, "MCP upstream returned 401 after retry")
        return httpx.Response(
            424,
            json={
                "error": "reauth_required",
                "message": "连接失效，请重新连接",
            },
        )
    finally:
        if should_close:
            await http_client.aclose()
