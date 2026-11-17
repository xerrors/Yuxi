"""aBridge MCP 的当前用户身份和目标地址绑定。"""

import httpx

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.abridge.options import AbridgeConnection, connection_settings
from yuxi.modules.identity.services.provider import ProviderService
from yuxi.shared.errors import StateConflictError


class AbridgeAuth(httpx.Auth):
    """出站前重读地址与用户，短期令牌不进入持久连接或工具元数据。"""

    requires_request_body = True

    def __init__(self, uid: str, settings: AbridgeConnection, before_request=None):
        """只捕获身份和两个非敏感地址，管理员凭据不进入个人请求。"""
        self.uid = uid
        self.settings = settings
        self.before_request = before_request

    async def async_auth_flow(self, request):
        """旧工具在配置修改后拒绝出站，令牌仅用于指定 MCP 端点。"""
        current = await connection_settings()
        if not current.configured:
            raise StateConflictError("aBridge 未配置或身份签发已关闭")
        if current != self.settings:
            raise StateConflictError("aBridge 地址已修改，请重新运行")
        if request.url.copy_with(query=None) != httpx.URL(self.settings.service_origin + "/mcp"):
            raise StateConflictError("aBridge 请求目标与配置不一致")
        if self.before_request is not None:
            await self.before_request(request)
        token = await issue_abridge_delegation(self.uid, self.settings.public_origin + "/mcp")
        request.headers["Authorization"] = "Bearer " + token
        yield request


async def abridge_mcp_connection(*, uid: str, before_request=None) -> dict:
    """按当前 Options 装配 SDK 认证与服务地址。"""
    if not uid:
        raise StateConflictError("aBridge 请求缺少当前用户身份")
    settings = await connection_settings()
    if not settings.configured:
        raise StateConflictError("aBridge 未配置")
    return {
        "url": settings.service_origin + "/mcp",
        "auth": AbridgeAuth(uid, settings, before_request),
        "httpx_client_factory": abridge_http_client,
    }


def abridge_http_client(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """内部通信不使用环境代理，拒绝携带委托跟随重定向。"""
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, trust_env=False, follow_redirects=False)


async def issue_abridge_delegation(uid: str, audience: str) -> str:
    """只供可信运行上下文调用，短期委托仍由 aBridge 执行本地设备授权。"""
    async with pg_manager.get_async_session_context() as db:
        service = ProviderService(db)
        user = await service.active_user(uid)
        return service.sign(
            user.uid,
            audience,
            "abridge-delegation",
            60,
            scope="devices:read devices:operate",
            name=user.username,
            client_id=service.config.client_id,
        )
