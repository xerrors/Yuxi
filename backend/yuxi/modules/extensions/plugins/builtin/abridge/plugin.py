"""aBridge 设备操作插件。"""

from deepagents.middleware._utils import append_to_system_message

from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.extensions.plugins.builtin.abridge.prompt import DEVICE_TOOLS_PROMPT


class AbridgePlugin(YuxiPlugin):
    """按当前用户设备授权提供设备操作与画面展示说明。"""

    plugin_id = "abridge"
    name = "aBridge 设备"
    description = "操作已授权设备的浏览器、桌面和文件，并展示设备画面。"
    entry_skill = "abridge"
    skills = ("abridge", "chat-widgets")
    mcps = ("abridge",)

    async def on_model_call(self, request, handler):
        """为已激活的设备能力补充沙盒边界与操作恢复约束。"""
        request = request.override(system_message=append_to_system_message(request.system_message, DEVICE_TOOLS_PROMPT.strip()))
        return await handler(request)
