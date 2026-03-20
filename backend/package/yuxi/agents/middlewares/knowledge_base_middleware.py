"""知识库中间件 - 提供通用知识库工具"""

from langchain.agents.middleware import AgentMiddleware

from yuxi.agents.toolkits.kbs import get_common_kb_tools
from yuxi.utils.logging_config import logger


class KnowledgeBaseMiddleware(AgentMiddleware):
    """知识库中间件 - 提供通用知识库工具

    提供 3 个通用工具：
    - list_kbs: 列出用户可访问的知识库
    - get_mindmap: 获取指定知识库的思维导图
    - query_kb: 在指定知识库中检索
    """

    def __init__(self):
        super().__init__()
        # 预加载通用知识库工具
        self.kb_tools = get_common_kb_tools()
        self.tools = self.kb_tools
        logger.debug(f"Initialized KnowledgeBaseMiddleware with {len(self.kb_tools)} tools")
