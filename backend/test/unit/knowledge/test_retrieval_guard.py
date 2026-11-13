"""检索入口覆盖参数的拒绝与收窄。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.knowledge.implementations import milvus as milvus_module
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.repositories.files import MAX_FILTER_FILE_IDS
from yuxi.modules.knowledge.services.retrieval import normalize_query_overrides
from yuxi.shared.errors import InvalidInputError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def rerank_catalog_without_redis(monkeypatch):
    """查询参数声明会读取重排序目录。单元环境没有 Redis。"""

    monkeypatch.setattr(milvus_module.model_cache, "get_all_specs", lambda _kind=None: [])


def test_query_override_rejects_unknown_keys_and_oversized_top_k():
    """调用方不能注入表达式，也不能用超出声明的 top_k 扩大召回。"""

    with pytest.raises(InvalidInputError, match="不支持的检索参数"):
        normalize_query_overrides(MilvusKB, {"expr": 'file_id == "other"'})
    with pytest.raises(InvalidInputError, match="超出允许范围"):
        normalize_query_overrides(MilvusKB, {"final_top_k": MAX_FILTER_FILE_IDS})


def test_query_override_keeps_declared_values_and_caps_file_ids():
    guarded = normalize_query_overrides(
        MilvusKB,
        {"final_top_k": 10, "file_name": " 报告 ", "file_ids": ["file-1", "file-1"]},
    )

    assert guarded == {"final_top_k": 10, "file_name": "报告", "file_ids": ["file-1"]}
    with pytest.raises(InvalidInputError, match="file_ids"):
        normalize_query_overrides(MilvusKB, {"file_ids": ["file-1"] * (MAX_FILTER_FILE_IDS + 1)})


@pytest.mark.asyncio
async def test_query_route_rejects_override_and_foreign_file_ids(monkeypatch):
    from yuxi.api.routers.knowledge import management
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
    from yuxi.modules.knowledge.runtime import knowledge_base

    calls = []

    async def get_kb_config(_kb_id):
        return SimpleNamespace(kb_type="milvus", query_options={})

    async def retrieve(kb_id, _query, **kwargs):
        """提供生产入口的标准片段，旁路调用不能满足断言。"""
        calls.append((kb_id, kwargs))
        metadata = {"score": 0, "score_type": "cosine", "highlights": [[{"text": "正文", "matched": True}]]}
        chunk = {"id": "chunk-1", "kb_id": kb_id, "file_id": "file-1", "content": "正文", "metadata": metadata}
        return {"kb_id": kb_id, "results": [chunk]}

    async def list_live_file_ids(self, *, kb_id, file_ids):
        del self, kb_id
        return [file_id for file_id in file_ids if file_id == "file-1"]

    monkeypatch.setattr(knowledge_base, "get_kb_config", get_kb_config)
    monkeypatch.setattr(knowledge_base, "retrieve", retrieve)
    monkeypatch.setattr(KnowledgeFileRepository, "list_live_file_ids", list_live_file_ids)
    user = SimpleNamespace(uid="reader")

    with pytest.raises(InvalidInputError, match="超出允许范围"):
        await management.query_test("kb-1", query="问题", meta={"final_top_k": 1000}, current_user=user)
    with pytest.raises(InvalidInputError, match="不属于该知识库"):
        await management.query_knowledge_base(
            "kb-1",
            query="问题",
            meta={"file_ids": ["file-other"]},
            current_user=user,
        )

    assert calls == []
    result = await management.query_test("kb-1", query="问题", meta={"file_ids": ["file-1"], "final_top_k": 4}, current_user=user)
    highlights = [[{"text": "正文", "matched": True}]]
    assert result == [
        {
            "id": "chunk-1",
            "kb_id": "kb-1",
            "file_id": "file-1",
            "content": "正文",
            "metadata": {"score": 0, "score_type": "cosine", "highlights": highlights},
            "score": 0,
            "score_type": "cosine",
            "highlights": highlights,
        }
    ]
    assert calls == [("kb-1", {"file_ids": ["file-1"], "final_top_k": 4})]
