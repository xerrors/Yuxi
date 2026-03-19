import os

from ..config import config
from .factory import KnowledgeBaseFactory
from .implementations.dify import DifyKB
from .manager import KnowledgeBaseManager

_LITE_MODE = os.environ.get("LITE_MODE", "").lower() in ("true", "1")

if not _LITE_MODE:
    from .graphs.upload_graph_service import UploadGraphService
    from .implementations.lightrag import LightRagKB
    from .implementations.milvus import MilvusKB

    # 注册知识库类型
    KnowledgeBaseFactory.register("milvus", MilvusKB, {"description": "基于 Milvus 的生产级向量知识库，适合高性能部署"})
    KnowledgeBaseFactory.register("lightrag", LightRagKB, {"description": "基于图的知识库，支持实体关系构建和复杂查询"})

KnowledgeBaseFactory.register("dify", DifyKB, {"description": "连接 Dify Dataset 的只读检索知识库"})

# 创建知识库管理器
work_dir = os.path.join(config.save_dir, "knowledge_base_data")
knowledge_base = KnowledgeBaseManager(work_dir)

# 创建图数据库实例
if _LITE_MODE:
    from ..utils import logger

    class _LiteGraphStub:
        """Lite 模式下的图数据库占位实例，所有操作报告为不可用"""

        def is_running(self):
            return False

        def get_graph_info(self, *args, **kwargs):
            return None

    graph_base = _LiteGraphStub()
    # 向后兼容
    GraphDatabase = _LiteGraphStub
    logger.info("LITE_MODE enabled, knowledge graph services disabled")
else:
    graph_base = UploadGraphService()
    # 向后兼容：让 GraphDatabase 指向 UploadGraphService
    GraphDatabase = UploadGraphService

__all__ = ["GraphDatabase", "knowledge_base", "graph_base"]
