"""以真实运行用户身份执行 MCP HTTP 请求。"""

import json

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.mcp.oauth_config import STATIC_AUTH_MODES, config_revision
from yuxi.modules.extensions.mcp.oauth_service import MCPOAuthService
from yuxi.modules.extensions.mcp.runtime import to_camel_case
from yuxi.shared.errors import StateConflictError


class PersonalMCPAuth(httpx.Auth):
    """仅捕获用户和配置修订，每次出站重新读取授权；不缓存 token。"""

    requires_request_body = True

    def __init__(self, slug, uid, revision, *, auth_mode="oauth", url=None, management=False):
        """绑定服务来源与个人身份，每次请求重新取得凭证。"""
        self.slug, self.uid, self.revision = slug, uid, revision
        self.auth_mode = auth_mode
        self.management = management
        endpoint = httpx.URL(url) if url else None
        self.origin = (endpoint.scheme, endpoint.host, endpoint.port) if endpoint else None

    async def async_auth_flow(self, request):
        """实际工具调用再次检查开关，已断开的旧工具对象也无法使用。"""
        if self.origin is not None and (request.url.scheme, request.url.host, request.url.port) != self.origin:
            raise StateConflictError("连接器请求来源与配置不一致")
        tool_name = None
        if request.method == "POST" and request.content:
            payload = json.loads(request.content)
            if payload.get("method") == "tools/call":
                tool_name = payload.get("params", {}).get("name")
        # 管理目录只放行 tools/list；tools/call 仍走运行启停，避免停用服务被目录客户端调用。
        catalog = self.management and tool_name is None
        async with pg_manager.get_async_session_context() as db:
            service = MCPOAuthService(db)
            if self.auth_mode in STATIC_AUTH_MODES:
                header, value, generation = await service.static_credentials(
                    self.slug, self.uid, tool_name=tool_name, expected_revision=self.revision, catalog=catalog
                )
            else:
                token = await service.access_token(
                    self.slug, self.uid, tool_name=tool_name, expected_revision=self.revision, catalog=catalog
                )
                header, value = "Authorization", "Bearer " + token
        request.headers[header] = value
        response = yield request
        if response.status_code == 401:
            async with pg_manager.get_async_session_context() as db:
                if self.auth_mode in STATIC_AUTH_MODES:
                    await MCPOAuthService(db).reject_credentials(self.slug, self.uid, generation)
                else:
                    await MCPOAuthService(db).reject_access_token(self.slug, self.uid, token)
            raise StateConflictError("连接器拒绝了个人授权，请重新连接", code="mcp_reconnect_required")
        if response.status_code == 403:
            raise StateConflictError("连接器服务方权限不足，请检查个人账号权限", code="mcp_remote_forbidden")


async def personal_mcp_tools(server, uid, *, include_disabled=False, management=False):
    """个人工具绑定可信用户与配置修订，传给 adapter 的配置不含凭据。"""
    if not uid:
        raise StateConflictError("连接器个人授权缺少运行用户身份")
    config = server.to_mcp_config()
    config.pop("disabled_tools", None)
    config["auth"] = PersonalMCPAuth(
        server.slug, uid, config_revision(server), auth_mode=server.auth_mode, url=server.url, management=management
    )
    tools = await MultiServerMCPClient({server.slug: config}).get_tools()
    for tool in tools:
        if tool.metadata is None:
            tool.metadata = {}
        tool.metadata["id"] = f"mcp__{to_camel_case(server.slug)}__{to_camel_case(tool.name)}"
        tool.handle_tool_error = True
    return tools if include_disabled else [tool for tool in tools if tool.name not in (server.disabled_tools or [])]
