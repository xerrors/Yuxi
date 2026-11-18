"""知识库运行时装配：构建执行器管理器的进程级单例。"""

import os

from yuxi.infrastructure.runtime_settings import get_runtime_dir
from yuxi.modules.knowledge.manager import KnowledgeBaseManager

knowledge_base = KnowledgeBaseManager(os.path.join(get_runtime_dir(), "knowledge_base_data"))
