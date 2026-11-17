from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.implementations.dify import DifyKB
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.services import directories as knowledge_directories


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response_payload: dict | None = None, raises: Exception | None = None, **kwargs):
        del kwargs
        self._response_payload = response_payload or {}
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def post(self, url: str, json: dict, headers: dict):
        assert "/datasets/" in url
        assert headers.get("Authorization", "").startswith("Bearer ")
        if self._raises:
            raise self._raises
        assert json["retrieval_model"]["search_method"] == "semantic_search"
        assert json["retrieval_model"]["top_k"] == 5
        assert json["retrieval_model"]["reranking_enable"] is False
        assert json["retrieval_model"]["score_threshold_enabled"] is True
        assert json["retrieval_model"]["score_threshold"] == 0.3
        return _FakeResponse(self._response_payload)


@pytest.mark.asyncio
async def test_dify_rejects_folder_rename(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_by_kb_id",
        AsyncMock(return_value=SimpleNamespace(kb_type="dify", name="Dify"))
    )
    with pytest.raises(ValueError, match="只支持检索"):
        await knowledge_directories.rename_folder("kb-1", "folder-1", "renamed")


def test_dify_create_params_config_and_validation():
    config = DifyKB.get_create_params_config()
    keys = [option["key"] for option in config["options"]]
    assert keys == ["dify_api_url", "dify_token", "dify_dataset_id"]
    assert all(option["required"] for option in config["options"])

    params = DifyKB.normalize_additional_params(
        {
            "dify_api_url": " https://api.dify.ai/v1 ",
            "dify_token": " token ",
            "dify_dataset_id": " dataset-123 ",
        }
    )
    assert params == {
        "dify_api_url": "https://api.dify.ai/v1",
        "dify_token": "token",
        "dify_dataset_id": "dataset-123",
    }
    assert "chunk_preset_id" not in params


def test_dify_validation_rejects_missing_or_invalid_params():
    with pytest.raises(ValueError, match="Dify 参数缺失"):
        DifyKB.normalize_additional_params({"dify_api_url": "https://api.dify.ai/v1"})

    with pytest.raises(ValueError, match="必须以 /v1 结尾"):
        DifyKB.normalize_additional_params(
            {
                "dify_api_url": "https://api.dify.ai",
                "dify_token": "token",
                "dify_dataset_id": "dataset-123",
            }
        )


@pytest.mark.asyncio
async def test_dify_kb_aquery_maps_records(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    slug = "kb_test_dify"
    query_options = {
        "search_mode": "vector",
        "final_top_k": 5,
        "score_threshold_enabled": True,
        "similarity_threshold": 0.3,
    }
    additional_params = {
        "dify_api_url": "https://api.dify.ai/v1",
        "dify_token": "token",
        "dify_dataset_id": "dataset-123",
    }
    config = KnowledgeBaseConfig(
        kb_id=slug,
        kb_type="dify",
        query_params={"options": query_options},
        additional_params=additional_params,
    )

    payload = {
        "records": [
            {
                "score": 0.98,
                "segment": {
                    "id": "seg-1",
                    "position": 2,
                    "content": "hello world",
                    "document": {"id": "doc-1", "name": "Doc One"},
                },
            }
        ]
    }

    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response_payload=payload, **kwargs),
    )

    result = await kb.aquery(
        "hello",
        slug,
        config=config,
    )
    assert len(result) == 1
    assert result[0]["content"] == "hello world"
    assert result[0]["score"] == 0.98
    assert result[0]["metadata"]["source"] == "Doc One"
    assert result[0]["metadata"]["file_id"] == "doc-1"
    assert result[0]["metadata"]["chunk_id"] == "seg-1"
    assert result[0]["metadata"]["chunk_index"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_dify_kb_aquery_caps_top_k(tmp_path, fallback):
    """正常请求与兼容重试均限制最终返回数量。"""
    kb = DifyKB(str(tmp_path))
    config = KnowledgeBaseConfig(
        kb_id="kb_dify_limit",
        kb_type="dify",
        query_params={"options": {"final_top_k": 1_000_000}},
        additional_params={
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "token",
            "dify_dataset_id": "dataset-123",
        },
    )
    response = {"records": [{"segment": {"id": str(index), "content": f"chunk {index}"}} for index in range(150)]}
    request = AsyncMock(side_effect=[RuntimeError("unsupported retrieval model"), response] if fallback else [response])
    kb._request_dify = request

    results = await kb.aquery("hello", config.kb_id, config=config)

    payload = request.await_args_list[0].kwargs["client_payload"]
    assert payload["retrieval_model"]["top_k"] == 100
    assert [result["content"] for result in results] == [f"chunk {index}" for index in range(100)]
    if fallback:
        assert request.await_args.kwargs["client_payload"] == {"query": "hello"}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["dify_api_url", "dify_token", "dify_dataset_id"])
async def test_dify_kb_aquery_rejects_incomplete_persisted_config(tmp_path, missing_field):
    """运行时拒绝不完整的持久化连接配置，并避免远端请求。"""
    params = {
        "dify_api_url": "https://api.dify.ai/v1",
        "dify_token": "token",
        "dify_dataset_id": "dataset-123",
    }
    del params[missing_field]
    config = KnowledgeBaseConfig(kb_id="kb_dify_missing", kb_type="dify", additional_params=params)
    kb = DifyKB(str(tmp_path))
    kb._request_dify = AsyncMock()

    with pytest.raises(ValueError, match="Dify config incomplete"):
        await kb.aquery("hello", config.kb_id, config=config)

    kb._request_dify.assert_not_awaited()


@pytest.mark.asyncio
async def test_dify_kb_aquery_error_is_explicit(monkeypatch, tmp_path):
    kb = DifyKB(str(tmp_path))
    slug = "kb_test_dify_error"
    additional_params = {
        "dify_api_url": "https://api.dify.ai/v1",
        "dify_token": "token",
        "dify_dataset_id": "dataset-123",
    }
    config = KnowledgeBaseConfig(
        kb_id=slug,
        kb_type="dify",
        query_params={"options": {}},
        additional_params=additional_params,
    )

    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(raises=RuntimeError("boom"), **kwargs),
    )

    with pytest.raises(RuntimeError, match="Dify query failed"):
        await kb.aquery(
            "hello",
            slug,
            config=config,
        )
