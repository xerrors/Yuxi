from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db
from yuxi.services.mcp_auth.proxy_service import (
    INTERNAL_PROXY_TOKEN_HEADER,
    handle_mcp_proxy_request,
)

mcp_internal = APIRouter(prefix="/internal/mcp-proxy", tags=["mcp-internal"])


@mcp_internal.api_route(
    "/{server_name}{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_mcp_server_request(
    server_name: str,
    request: Request,
    path: str = "",
    internal_token: str | None = Header(None, alias=INTERNAL_PROXY_TOKEN_HEADER),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """代理路由（纯路由层）：业务鉴权、DB操作及背压透传已全部下沉到 proxy_service 领域服务处理"""
    # 去除前导斜杠，以兼容不带 path 和带 path 两种情况
    path = path.lstrip("/")
    
    return await handle_mcp_proxy_request(
        server_name=server_name,
        request=request,
        path=path,
        internal_token=internal_token or "",
        db=db,
    )
