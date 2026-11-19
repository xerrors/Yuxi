from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig

EMBEDDING_MODEL_SPEC = "test-provider:test-embedding"


def make_query_config() -> KnowledgeBaseConfig:
    """创建检索测试所用的固定知识库配置。"""
    return KnowledgeBaseConfig(
        kb_id="db",
        kb_type="milvus",
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
    )


class FakeHit:
    """保存检索断言需要的最小命中结果。"""

    def __init__(self, content: str, distance: float, file_id: str = "file-1"):
        """构造带固定 chunk 标识的结果。"""
        self.distance = distance
        self.entity = {
            "content": content,
            "chunk_id": "chunk-1",
            "file_id": file_id,
            "chunk_index": 0,
        }


class FakeCollection:
    """记录 Milvus 调用并返回稳定的检索结果。"""

    def __init__(self, distance: float = 0.8):
        """初始化可检索、插入和更新调用记录。"""
        self.search_calls = []
        self.hybrid_calls = []
        self.insert_calls = []
        self.upsert_calls = []
        self.distance = distance

    def search(self, **kwargs):
        """记录单通道查询并返回一个结果。"""
        self.search_calls.append(kwargs)
        return [[FakeHit("BM25 result", self.distance)]]

    def hybrid_search(self, **kwargs):
        """记录混合查询并返回一个结果。"""
        self.hybrid_calls.append(kwargs)
        return [[FakeHit("Hybrid result", self.distance)]]

    def insert(self, entities):
        """记录插入的向量实体。"""
        self.insert_calls.append(entities)

    def upsert(self, entities):
        """记录幂等写入的向量实体。"""
        self.upsert_calls.append(entities)


def make_kb(collection: FakeCollection) -> MilvusKB:
    """构造使用指定假集合的轻量 Milvus 知识库。"""
    kb = MilvusKB.__new__(MilvusKB)
    from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

    kb.graph_service = KnowledgeGraphService(graph_vector_store=SimpleNamespace())

    async def get_collection(kb_id: str, embedding_model_spec: str | None):
        """返回当前测试固定的集合。"""
        del kb_id, embedding_model_spec
        return collection

    async def hydrate_chunk_sources(kb_id: str, chunks: list[dict]) -> list[dict]:
        """为检索结果填入稳定来源字段。"""
        for chunk in chunks:
            chunk["metadata"]["source"] = "demo.md"
        return chunks

    async def retrieval_scope(kb_id, metadata_filter=None, file_name=None, *, file_ids=None):
        """检索通道单测使用固定 PG 可见范围；真实范围由 integration 验证。"""
        if file_ids is not None and "file-1" not in file_ids:
            return "file_id in []", []
        return 'generation == 1 and file_id in ["file-1"]', [("file-1", 1)]

    kb._get_or_create_milvus_collection = get_collection
    kb._get_existing_milvus_collection = AsyncMock(return_value=collection)
    kb._hydrate_chunk_sources = hydrate_chunk_sources
    kb._build_retrieval_scope = retrieval_scope
    return kb


@pytest.fixture(autouse=True)
def fake_milvus_embedding(monkeypatch):
    """检索单测注入固定编码器；真实模型协议由 E2E 覆盖。"""
    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.milvus.get_embedding_function",
        lambda spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts],
    )
