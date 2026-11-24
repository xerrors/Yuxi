"""aBridge 的浏览器登记协议。设备归属、绑定版本及一次性票据均由服务端决定。"""

from typing import Literal

import httpx
from pydantic import BaseModel, Field

from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.extensions.plugins.abridge.client import abridge_http_client
from yuxi.modules.extensions.plugins.abridge.options import AbridgeConnection
from yuxi.modules.identity.services.provider import ProviderService
from yuxi.shared.errors import DomainError, OperationTimeoutError, PermissionDeniedError, StateConflictError, UpstreamError


class BrowserBinding(BaseModel):
    """上游返回的设备、安装身份和凭据兑换状态。"""

    installation_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(strict=True, ge=1)
    device_id: str = Field(min_length=1, max_length=160)
    status: Literal["online", "offline", "paused", "revoked"]
    key_issued: bool = Field(strict=True)
    bootstrap_token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)

    def require_available(self) -> None:
        """设备暂停或撤权后，重复登记不得重新启用它。"""
        if self.status == "revoked":
            raise PermissionDeniedError("aBridge 托管浏览器授权已撤销", code="abridge_managed_browser_revoked")
        if self.status == "paused":
            raise StateConflictError("aBridge 托管浏览器已暂停，请在设备端恢复", code="abridge_managed_browser_paused")


class _EnrollmentStatus(BaseModel):
    """只读登记端点返回的协议版本和公开来源。"""

    version: Literal[2]
    enabled: bool = Field(strict=True)
    browser_origin: str
    binding: BrowserBinding | None


async def read_binding(scope: ActorScope, settings: AbridgeConnection) -> BrowserBinding | None:
    """读取现有登记，不创建账号、设备或票据。"""
    reply = await _request(scope, settings, "status")
    try:
        status = _EnrollmentStatus.model_validate(reply)
        if status.browser_origin != settings.public_origin:
            raise ValueError
    except ValueError:
        raise UpstreamError("aBridge 登记协议或公开来源未确认", code="abridge_managed_browser_unavailable") from None
    if not status.enabled:
        raise StateConflictError("aBridge 未启用托管浏览器", code="abridge_managed_browser_upstream_disabled")
    return status.binding


async def register_browser(
    scope: ActorScope, settings: AbridgeConnection, installation: str, previous: BrowserBinding | None, *, replace: bool, username: str
) -> BrowserBinding:
    """按已读取的版本登记或换绑；并发变更由 aBridge 拒绝。"""
    revision = previous.revision if previous else 0
    claims = {"installation_id": installation, "revision": revision, "replace": replace}
    if scope.app_id is None and previous is None:
        claims["name"] = username[:120]
    reply = await _request(scope, settings, "provision", **claims)
    try:
        binding = BrowserBinding.model_validate(reply)
        if (
            binding.installation_id != installation
            or binding.revision != (revision + 1 if replace or previous is None else revision)
            or (previous is not None and binding.device_id != previous.device_id)
            or bool(binding.bootstrap_token) == binding.key_issued
        ):
            raise ValueError
    except ValueError:
        raise UpstreamError("aBridge 登记结果与请求不符", code="abridge_managed_browser_unavailable") from None
    binding.require_available()
    return binding


def sign_browser_request(uid: str, settings: AbridgeConnection, path: str, purpose: str, **claims) -> str:
    """仅为指定来源、端点和用途签发短期凭据。"""
    provider = ProviderService()
    return provider.sign(uid, settings.public_origin + path, purpose, 60, client_id=provider.config.client_id, **claims)


async def _request(scope: ActorScope, settings: AbridgeConnection, action: Literal["status", "provision"], **claims) -> dict:
    """发送当前主体的登记请求，只向调用者返回脱敏错误。"""
    mode = "personal" if scope.app_id is None else "managed"
    path = f"/api/{mode}-browsers/{action}"
    token = sign_browser_request(scope.uid, settings, path, f"abridge-{mode}-browser-{action}", **claims)
    try:
        async with abridge_http_client(timeout=5) as client:
            response = await client.request(
                "GET" if action == "status" else "POST", settings.service_origin + path, headers={"Authorization": "Bearer " + token}
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise OperationTimeoutError("等待 aBridge 托管浏览器响应超时", code="abridge_managed_browser_timeout") from None
    except httpx.HTTPStatusError as exc:
        raise _response_error(exc.response) from None
    except (httpx.HTTPError, ValueError):
        raise UpstreamError("aBridge 托管浏览器登记不可用", code="abridge_managed_browser_unavailable") from None


def _response_error(response: httpx.Response) -> DomainError:
    """映射对接契约中的拒绝原因。"""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, TypeError, KeyError):
        code = None
    if response.status_code == 403 and code == "managed_browser_revoked":
        return PermissionDeniedError("aBridge 托管浏览器授权已撤销", code="abridge_managed_browser_revoked")
    if response.status_code == 409 and code == "stale_binding":
        return StateConflictError("aBridge 托管浏览器登记已改变，请重试", code="abridge_managed_browser_stale")
    if response.status_code == 409 and code == "device_paused":
        return StateConflictError("aBridge 托管浏览器已暂停，请在设备端恢复", code="abridge_managed_browser_paused")
    if response.status_code == 404:
        return StateConflictError("aBridge 未启用或不支持托管浏览器协议", code="abridge_managed_browser_upstream_disabled")
    return UpstreamError("aBridge 托管浏览器登记不可用", code="abridge_managed_browser_unavailable")
