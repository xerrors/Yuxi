"""Embedding 响应必须与请求输入逐条对应，非法响应整批失败。"""

from unittest.mock import AsyncMock

import httpx
import pytest

from yuxi.modules.models.embed import OtherEmbedding


@pytest.fixture
def client():
    """使用固定的测试供应商配置。"""
    return OtherEmbedding(model="test-model", base_url="http://test.local/embeddings", api_key="test-key", batch_size=2)


@pytest.fixture
def response_data(monkeypatch):
    """在 HTTP 边界为同步和异步客户端提供相同响应。"""
    data = []
    response = httpx.Response(200, request=httpx.Request("POST", "http://test.local/embeddings"))
    monkeypatch.setattr(response, "json", lambda: {"data": data})
    monkeypatch.setattr("yuxi.modules.models.embed.requests.post", lambda *args, **kwargs: response)
    monkeypatch.setattr("yuxi.modules.models.embed.httpx.AsyncClient.post", AsyncMock(return_value=response))
    return data


@pytest.mark.parametrize("method", ["encode", "aencode"])
@pytest.mark.parametrize(
    ("items", "error"),
    [
        ([{"embedding": [0.1]}], "returned 1 vectors for 2 inputs"),
        ([{"embedding": [0.1]}, {"embedding": [0.2]}, {"embedding": [0.3]}], "returned 3 vectors for 2 inputs"),
        ([{"index": 0, "embedding": None}, {"index": 1, "embedding": [0.2]}], "invalid item"),
        ([{"embedding": None}, {"embedding": [0.2]}], "invalid item"),
        ([{"index": 0, "embedding": None}, {"index": 0, "embedding": [0.2]}], "invalid item"),
        ([{"index": 0, "embedding": [0.1]}, {"index": 0, "embedding": [0.2]}], "duplicate index"),
        ([{"index": 0, "embedding": [0.1]}, {"embedding": [0.2]}], "mixes indexed and unindexed"),
        ([{"index": 0, "embedding": [0.1]}, {}], "invalid item"),
    ],
    ids=["short", "extra", "indexed-null", "unindexed-null", "null-and-duplicate", "duplicate", "mixed", "missing-vector"],
)
async def test_direct_encode_rejects_invalid_response(client, response_data, method, items, error):
    """直接编码也必须拒绝数量错误、空向量和无效索引，避免语义切分丢句。"""
    response_data.extend(items)

    with pytest.raises(ValueError, match=error):
        if method == "encode":
            client.encode(["a", "b"])
        else:
            await client.aencode(["a", "b"])


@pytest.mark.parametrize(
    ("data", "error"),
    [({}, "data must be a list"), (None, "data must be a list"), ([None], "invalid item")],
)
def test_extract_embeddings_rejects_malformed_data(data, error):
    """响应容器和条目必须符合供应商协议形态。"""
    with pytest.raises(ValueError, match=error):
        OtherEmbedding._extract_embeddings({"data": data})


@pytest.mark.parametrize("index", [-1, 2, True, 0.5, "1"])
def test_extract_embeddings_rejects_invalid_index(index):
    """索引必须是响应范围内的整数，布尔值不能充当索引。"""
    with pytest.raises(ValueError, match="out-of-range index"):
        OtherEmbedding._extract_embeddings({"data": [{"index": 0, "embedding": [0.1]}, {"index": index, "embedding": [0.2]}]}, expected=2)


@pytest.mark.parametrize("method", ["encode", "aencode"])
@pytest.mark.parametrize(
    ("message", "items", "expected"),
    [
        (["a", "b"], [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}], [[0.1], [0.2]]),
        (["a", "b"], [{"embedding": [0.1]}, {"embedding": [0.2]}], [[0.1], [0.2]]),
        (["a", "b"], [{"index": None, "embedding": [0.1]}, {"embedding": [0.2]}], [[0.1], [0.2]]),
        ("hello", [{"index": 0, "embedding": [0.1]}], [[0.1]]),
        ([], [], []),
    ],
    ids=["reordered", "unindexed", "null-index", "string", "empty"],
)
async def test_direct_encode_preserves_alignment(client, response_data, method, message, items, expected):
    """同步、异步入口保持归位结果及无索引、单条和空输入兼容。"""
    response_data.extend(items)

    if method == "encode":
        actual = client.encode(message)
    else:
        actual = await client.aencode(message)

    assert actual == expected


@pytest.mark.parametrize("method", ["batch_encode", "abatch_encode"])
async def test_batch_encode_rejects_partial_later_batch(client, monkeypatch, method):
    """首批成功后，后续批次缺项仍须整批抛错。"""
    batches = iter([[[0.1], [0.2]], []])
    monkeypatch.setattr(client, "encode", lambda message: next(batches))
    monkeypatch.setattr(client, "aencode", AsyncMock(side_effect=lambda message: next(batches)))

    with pytest.raises(ValueError, match="returned 0 vectors for 1 inputs"):
        if method == "batch_encode":
            client.batch_encode(["a", "b", "c"])
        else:
            await client.abatch_encode(["a", "b", "c"])
