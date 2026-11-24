"""aBridge MCP 的当前用户身份和目标地址绑定。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.mcp.runtime import MCPUnavailableError
from yuxi.modules.extensions.plugins.abridge.options import AbridgeConnection, connection_settings
from yuxi.modules.identity.provider import ProviderError
from yuxi.modules.identity.services.provider import ProviderService
from yuxi.shared.errors import PermissionDeniedError, StateConflictError

if TYPE_CHECKING:
    from yuxi.modules.agents.runtime.context import BaseContext


class AbridgeAuth(httpx.Auth):
    """出站前重读地址与用户，短期令牌不进入持久连接或工具元数据。"""

    requires_request_body = True

    def __init__(self, uid: str, settings: AbridgeConnection, before_request=None, *, context: BaseContext | None = None):
        """固定可信的运行身份及出站地址。"""
        self.uid = uid
        self.settings = settings
        self.before_request = before_request
        self.scope = context.actor_scope if context is not None else None
        self.run_id = context.run_id if context is not None else None
        self.worker_id = context.worker_id if context is not None else None

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
        request.headers["Authorization"] = "Bearer " + await self._issue_token(current)
        yield request

    async def _issue_token(self, settings: AbridgeConnection) -> str:
        """个人委托仅接收有效人类用户，APP 委托绑定当前 Run 和 Worker。"""
        scope = self.scope
        if scope is not None and scope.uid != self.uid:
            raise PermissionDeniedError("aBridge 缺少匹配的执行身份")
        if scope is None or scope.app_id is None:
            try:
                return await issue_abridge_delegation(self.uid, settings.public_origin + "/mcp")
            except ProviderError as exc:
                if exc.status != 403:
                    raise
                raise PermissionDeniedError(exc.description) from exc

        if not self.run_id or not self.worker_id:
            raise PermissionDeniedError("托管浏览器需要当前执行身份和有效运行租约")

        from yuxi.modules.extensions.plugins.abridge.managed_browser import managed_run_delegation

        try:
            return await managed_run_delegation(scope, self.run_id, self.worker_id, settings)
        except (PermissionDeniedError, StateConflictError) as exc:
            if exc.code == "abridge_managed_browser_offline":
                raise MCPUnavailableError(exc.message, retryable=True) from exc
            if exc.code in {
                "abridge_managed_browser_binding_missing",
                "abridge_managed_browser_disabled",
                "abridge_managed_browser_requires_cube",
                "abridge_managed_browser_revoked",
                "sandbox_unavailable",
            }:
                raise MCPUnavailableError(exc.message, retryable=False) from exc
            raise


async def abridge_mcp_connection(*, uid: str, before_request=None, context: BaseContext | None = None) -> dict:
    """按当前 Options 装配 SDK 认证与服务地址。"""
    if not uid:
        raise StateConflictError("aBridge 请求缺少当前用户身份")
    settings = await connection_settings()
    if not settings.configured:
        raise StateConflictError("aBridge 未配置")
    return {
        "url": settings.service_origin + "/mcp",
        "auth": AbridgeAuth(uid, settings, before_request, context=context),
        "httpx_client_factory": abridge_http_client,
    }


class _DelegationHttpClient(httpx.AsyncClient):
    """在单次 MCP 会话内保留 SDK 通知写入器可能丢弃的执行撤权。"""

    _denied: PermissionDeniedError | None = None

    async def send(self, request, **kwargs):
        """记录本地出站授权拒绝，远端 HTTP 与传输错误保持原语义。"""
        try:
            return await super().send(request, **kwargs)
        except PermissionDeniedError as exc:
            if request.method != "DELETE":
                self._denied = exc
            raise

    async def __aexit__(self, exc_type, exc_value, traceback):
        """SDK 将通知失败转换为流关闭时，向同次调用恢复原拒绝。"""
        await super().__aexit__(exc_type, exc_value, traceback)
        # 工具适配器会先暂存异常，再退出 HTTP 会话；通知写入失败时这里也可能收到 None。
        if self._denied is not None and (exc_value is None or isinstance(exc_value, Exception)):
            raise self._denied from None


def abridge_http_client(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """内部通信不使用环境代理，拒绝携带委托跟随重定向。"""
    return _DelegationHttpClient(headers=headers, timeout=timeout, auth=auth, trust_env=False, follow_redirects=False)


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
