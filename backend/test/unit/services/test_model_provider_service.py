import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.models.providers.builtin import BUILTIN_PROVIDERS
from yuxi.models.providers.service import (
    _normalize_payload,
    _normalize_remote_model,
    _validate_request_body_overrides_scope,
    check_credential_status,
    fetch_remote_models,
    update_provider_config,
)


def test_normalize_payload_accepts_enabled_chat_model():
    payload = _normalize_payload(
        {
            "provider_id": "openrouter-local",
            "display_name": "OpenRouter Local",
            "base_url": "https://openrouter.ai/api/v1",
            "enabled_models": [{"id": "anthropic/claude-sonnet-4.5", "type": "chat"}],
        }
    )

    assert payload["provider_id"] == "openrouter-local"
    assert payload["provider_type"] == "openai"
    assert "models_endpoint" not in payload
    assert "embedding_models_endpoint" not in payload
    assert payload["enabled_models"][0]["display_name"] == "anthropic/claude-sonnet-4.5"
    assert payload["enabled_models"][0]["source"] == "remote"


def test_normalize_payload_accepts_allowed_model_request_body_overrides():
    overrides = {
        "enable_thinking": True,
        "thinking_budget": 1024,
        "thinking": {"type": "enabled"},
        "reasoning": {"future_provider_option": {"enabled": True}},
        "reasoning_effort": "high",
    }
    payload = _normalize_payload(
        {
            "provider_id": "siliconflow-local",
            "display_name": "SiliconFlow Local",
            "base_url": "https://api.siliconflow.cn/v1",
            "enabled_models": [
                {
                    "id": "Qwen/Qwen3-8B",
                    "type": "chat",
                    "request_body_overrides": overrides,
                }
            ],
        }
    )

    assert payload["enabled_models"][0]["request_body_overrides"] == overrides


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (["enable_thinking"], "必须是 JSON 对象"),
        ({"messages": []}, "包含不支持的 extra_body 字段"),
        ({"thinking_budget": float("nan")}, "只能包含合法 JSON 值"),
    ],
)
def test_normalize_request_body_overrides_rejects_invalid_values(value, error):
    data = {
        "provider_id": "siliconflow-local",
        "display_name": "SiliconFlow Local",
        "base_url": "https://api.siliconflow.cn/v1",
        "enabled_models": [
            {
                "id": "Qwen/Qwen3-8B",
                "type": "chat",
                "request_body_overrides": value,
            }
        ],
    }
    with pytest.raises(ValueError, match=error):
        _normalize_payload(data)


@pytest.mark.parametrize(
    ("provider_type", "model_type", "error"),
    [
        ("anthropic", "chat", "仅支持 OpenAI 兼容供应商"),
        ("openai", "rerank", "仅支持 chat 模型"),
    ],
)
def test_request_body_overrides_require_openai_chat_model(provider_type, model_type, error):
    model = {
        "id": "model",
        "type": model_type,
        "request_body_overrides": {"thinking_budget": 1024},
    }
    with pytest.raises(ValueError, match=error):
        _validate_request_body_overrides_scope([model], provider_type)


@pytest.mark.asyncio
async def test_update_provider_config_rejects_provider_type_change_with_existing_overrides(monkeypatch):
    provider = SimpleNamespace(
        provider_id="openai-local",
        provider_type="openai",
        capabilities=["chat"],
        enabled_models=[
            {
                "id": "chat-model",
                "type": "chat",
                "request_body_overrides": {"enable_thinking": False},
            }
        ],
    )

    async def fake_get_model_provider(db, provider_id):
        del db
        return provider if provider_id == "openai-local" else None

    async def fail_update_model_provider(db, provider, data):
        pytest.fail("不应在非法 request_body_overrides 范围下写入 provider")

    monkeypatch.setattr("yuxi.models.providers.service.get_model_provider", fake_get_model_provider)
    monkeypatch.setattr("yuxi.models.providers.service.update_model_provider", fail_update_model_provider)

    with pytest.raises(ValueError, match="仅支持 OpenAI 兼容供应商"):
        await update_provider_config(None, "openai-local", {"provider_type": "anthropic"}, "tester")


def test_normalize_payload_accepts_anthropic_provider_type():
    payload = _normalize_payload(
        {
            "provider_id": "xiaomi-token-plan",
            "display_name": "Xiaomi Token Plan",
            "provider_type": "anthropic",
            "base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
            "capabilities": ["chat"],
            "enabled_models": [{"id": "mimo-v2.5-pro", "type": "chat", "source": "manual"}],
        }
    )

    assert payload["provider_type"] == "anthropic"
    assert payload["enabled_models"][0]["id"] == "mimo-v2.5-pro"


def test_normalize_payload_rejects_unknown_enabled_model_type():
    with pytest.raises(ValueError, match="type 必须是"):
        _normalize_payload(
            {
                "provider_id": "openrouter-local",
                "display_name": "OpenRouter Local",
                "base_url": "https://openrouter.ai/api/v1",
                "enabled_models": [{"id": "unknown-model", "type": "unknown"}],
            }
        )


