"""镜像内 aBridge 客户端的固定命令和私有交接协议。"""

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, Field

from yuxi.modules.extensions.plugins.abridge.browser_enrollment import BrowserBinding
from yuxi.modules.extensions.plugins.abridge.options import AbridgeConnection
from yuxi.shared.errors import StateConflictError, UpstreamError

CLIENT = "/opt/yuxi-managed-browser/abridge-client-browser"


class BrowserGuestStatus(BaseModel):
    """客户端回读的本地绑定及扩展连接状态。"""

    status: Literal["unbound", "bound", "running"]
    browser_origin: str | None = None
    device_id: str | None = Field(default=None, min_length=1)
    revision: int | None = Field(default=None, strict=True, ge=1)
    connected: bool = Field(default=False, strict=True)

    def matches(self, binding: BrowserBinding, settings: AbridgeConnection) -> bool:
        """核对客户端绑定的来源、设备及授权版本。"""
        return self.status != "unbound" and (self.browser_origin, self.device_id, self.revision) == (
            settings.public_origin,
            binding.device_id,
            binding.revision,
        )

    def ready(self, binding: BrowserBinding, settings: AbridgeConnection) -> bool:
        """同一登记同时获得本地握手确认与远端在线状态。"""
        return self.status == "running" and self.connected and binding.status == "online" and self.matches(binding, settings)


async def require_browser_client(provider, uid: str, generation: int) -> None:
    """登记前检查客户端安装标记，为普通旧镜像给出升级提示。"""
    code, _ = await asyncio.to_thread(
        provider.run_guest_program,
        uid,
        generation,
        user="user",
        command="/usr/bin/test",
        arguments=["-r", "/opt/yuxi-managed-browser/service-v2"],
        timeout=5,
    )
    if code == 1:
        raise StateConflictError("运行环境需要升级浏览器客户端", code="sandbox_service_unsupported")
    if code:
        raise UpstreamError("无法确认运行环境的浏览器客户端", code="sandbox_service_unavailable")


async def browser_status(provider, uid: str, generation: int) -> BrowserGuestStatus:
    """回读已安装客户端的本地绑定和连接状态。"""
    return await _client(provider, uid, generation, "status")


async def start_browser(
    provider, uid: str, generation: int, settings: AbridgeConnection, binding: BrowserBinding, *, token: str | None
) -> None:
    """经专用账号的 stdin 启动先前确认过的客户端，不向普通命令暴露票据。"""
    handoff = {"browser_origin": settings.public_origin, "device_id": binding.device_id, "revision": binding.revision}
    if token:
        handoff["bootstrap_token"] = token
    if settings.public_origin.startswith("http://"):
        handoff["http_confirmed_origin"] = settings.public_origin
    await _client(provider, uid, generation, "start", json.dumps(handoff).encode() + b"\n")


async def _client(provider, uid: str, generation: int, action: Literal["start", "status"], data: bytes | None = None) -> BrowserGuestStatus:
    """固定入口和工作目录由适配器拥有，退出码和回执必须同时有效。"""
    code, output = await asyncio.to_thread(
        provider.run_guest_program,
        uid,
        generation,
        user="managed-browser",
        command="/usr/local/bin/python3",
        arguments=["-I", CLIENT, action],
        timeout=45 if action == "start" else 10,
        stdin_data=data,
        touch_idle=action == "start",
    )
    try:
        if code == 0:
            status = BrowserGuestStatus.model_validate_json(output)
            if status.status == "unbound" or (status.browser_origin and status.device_id and status.revision is not None):
                return status
    except ValueError:
        pass
    raise UpstreamError("浏览器客户端未确认操作结果", code="sandbox_service_unavailable")
