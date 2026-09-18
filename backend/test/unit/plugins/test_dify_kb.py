from __future__ import annotations

import pytest

from yuxi.knowledge.implementations.dify import DifyKB
from yuxi.knowledge.read_models import KnowledgeBaseConfig


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
async def test_dify_rejects_folder_rename(tmp_path):
    kb = DifyKB(str(tmp_path))

    with pytest.raises(ValueError, match="只读检索连接器不支持该操作"):
        await kb.rename_folder("kb-1", "folder-1", "renamed")


@pytest.mark.asyncio
async def test_dify_rejects_file_rename(tmp_path):
    """只读连接器的写操作必须在 executor 处 fail-closed。

    HTTP 侧虽然会被 _ensure_database_supports_documents 先拦下，但 KnowledgeBaseManager.rename_file
    是公开入口，漏掉这条覆写就可能被别的调用路径绕过。
    """
    kb = DifyKB(str(tmp_path))

    with pytest.raises(ValueError, match="只读检索连接器不支持该操作"):
        await kb.rename_file("kb-1", "file-1", "renamed.pdf")


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
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
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
async def test_dify_kb_aquery_error_returns_empty(monkeypatch, tmp_path):
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
        "yuxi.knowledge.implementations.dify.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(raises=RuntimeError("boom"), **kwargs),
    )

    result = await kb.aquery(
        "hello",
        slug,
        config=config,
    )
    assert result == []
