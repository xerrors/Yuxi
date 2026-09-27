import pytest

from server.routers import model_provider_router
from server.routers.model_provider_router import ModelProviderPayload


@pytest.mark.parametrize("value", ["true", "false", 1, 0])
def test_model_provider_payload_rejects_non_boolean_uid_header_values(value):
    with pytest.raises(ValueError):
        ModelProviderPayload(include_user_uid=value)


@pytest.mark.parametrize("value", [True, False])
def test_model_provider_payload_accepts_boolean_uid_header_values(value):
    assert ModelProviderPayload(include_user_uid=value).include_user_uid is value


def test_model_provider_payload_accepts_embedding_and_rerank_urls():
    payload = ModelProviderPayload(
        provider_id="mixed-provider",
        display_name="Mixed Provider",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/v1/embeddings",
        rerank_base_url="https://api.example.com/v1/rerank",
        capabilities=["chat", "embedding", "rerank"],
    )

    data = payload.model_dump(exclude_none=True)

    assert data["embedding_base_url"] == "https://api.example.com/v1/embeddings"
    assert data["rerank_base_url"] == "https://api.example.com/v1/rerank"


@pytest.mark.asyncio
async def test_update_provider_commits_before_refreshing_cache(monkeypatch):
    calls = []

    class Db:
        async def commit(self):
            calls.append("commit")

    class User:
        username = "admin"

    class Provider:
        def to_dict(self):
            return {"provider_id": "alibaba"}

    async def fake_update_provider_config(db, provider_id, data, username):
        calls.append("update")
        return Provider()

    async def fake_refresh_model_cache():
        calls.append("refresh")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update_provider_config)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fake_refresh_model_cache)

    result = await model_provider_router.update_provider(
        "alibaba",
        ModelProviderPayload(enabled_models=[]),
        current_user=User(),
        db=Db(),
    )

    assert result == {"success": True, "data": {"provider_id": "alibaba"}}
    assert calls == ["update", "commit", "refresh"]