def test_normalize_payload_allows_embedding_without_dimension():
    """embedding 模型的 dimension 是可选字段，不提供也不会报错。"""
    payload = _normalize_payload(
        {
            "provider_id": "embedding-local",
            "display_name": "Embedding Local",
            "base_url": "https://example.com/v1",
            "capabilities": ["embedding"],
            "embedding_base_url": "https://example.com/v1/embeddings",
            "enabled_models": [{"id": "text-embedding", "type": "embedding"}],
        }
    )
    assert payload["provider_id"] == "embedding-local"
    assert payload["enabled_models"][0].get("dimension") is None


def test_normalize_remote_model_preserves_detailed_model_config():
    model = _normalize_remote_model(
        {
            "id": "xiaomi/mimo-v2-omni",
            "name": "Xiaomi: MiMo-V2-Omni",
            "context_length": 262144,
            "architecture": {
                "input_modalities": ["text", "audio", "image", "video"],
                "output_modalities": ["text"],
            },
            "top_provider": {"max_completion_tokens": 65536},
            "supported_parameters": ["temperature", "tools"],
        }
    )

    assert model["id"] == "xiaomi/mimo-v2-omni"
    assert model["display_name"] == "Xiaomi: MiMo-V2-Omni"
    assert model["type"] == "chat"
    assert model["input_modalities"] == ["text", "audio", "image", "video"]
    assert model["max_completion_tokens"] == 65536
    assert model["raw_metadata"]["supported_parameters"] == ["temperature", "tools"]


def test_normalize_remote_model_uses_endpoint_model_type():
    model = _normalize_remote_model({"id": "BAAI/bge-m3", "object": "model"}, "embedding")

    assert model["id"] == "BAAI/bge-m3"
    assert model["type"] == "embedding"


@pytest.mark.asyncio
async def test_fetch_remote_models_loads_embedding_only_when_capability_enabled(monkeypatch):
    calls = []

    async def fake_fetch(client, provider, headers, endpoint, model_type):
        calls.append((endpoint, model_type))
        return [{"id": f"{model_type}-model", "type": model_type}]

    monkeypatch.setattr("yuxi.models.providers.service._fetch_models_from_endpoint", fake_fetch)

    class Provider:
        base_url = "https://example.com/v1"
        api_key = None
        api_key_env = None
        headers_json = {}
        capabilities = ["chat", "embedding", "rerank"]
        models_endpoint = "/models"
        embedding_models_endpoint = "/embeddings/models"
        rerank_models_endpoint = None

    models = await fetch_remote_models(Provider())

    assert calls == [("/models", "chat"), ("/embeddings/models", "embedding")]
    assert [model["type"] for model in models] == ["chat", "embedding"]


def test_normalize_payload_rejects_ollama_provider_type():
    with pytest.raises(ValueError, match="provider_type 必须是"):
        _normalize_payload(
            {
                "provider_id": "ollama-local",
                "display_name": "Ollama Local",
                "provider_type": "ollama",
                "base_url": "http://localhost:11434",
            }
        )


def test_builtin_provider_templates_default_to_openai_provider_type():
    provider_types = {
        _normalize_payload(
            {
                "provider_id": provider["provider_id"],
                "display_name": provider["display_name"],
                "base_url": provider["base_url"],
                "provider_type": provider.get("provider_type"),
            }
        )["provider_type"]
        for provider in BUILTIN_PROVIDERS
    }
    assert provider_types == {"openai"}
    assert all("ollama" not in provider["provider_id"] for provider in BUILTIN_PROVIDERS)


@pytest.mark.parametrize(
    ("is_enabled", "api_key", "api_key_env", "expected"),
    [
        (False, None, None, "ok"),
        (True, "sk-test", None, "ok"),
        (True, None, "TEST_API_KEY", "ok"),
        (True, None, "MISSING_KEY", "warning"),
        (True, None, None, "warning"),
    ],
)
def test_check_credential_status(monkeypatch, is_enabled, api_key, api_key_env, expected):
    """check_credential_status 依据启用状态与凭证配置返回 ok / warning。"""
    if api_key_env == "TEST_API_KEY":
        monkeypatch.setenv(api_key_env, "exists")
    elif api_key_env == "MISSING_KEY":
        monkeypatch.delenv(api_key_env, raising=False)

    provider = SimpleNamespace(is_enabled=is_enabled, api_key=api_key, api_key_env=api_key_env)

    assert check_credential_status(provider) == expected


# ==================== 手动添加模型 / source 字段 ====================


def test_normalize_payload_accepts_manual_source():
    """source=manual 表示管理员手动添加的模型，规范化保留该标签。"""
    payload = _normalize_payload(
        {
            "provider_id": "custom-local",
            "display_name": "Custom Local",
            "base_url": "https://example.com/v1",
            "capabilities": ["chat"],
            "enabled_models": [{"id": "my-chat-model", "type": "chat", "source": "manual"}],
        }
    )

    assert payload["enabled_models"][0]["source"] == "manual"


