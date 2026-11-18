"""知识库类型注册表与实例工厂。"""

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.base import KBNotFoundError, KnowledgeExecutor
from yuxi.modules.knowledge.implementations.dify import DifyKB
from yuxi.modules.knowledge.implementations.feishu import FeishuKB
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.implementations.notion import NotionKB


class KnowledgeBaseFactory:
    """按 kb_type 注册与创建执行器；管理用例可独立读取类型能力。"""

    _kb_types: dict[str, type[KnowledgeExecutor]] = {}

    @classmethod
    def create(cls, kb_type: str, work_dir: str, **kwargs) -> KnowledgeExecutor:
        """创建知识库实例，未知类型抛出 KBNotFoundError。"""
        kb_class = cls.get_kb_class(kb_type)
        try:
            instance = kb_class(work_dir, **kwargs)
            logger.info(f"Created {kb_type} knowledge base instance at {work_dir}")
            return instance
        except Exception as e:
            logger.error(f"Failed to create {kb_type} knowledge base: {e}")
            raise

    @classmethod
    def get_kb_class(cls, kb_type: str) -> type[KnowledgeExecutor]:
        """返回指定类型的知识库类，未知类型抛出 KBNotFoundError。"""
        kb_class = cls._kb_types.get(kb_type)
        if kb_class is None:
            available_types = list(cls._kb_types.keys())
            raise KBNotFoundError(f"Unknown knowledge base type: {kb_type}. Available types: {available_types}")
        return kb_class

    @classmethod
    def is_type_supported(cls, kb_type: str) -> bool:
        """检查是否支持指定的知识库类型。"""
        return kb_type in cls._kb_types

    @classmethod
    def supports_documents(cls, kb_type: str | None) -> bool:
        """判断注册类型是否支持文档管理和全文操作。"""
        normalized_type = (kb_type or "milvus").lower()
        if not cls.is_type_supported(normalized_type):
            return False
        return cls.get_kb_class(normalized_type).supports_documents

    @classmethod
    def get_available_types(cls) -> dict[str, dict]:
        """返回所有可用知识库类型的描述信息。"""
        result = {}
        for kb_type, kb_class in cls._kb_types.items():
            result[kb_type] = {
                "name": kb_class.name,
                "description": kb_class.description,
                "requires_embedding_model": kb_class.requires_embedding_model,
                "supports_documents": kb_class.supports_documents,
                "supports_document_read": kb_class.supports_document_read,
                "create_params": kb_class.get_create_params_config(),
            }
        return result

    @classmethod
    def register(cls, kb_class: type[KnowledgeExecutor]) -> None:
        """注册知识库类型，类型约束不满足时显式失败。"""
        if not issubclass(kb_class, KnowledgeExecutor):
            raise ValueError("Knowledge base class must inherit from KnowledgeExecutor")
        if not kb_class.kb_type:
            raise ValueError("Knowledge base class must define kb_type")
        cls._kb_types[kb_class.kb_type] = kb_class


# 注册顺序也是管理界面的类型展示顺序；注册类不创建连接或运行时单例。
KnowledgeBaseFactory.register(MilvusKB)
KnowledgeBaseFactory.register(FeishuKB)
KnowledgeBaseFactory.register(DifyKB)
KnowledgeBaseFactory.register(NotionKB)
