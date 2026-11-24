"""aBridge 设备操作插件。"""

from deepagents.middleware._utils import append_to_system_message

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.mcp.config import BuiltinMCP
from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.extensions.plugins.builtin.abridge.prompt import DEVICE_TOOLS_PROMPT
from yuxi.shared.errors import StateConflictError


async def _available(db) -> bool:
    """当前实例须启用设备委托并拥有有效服务地址。"""
    from yuxi.modules.extensions.abridge.options import connection_settings

    try:
        return (await connection_settings(db)).configured
    except StateConflictError as exc:
        if exc.code != "abridge_invalid_configuration":
            raise
        logger.warning("component=abridge available=false reason=abridge_invalid_configuration")
        return False


async def _delegation(*, uid, context, management, before_request) -> dict:
    """按当前用户与设备配置装配出站委托。"""
    from yuxi.modules.extensions.abridge.client import abridge_mcp_connection

    return await abridge_mcp_connection(uid=uid, before_request=before_request)


class AbridgePlugin(YuxiPlugin):
    """按当前用户设备授权提供设备操作与画面展示说明。"""

    plugin_id = "abridge"
    name = "aBridge 设备"
    description = "操作已授权设备的浏览器、桌面和文件，并展示设备画面。"
    entry_skill = "abridge"
    skills = ("abridge", "chat-widgets")
    mcps = (
        BuiltinMCP(
            slug="abridge",
            manifest={
                "type": "http",
                "url": "http://abridge:8765/mcp",
                "extra_data": {"name": "aBridge", "description": "使用当前用户授权的电脑和浏览器设备。"},
            },
            delegation=_delegation,
            available=_available,
        ),
    )

    async def on_model_call(self, request, handler):
        """为已激活的设备能力补充沙盒边界与操作恢复约束。"""
        request = request.override(system_message=append_to_system_message(request.system_message, DEVICE_TOOLS_PROMPT.strip()))
        return await handler(request)
