"""Embedding 响应必须与请求输入逐条对应，条数不符时整批失败。"""

import pytest

from yuxi.modules.models.embed import OtherEmbedding


def _client() -> OtherEmbedding:
    return OtherEmbedding(model="test-model", base_url="http://test.local/embeddings", api_key="test-key", batch_size=40)


def test_extract_embeddings_requires_one_vector_per_input():
    """返回条数少于输入条数时整批拒绝，不能按下标错配。"""
    with pytest.raises(ValueError, match="Embedding response"):
        OtherEmbedding._extract_embeddings(
            {"data": [{"index": 0, "embedding": [0.1]}, {"index": 1, "embedding": [0.2]}]},
            expected=3,
        )


def test_extract_embeddings_rejects_duplicate_index():
    with pytest.raises(ValueError, match="Embedding response"):
        OtherEmbedding._extract_embeddings(
            {"data": [{"index": 0, "embedding": [0.1]}, {"index": 0, "embedding": [0.2]}]},
            expected=2,
        )


def test_extract_embeddings_rejects_out_of_range_index():
    with pytest.raises(ValueError, match="Embedding response"):
        OtherEmbedding._extract_embeddings(
            {"data": [{"index": 0, "embedding": [0.1]}, {"index": 5, "embedding": [0.2]}]},
            expected=2,
        )


def test_extract_embeddings_orders_by_index_when_provider_reorders():
    """提供者乱序返回时按 index 归位，而不是按响应顺序。"""
    embeddings = OtherEmbedding._extract_embeddings(
        {"data": [{"index": 1, "embedding": [0.3]}, {"index": 0, "embedding": [0.4]}]},
        expected=2,
    )

    assert embeddings == [[0.4], [0.3]]


def test_extract_embeddings_accepts_response_without_index():
    """既有的无 index 响应继续按顺序接受。"""
    embeddings = OtherEmbedding._extract_embeddings({"data": [{"embedding": [0.1]}, {"embedding": [0.2]}]})

    assert embeddings == [[0.1], [0.2]]


def test_extract_embeddings_accepts_empty_response_for_empty_input():
    assert OtherEmbedding._extract_embeddings({"data": []}, expected=0) == []


def test_encode_rejects_partial_batch(monkeypatch):
    """客户端边界同样拒绝部分结果，而不是把子集交给调用方。"""
    client = _client()

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"index": 0, "embedding": [0.1]}]}

    monkeypatch.setattr("yuxi.modules.models.embed.requests.post", lambda *a, **k: _Response())

    with pytest.raises(ValueError, match="Embedding response"):
        client.encode(["a", "b"])


async def test_abatch_encode_rejects_batch_with_partial_vectors():
    """逐批累加时，任何一批条数不足都必须整批失败。"""

    class _PartialBatchEmbedding(OtherEmbedding):
        def __init__(self) -> None:
            super().__init__(model="test-model", base_url="http://test.local/embeddings", api_key="test-key", batch_size=2)

        async def aencode(self, message):
            # 每批都少返回一个向量，模拟提供者截断。
            count = len([message] if isinstance(message, str) else message)
            return [[0.1] for _ in range(max(count - 1, 0))]

    client = _PartialBatchEmbedding()

    with pytest.raises(ValueError, match="Embedding response"):
        await client.abatch_encode(["a", "b", "c"])
