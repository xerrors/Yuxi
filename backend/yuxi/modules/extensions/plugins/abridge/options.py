"""aBridge 地址配置与部署就绪判据。"""

import os
from dataclasses import dataclass, field

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.system.services.options import Option, normalize_option_value
from yuxi.shared.errors import StateConflictError

abridge_options = Option(
    key="abridge_options",
    name="设备管理",
    description="aBridge 实例连接；个人设备权限仍由 aBridge 管理。",
    params={
        "fields": [
            {
                "key": "url",
                "label": "服务 URL",
                "type": "origin",
                "environment": "ABRIDGE_URL",
                "default": "",
                "help": "留空读取 ABRIDGE_URL。请填写不带账号、路径、查询和片段的 HTTP(S) 服务根地址。全局管理请进入 aBridge。",
            },
            {
                "key": "allow_shadow_managed_browser",
                "label": "允许 APP 终端用户使用托管浏览器",
                "type": "boolean",
                "default": False,
                "help": "允许 APP 终端用户通过 Agents Public API 启动 Cube 浏览器并通过 aBridge 插件使用它。",
            },
        ],
    },
)


@dataclass(frozen=True)
class AbridgeConnection:
    """个人设备与 MCP 共享的服务地址，不携带管理员凭据。"""

    service_origin: str
    public_origin: str
    provider_enabled: bool
    # 开关只影响托管委托；人类用户的地址快照不因此失效。
    allow_shadow_managed_browser: bool = field(default=False, compare=False)

    @property
    def configured(self) -> bool:
        """设备与 MCP 入口共享同一个部署就绪条件。"""
        return self.provider_enabled and bool(self.service_origin and self.public_origin)


async def connection_settings(db=None) -> AbridgeConnection:
    """读取当前地址覆盖值，公开来源由部署配置固定。"""
    # 每次出站复核数据库事实，不让配置缓存延长旧地址的有效期。
    if db is None:
        async with pg_manager.get_async_session_context() as session:
            options = await abridge_options.get(session)
    else:
        options = await abridge_options.get(db)
    try:
        url = normalize_option_value(abridge_options.fields[0], options.get("url"))
        public_origin = normalize_option_value({"key": "ABRIDGE_PUBLIC_ORIGIN", "type": "origin"}, os.getenv("ABRIDGE_PUBLIC_ORIGIN", ""))
    except ValueError as exc:
        raise StateConflictError("aBridge 地址配置无效，请检查服务和公开根地址", code="abridge_invalid_configuration") from exc
    return AbridgeConnection(
        service_origin=url,
        public_origin=public_origin,
        provider_enabled=os.getenv("YUXI_OIDC_PROVIDER_ENABLED", "false").lower() == "true",
        allow_shadow_managed_browser=options["allow_shadow_managed_browser"],
    )