def test_normalize_payload_rejects_invalid_source():
    """source 仅允许 manual 或 remote，其他取值视为非法。"""
    with pytest.raises(ValueError, match="source 必须是"):
        _normalize_payload(
            {
                "provider_id": "custom-local",
                "display_name": "Custom Local",
                "base_url": "https://example.com/v1",
                "enabled_models": [{"id": "x", "type": "chat", "source": "custom"}],
            }
        )


def test_normalize_payload_rejects_model_type_not_in_capabilities():
    """provider 仅声明 chat 能力时，不允许写入 embedding 类型的模型。"""
    with pytest.raises(ValueError, match="不在 provider 能力"):
        _normalize_payload(
            {
                "provider_id": "chat-only",
                "display_name": "Chat Only",
                "base_url": "https://example.com/v1",
                "capabilities": ["chat"],
                "enabled_models": [{"id": "rogue-embedding", "type": "embedding", "dimension": 1024}],
            }
        )


def test_normalize_payload_allows_model_type_within_capabilities():
    """provider 同时声明 chat + embedding 时，两类模型均可正常写入。"""
    payload = _normalize_payload(
        {
            "provider_id": "multi-cap",
            "display_name": "Multi Cap",
            "base_url": "https://example.com/v1",
            "capabilities": ["chat", "embedding"],
            "embedding_base_url": "https://example.com/v1/embeddings",
            "embedding_models_endpoint": "/embeddings/models",
            "enabled_models": [
                {"id": "chat-1", "type": "chat", "source": "manual"},
                {
                    "id": "embed-1",
                    "type": "embedding",
                    "source": "manual",
                    "dimension": 1024,
                },
            ],
        }
    )

    types = [model["type"] for model in payload["enabled_models"]]
    sources = [model["source"] for model in payload["enabled_models"]]
    assert types == ["chat", "embedding"]
    assert sources == ["manual", "manual"]


def test_normalize_payload_accepts_image_model_type():
    """image 是正式模型类型，provider 声明该能力后可写入图像生成模型。"""
    payload = _normalize_payload(
        {
            "provider_id": "image-provider",
            "display_name": "Image Provider",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "capabilities": ["chat", "image"],
            "enabled_models": [{"id": "qwen-image-3.0", "type": "image", "source": "manual"}],
        }
    )

    assert payload["enabled_models"][0]["type"] == "image"


def test_normalize_payload_rejects_image_model_without_capability():
    """provider 未声明 image 能力时，拒绝写入 image 类型模型。"""
    with pytest.raises(ValueError, match="不在 provider 能力"):
        _normalize_payload(
            {
                "provider_id": "chat-only",
                "display_name": "Chat Only",
                "base_url": "https://example.com/v1",
                "capabilities": ["chat"],
                "enabled_models": [{"id": "qwen-image-3.0", "type": "image"}],
            }
        )


def test_normalize_remote_model_preserves_image_type():
    """远端模型清单返回 image 类型时，归一化保留 image 而非兜底成 chat。"""
    model = _normalize_remote_model({"id": "qwen-image-3.0", "type": "image", "name": "Qwen Image"})

    assert model["id"] == "qwen-image-3.0"
    assert model["type"] == "image"


def _image_model_info(*, base_url: str, model_id: str = "qwen-image-3.0"):
    from types import SimpleNamespace

    return SimpleNamespace(
        provider_id="alibaba-cn",
        model_id=model_id,
        model_type="image",
        display_name=model_id,
        api_key="sk-test",
        base_url=base_url,
        provider_type="openai",
        spec=f"alibaba-cn:{model_id}",
    )


@pytest.mark.asyncio
async def test_image_test_uses_native_endpoint_without_size_parameter(httpx_mock):
    """图像模型测试走 DashScope 原生接口，且不硬编码 size(型号支持集合不同)。"""
    from yuxi.models.providers.service import _test_image_generation_model

    httpx_mock.add_response(
        url="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        json={"output": {"choices": [{"message": {"content": [{"image": "https://example.test/a.png"}]}}]}},
    )

    info = _image_model_info(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    result = await _test_image_generation_model("alibaba-cn:qwen-image-3.0", info)

    assert result["status"] == "available"
    request = httpx_mock.get_requests()[-1]
    assert request.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
    body = json.loads(request.content)
    assert "size" not in body["parameters"]
    assert body["input"]["messages"][0]["content"] == [{"text": "a red circle"}]


@pytest.mark.asyncio
async def test_image_test_reports_unsupported_provider_without_sending_request(httpx_mock):
    """非 DashScope 供应商的 image 模型：显式报告暂不支持，不得按其 base_url 拼 DashScope 路径。"""
    from yuxi.models.providers.service import _test_image_generation_model

    info = _image_model_info(base_url="https://api.example.com/v1/images")
    result = await _test_image_generation_model("example:flux-pro", info)

    assert result["status"] == "unavailable"
    assert "DashScope" in result["message"]
    assert httpx_mock.get_requests() == []
