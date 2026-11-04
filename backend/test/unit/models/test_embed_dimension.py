"""Embedding 向量维度必须与配置一致，否则整批失败。"""

from unittest.mock import AsyncMock

import httpx
import pytest

from yuxi.modules.models.embed import OtherEmbedding


@pytest.fixture
def client():
    """配置了 4 维的测试供应商。"""
    return OtherEmbedding(
        model="test-model",
        base_url="http://test.local/embeddings",
        api_key="test-key",
        dimension=4,
        batch_size=2,
    )


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
        ([{"embedding": [0.1, 0.2]}], "dimension"),
        ([{"embedding": [0.1]}], "dimension"),
        ([{"embedding": [0.1, 0.2, 0.3, 0.4, 0.5]}], "dimension"),
        (
            [{"embedding": [0.1, 0.2, 0.3, 0.4]}, {"embedding": [0.5, 0.6]}],
            "dimension",
        ),
    ],
    ids=["short", "much-short", "long", "second-item-wrong"],
)
async def test_direct_encode_rejects_wrong_dimension(client, response_data, method, items, error):
    """直接编码路径同样拒绝维度不符的向量，不只依赖连接探针。"""
    response_data.extend(items)

    with pytest.raises(ValueError, match=error):
        if method == "encode":
            client.encode("a" if len(items) == 1 else ["a", "b"])
        else:
            await client.aencode("a" if len(items) == 1 else ["a", "b"])


def test_extract_embeddings_rejects_wrong_dimension():
    with pytest.raises(ValueError, match="dimension"):
        OtherEmbedding._extract_embeddings(
            {"data": [{"embedding": [0.1, 0.2]}]},
            expected=1,
            dimension=4,
        )


def test_extract_embeddings_accepts_matching_dimension():
    vectors = OtherEmbedding._extract_embeddings(
        {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}, {"embedding": [0.5, 0.6, 0.7, 0.8]}]},
        expected=2,
        dimension=4,
    )

    assert vectors == [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]


def test_extract_embeddings_skips_dimension_check_when_unconfigured():
    """未配置维度时保持既有行为。"""
    vectors = OtherEmbedding._extract_embeddings(
        {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3]}]},
        expected=2,
        dimension=None,
    )

    assert vectors == [[0.1, 0.2], [0.3]]


@pytest.mark.parametrize("method", ["batch_encode", "abatch_encode"])
async def test_batch_encode_rejects_wrong_dimension_in_later_batch(client, monkeypatch, method):
    """首批正常，后续批次维度不符也必须整批失败。"""
    batches = iter(
        [
            [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
            [[0.9]],
        ]
    )
    monkeypatch.setattr(client, "encode", lambda message: next(batches))
    monkeypatch.setattr(client, "aencode", AsyncMock(side_effect=lambda message: next(batches)))

    with pytest.raises(ValueError, match="dimension"):
        if method == "batch_encode":
            client.batch_encode(["a", "b", "c"])
        else:
            await client.abatch_encode(["a", "b", "c"])


@pytest.mark.parametrize("dimension", ["", "2"])
async def test_string_dimension_configuration(response_data, dimension):
    client = OtherEmbedding(model="test-model", base_url="http://test.local/embeddings", dimension=dimension)
    response_data.append({"embedding": [0.1, 0.2]})

    assert client.encode("a") == [[0.1, 0.2]]
    assert await client.aencode("a") == [[0.1, 0.2]]
    assert client.batch_encode(["a"]) == [[0.1, 0.2]]
    assert await client.abatch_encode(["a"]) == [[0.1, 0.2]]
    assert (await client.test_connection())[0]

    if dimension:
        response_data[0]["embedding"] = [0.1]
        with pytest.raises(ValueError, match="dimension"):
            client.encode("a")
