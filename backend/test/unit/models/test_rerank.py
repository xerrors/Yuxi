"""重排响应与输入文档的对应关系，以及既有批次降级契约。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.modules.models.rerank import DashscopeReranker, OpenAIReranker, sigmoid


@pytest.fixture(params=[OpenAIReranker, DashscopeReranker])
async def reranker(request):
    """两种提供者使用真实解析器和隔离的 HTTP 响应。"""
    client = request.param(model_name="test-model", api_key="test-key", base_url="http://test.local/rerank")
    client.session = MagicMock(closed=False, close=AsyncMock())
    yield client
    await client.aclose()


def _respond_with(client, *batches):
    """按请求顺序设置提供者原始响应，不替换被测解析逻辑。"""
    contexts = []
    for results in batches:
        payload = {"results": results}
        if isinstance(client, DashscopeReranker):
            payload = {"output": payload}
        response = MagicMock(json=AsyncMock(return_value=payload))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        contexts.append(context)
    client.session.post.side_effect = contexts


async def test_complete_response_preserves_document_order_and_numeric_strings(reranker):
    _respond_with(
        reranker,
        [
            {"index": 2, "relevance_score": "0.9"},
            {"index": 0, "relevance_score": -2},
            {"index": 1, "relevance_score": 0.4},
        ],
    )

    scores = await reranker._batch_rerank("query", ["A", "B", "C"], max_length=128)

    assert scores == [-2.0, 0.4, 0.9]
    payload = reranker.session.post.call_args.kwargs["json"]
    if isinstance(reranker, DashscopeReranker):
        assert payload["input"]["documents"] == ["A", "B", "C"]
        assert payload["parameters"]["top_n"] == 3
    else:
        assert payload["documents"] == ["A", "B", "C"]
        assert "max_chunks_per_doc" not in payload


@pytest.mark.parametrize(
    "results",
    [
        [],
        [{"index": 1, "relevance_score": 0.9}],
        [{"index": i, "relevance_score": 0.9} for i in range(3)],
        [{"index": 0, "relevance_score": 0.1}, {"index": 0, "relevance_score": 0.9}],
    ],
    ids=["empty", "missing", "extra", "duplicate"],
)
async def test_incomplete_or_duplicate_response_is_rejected(reranker, results):
    _respond_with(reranker, results)

    with pytest.raises(ValueError, match="Rerank response"):
        await reranker._batch_rerank("query", ["A", "B"], max_length=128)


@pytest.mark.parametrize("index", [-1, 2, True, False, "0", 0.0, None])
async def test_invalid_index_is_rejected(reranker, index):
    _respond_with(reranker, [{"index": index, "relevance_score": 0.9}])

    with pytest.raises(ValueError, match="Rerank response"):
        await reranker._batch_rerank("query", ["A"], max_length=128)


@pytest.mark.parametrize("entry", [None, "not-an-object", {}, {"relevance_score": 0.9}, {"index": 0}])
async def test_invalid_entry_or_missing_field_is_rejected(reranker, entry):
    _respond_with(reranker, [entry])

    with pytest.raises(ValueError, match="Rerank response"):
        await reranker._batch_rerank("query", ["A"], max_length=128)


@pytest.mark.parametrize("score", [None, True, False, "invalid", float("nan"), float("inf"), -float("inf"), "NaN", "inf"])
async def test_invalid_score_is_rejected(reranker, score):
    _respond_with(reranker, [{"index": 0, "relevance_score": score}])

    with pytest.raises(ValueError, match="Rerank response"):
        await reranker._batch_rerank("query", ["A"], max_length=128)


@pytest.mark.parametrize("normalize", [False, True])
async def test_bad_middle_batch_keeps_fallback_slots_and_later_document_scores(reranker, normalize):
    _respond_with(
        reranker,
        [{"index": 1, "relevance_score": 0.2}, {"index": 0, "relevance_score": -4}],
        [{"index": 1, "relevance_score": 0.9}],
        [{"index": 0, "relevance_score": 0.7}],
    )

    scores = await reranker.acompute_score(["query", ["A", "B", "C", "D", "E"]], batch_size=2, normalize=normalize)

    raw = [-4.0, 0.2, float("-inf"), float("-inf"), 0.7]
    if normalize:
        assert scores[0] == pytest.approx(float(sigmoid(-4.0)))
        assert scores[1] == pytest.approx(float(sigmoid(0.2)))
        assert scores[4] == pytest.approx(float(sigmoid(0.7)))
        assert scores[2] == pytest.approx(0.0)
        assert scores[3] == pytest.approx(0.0)
    else:
        assert scores == raw
    assert len(scores) == 5
    assert scores[2] < scores[0] and scores[2] < scores[1] and scores[2] < scores[4]
    assert scores[3] < scores[0] and scores[3] < scores[1] and scores[3] < scores[4]


async def test_connection_reports_invalid_response(reranker):
    _respond_with(reranker, [{"index": 7, "relevance_score": 0.9}])

    success, message = await reranker.test_connection()

    assert success is False
    assert "Rerank response" in message


async def test_connection_reports_transport_failure_without_fallback(reranker):
    """连接探针不能把默认分数当成远端可用。"""
    reranker.session.post.side_effect = ConnectionError("provider unavailable")

    success, message = await reranker.test_connection()

    assert success is False
    assert "provider unavailable" in message


async def test_default_request_does_not_send_length_as_chunk_limit(reranker):
    """默认长度上限不是每文档块数，请求里不能出现 512。"""
    _respond_with(reranker, [{"index": 0, "relevance_score": 0.2}])

    await reranker.acompute_score(["query", ["doc"]])

    payload = reranker.session.post.call_args.kwargs["json"]
    assert "max_chunks_per_doc" not in payload
    if isinstance(reranker, DashscopeReranker):
        assert "max_chunks_per_doc" not in payload["parameters"]


async def test_empty_input_does_not_request_provider(reranker):
    assert await reranker._batch_rerank("query", [], max_length=128) == []
    assert await reranker.acompute_score(["query", []]) == []
    reranker.session.post.assert_not_called()
