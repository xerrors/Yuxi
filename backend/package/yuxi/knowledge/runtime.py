"""知识库运行时单例。"""

import os

from yuxi.config import config
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.knowledge.implementations.dify import DifyKB
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.implementations.notion import NotionKB
from yuxi.knowledge.manager import KnowledgeBaseManager

if os.environ.get("LITE_MODE", "").lower() not in ("true", "1"):
    KnowledgeBaseFactory.register(MilvusKB)
KnowledgeBaseFactory.register(DifyKB)
KnowledgeBaseFactory.register(NotionKB)

knowledge_base = KnowledgeBaseManager(os.path.join(config.save_dir, "knowledge_base_data"))
