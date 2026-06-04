from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.proxy_service import (
    INTERNAL_PROXY_TOKEN_HEADER,
    decode_proxy_access_token,
    proxy_mcp_request,
)
from yuxi.services.mcp_service import _resolve_scope_id, get_mcp_server
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer
from yuxi.utils import logger

mcp_internal = APIRouter(prefix="/internal/mcp-proxy", tags=["mcp-internal"])


async def _load_active_connection(
    db: AsyncSession,
    *,
    server: MCPServer,
    auth_context,
) -> MCPConnection | None:
    auth_payload = server.auth_config_json or {}
    if not auth_payload:
        return None

    auth_config = MCPAuthConfig.model_validate(auth_payload)
    scope_id = _resolve_scope_id(auth_config.binding_scope, auth_context)
    if scope_id is None:
        return None

    result = await db.execute(
        select(MCPConnection).where(
            MCPConnection.server_name == server.name,
            MCPConnection.scope_type == auth_config.binding_scope,
            MCPConnection.scope_id == scope_id,
            MCPConnection.status == "active",
        )
    )
    return result.scalar_one_or_none()


@mcp_internal.api_route(
    "/{server_name}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@mcp_internal.api_route(
    "/{server_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_mcp_server_request(
    server_name: str,
    request: Request,
    path: str = "",
    internal_token: str | None = Header(None, alias=INTERNAL_PROXY_TOKEN_HEADER),
    db: AsyncSession = Depends(get_db),
):
    if not internal_token:
        raise HTTPException(status_code=401, detail="missing internal proxy token")

    try:
        auth_context = decode_proxy_access_token(internal_token, server_name=server_name)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    server = await get_mcp_server(db, server_name)
    if server is None:
        raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在")

    try:
        connection = await _load_active_connection(db, server=server, auth_context=auth_context)
        auth_config = MCPAuthConfig.model_validate(server.auth_config_json or {})
        if auth_config.binding_scope != "inline" and connection is None:
            raise HTTPException(status_code=403, detail="当前用户没有该 MCP 的有效连接")

        body = await request.body()
        upstream_response = await proxy_mcp_request(
            server,
            connection=connection,
            auth_context=auth_context,
            method=request.method,
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            body=body,
            path=path,
        )
        if connection is not None and hasattr(db, "commit"):
            await db.commit()
        response_headers = {}
        content_type = upstream_response.headers.get("content-type")
        if content_type:
            response_headers["content-type"] = content_type
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to proxy MCP server '{server_name}': {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
