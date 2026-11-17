"""内置插件声明。"""

from yuxi.modules.extensions.plugins.base import YuxiPlugin


class KnowledgePlugin(YuxiPlugin):
    """按当前用户连接提供知识库查询。"""

    plugin_id = "knowledge-base"
    name = "知识库"
    description = "检索当前会话选择的知识库，并引用原始资料。"
    entry_skill = "knowledge-base"
    skills = ("knowledge-base",)
    mcps = ("knowledge",)
