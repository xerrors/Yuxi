"""Provider DB runtime selector connectivity tests."""

from __future__ import annotations

import os
from typing import Any

import pytest

from yuxi.models.chat import select_model
from yuxi.models.embed import OtherEmbedding
from yuxi.models.rerank import DashscopeReranker, OpenAIReranker
from yuxi.models.providers.cache import ModelInfo
from yuxi.models.providers.service import (
    resolve_api_key,
    ensure_builtin_model_providers_in_db,
    get_model_provider_by_id,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ModelProvider

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MODEL_PROVIDER_CONNECTIVITY") != "1",
        reason="Set RUN_MODEL_PROVIDER_CONNECTIVITY=1 to call real provider model APIs.",
    ),
]


def _model_spec(provider: ModelProvider, model: dict[str, Any]) -> dict[str, Any]:
    """Turn an enabled model item into runtime parameters for existing model clients."""
    api_key = resolve_api_key(provider)
    if api_key is None:
        api_key = "no_api_key"
    return {
        "provider_id": provider.provider_id,
        "model_id": model["id"],
        "model_type": model["type"],
        "api_key": api_key,
        "base_url": model.get("base_url_override") or provider.base_url,
        "dimension": model.get("dimension"),
        "batch_size": int(model.get("batch_size") or 40),
        "parameters": model.get("extra", {}).get("parameters", {}),
    }


def _select_enabled_model(provider: ModelProvider, model_type: str, env_name: str) -> dict[str, Any]:
    """Select a runnable enabled model, mirroring select_model-style explicit/default lookup."""
    preferred_model_id = os.getenv(env_name)
    enabled_models = provider.enabled_models or []
    for model in enabled_models:
        if model.get("type") != model_type:
            continue
        if preferred_model_id and model.get("id") != preferred_model_id:
            continue
        return model
    if preferred_model_id:
        pytest.skip(f"{provider.provider_id} does not expose {preferred_model_id} as {model_type}.")
    pytest.skip(f"{provider.provider_id} has no enabled {model_type} model.")


def _select_provider_model(provider: ModelProvider, model_type: str, env_name: str) -> dict[str, Any]:
    """Select provider DB model params for chat, embedding or rerank clients."""
    model = _select_enabled_model(provider, model_type, env_name)
    spec = _model_spec(provider, model)
    if not spec["api_key"]:
        pytest.skip(f"{provider.provider_id} requires {provider.api_key_env} for connectivity testing.")
    return spec


async def _load_provider() -> ModelProvider:
    """Load provider through service/repository, matching app startup bootstrap behavior."""
    provider_id = os.getenv("TEST_MODEL_PROVIDER_ID", "siliconflow-cn")
    if not pg_manager._initialized:
        pg_manager.initialize()

    async with pg_manager.get_async_session_context() as db:
        await ensure_builtin_model_providers_in_db(db)
        provider = await get_model_provider_by_id(db, provider_id)
        if provider is None:
            pytest.skip(f"Provider {provider_id} is not configured.")
        return provider


async def test_provider_db_chat_model_connectivity(monkeypatch: pytest.MonkeyPatch):
    provider = await _load_provider()
    spec = _select_provider_model(provider, "chat", "TEST_PROVIDER_CHAT_MODEL")
    model_spec = f"{spec['provider_id']}:{spec['model_id']}"
    info = ModelInfo(
        provider_id=spec["provider_id"],
        model_id=spec["model_id"],
        model_type="chat",
        display_name=spec["model_id"],
        api_key=spec["api_key"],
        base_url=spec["base_url"],
        provider_type=provider.provider_type,
        extra={"parameters": spec["parameters"]},
    )

    def get_model_info(current: str):
        return info if current == model_spec else None

    monkeypatch.setattr("yuxi.models.chat.model_cache.get_model_info", get_model_info)
    monkeypatch.setattr("yuxi.agents.models.model_cache.get_model_info", get_model_info)

    model = select_model(model_spec, model_params=spec["parameters"])
    response = await model.call([{"role": "user", "content": "Say 1"}], stream=False)

    assert response.content


async def test_provider_db_embedding_model_connectivity():
    provider = await _load_provider()
    spec = _select_provider_model(provider, "embedding", "TEST_PROVIDER_EMBEDDING_MODEL")

    model = OtherEmbedding(
        name=spec["model_id"],
        dimension=spec["dimension"],
        base_url=spec["base_url"],
        api_key=spec["api_key"],
        batch_size=spec["batch_size"],
    )
    success, message = await model.test_connection()

    assert success, message


async def test_provider_db_rerank_model_connectivity():
    provider = await _load_provider()
    spec = _select_provider_model(provider, "rerank", "TEST_PROVIDER_RERANK_MODEL")

    reranker_class = DashscopeReranker if provider.provider_id == "dashscope" else OpenAIReranker
    reranker = reranker_class(
        model_name=spec["model_id"],
        api_key=spec["api_key"],
        base_url=spec["base_url"],
        parameters=spec["parameters"],
    )
    scores = await reranker.acompute_score(("Yuxi knowledge base", ["Yuxi is a RAG platform.", "Unrelated text."]))
    await reranker.aclose()

    assert len(scores) == 2
