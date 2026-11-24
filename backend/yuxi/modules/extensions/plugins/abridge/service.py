"""aBridge 地址解析及本人设备读取。"""

import json
from datetime import timedelta
from typing import Literal

import httpx
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, ValidationError

from yuxi.modules.extensions.plugins.abridge.client import AbridgeAuth, abridge_http_client
from yuxi.modules.extensions.plugins.abridge.options import connection_settings
from yuxi.shared.errors import UpstreamError


async def personal_devices(uid: str) -> dict:
    """通过本人委托读取授权可用设备，登录与服务故障分别表达。"""
    settings = await connection_settings()
    result = {"configured": settings.configured, "management_url": settings.public_origin, "devices": []}
    if not settings.configured:
        return result | {"configured": False, "state": "not_configured"}
    result["management_url"] = settings.public_origin + "/api/auth/oidc/login"
    try:
        async with (
            abridge_http_client(auth=AbridgeAuth(uid, settings), timeout=15) as client,
            streamable_http_client(settings.service_origin + "/mcp", http_client=client) as (read, write, _),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session,
        ):
            await session.initialize()
            response = await session.call_tool("devices_list", {})
        if response.isError:
            raise UpstreamError("设备列表读取失败，请在 aBridge 检查授权", code="abridge_unavailable")
        data = response.structuredContent
        if data is None:
            data = json.loads("".join(item.text for item in response.content if item.type == "text"))
        parsed = DeviceList.model_validate(data)
        return result | {"state": "ready", "devices": [device.model_dump() for device in parsed.devices]}
    except (
        httpx.HTTPError,
        McpError,
        ValidationError,
        json.JSONDecodeError,
        EndOfStream,
        ClosedResourceError,
        BrokenResourceError,
        ExceptionGroup,
    ) as exc:
        if isinstance(exc, ExceptionGroup) and not _remote_failure(exc):
            raise
        if _unauthorized(exc):
            return result | {"state": "login_required"}
        raise UpstreamError("aBridge 暂时不可用，请稍后重试", code="abridge_unavailable") from exc


def _unauthorized(error: BaseException) -> bool:
    """识别 SDK task group 中的远端未开户或认证失效响应。"""
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 401:
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_unauthorized(child) for child in error.exceptions)
    return error.__cause__ is not None and _unauthorized(error.__cause__)


def _remote_failure(error: BaseException) -> bool:
    """只将 SDK 任务组中的远端协议或传输错误翻译为设备服务故障。"""
    if isinstance(error, BaseExceptionGroup):
        return all(_remote_failure(child) for child in error.exceptions)
    return isinstance(error, (httpx.HTTPError, McpError, EndOfStream, ClosedResourceError, BrokenResourceError))


class Device(BaseModel):
    """严格解析设备展示字段，未知字段由边界丢弃。"""

    model_config = ConfigDict(strict=True)

    id: str
    name: str
    kind: Literal["computer", "browser"]
    status: Literal["online", "offline"]
    paused: bool = False
    platform: str | None = None
    last_seen: float | None = None


class DeviceList(BaseModel):
    """设备列表是远端 MCP 响应的唯一消费契约。"""

    devices: list[Device]
