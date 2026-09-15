"""Atlas Cloud 内置供应商模板的契约测试。

Atlas Cloud 是 OpenAI 兼容的聚合网关，内置模板只提供 base_url、凭证环境变量
和模型发现端点；真正会出错的是这三项与网关实际行为不一致：端点拼错导致模型
发现静默返回空列表、凭证环境变量拼错导致请求不带 Authorization、以及网关的
响应形状与 `_normalize_remote_model` 的取值路径不匹配。这里用网关真实响应片段
作为 oracle，逐项给出正向与负向用例。
"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.models.providers.builtin import BUILTIN_PROVIDERS
from yuxi.models.providers.service import (
    _normalize_payload,
    check_credential_status,
    fetch_remote_models,
)

# 2026-09-15 从 GET https://api.atlascloud.ai/v1/models 取回的真实条目，未改字段。
_REAL_ATLASCLOUD_MODEL = {
    "id": "openai/gpt-4.1-mini",
    "hugging_face_id": "",
    "name": "GPT 4.1 mini",
    "object": "model",
    "created": 1626777600,
    "is_ready": False,
    "owned_by": "custom",
    "root": "openai/gpt-4.1-mini",
    "description": "GPT 4.1 mini",
    "context_length": 1047576,
    "max_output_length": 32768,
    "quantization": "fp8",
    "input_modalities": ["text"],
    "output_modalities": ["text"],
    "pricing": {
        "prompt": "0.0000004",
        "completion": "0.0000016",
        "image": "0",
        "request": "0",
        "input_cache_read": "0.0000001",
    },
}


def _atlascloud_template() -> dict:
    """返回内置模板中的 Atlas Cloud 定义。"""
    matches = [p for p in BUILTIN_PROVIDERS if p["provider_id"] == "atlascloud"]
    assert len(matches) == 1, "Atlas Cloud 内置模板必须且只能有一条"
    return matches[0]


def _provider(**overrides):
    template = _atlascloud_template()
    fields = {
        "provider_id": template["provider_id"],
        "base_url": template["base_url"],
        "models_endpoint": template["models_endpoint"],
        "api_key_env": template["api_key_env"],
        "api_key": None,
        "headers_json": {},
        "capabilities": [],
        "is_enabled": True,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_template_points_at_the_atlas_cloud_gateway():
    """模板三要素与网关实际地址一致。"""
    template = _atlascloud_template()
    assert template["display_name"] == "Atlas Cloud"
    assert template["base_url"] == "https://api.atlascloud.ai/v1"
    assert template["models_endpoint"] == "https://api.atlascloud.ai/v1/models"
    assert template["api_key_env"] == "ATLASCLOUD_API_KEY"


def test_template_normalizes_to_openai_provider_type():
    """未声明 provider_type 时必须归一化为 openai，否则走不到兼容适配器。"""
    normalized = _normalize_payload(
        {
            "provider_id": "atlascloud",
            "display_name": "Atlas Cloud",
            "base_url": _atlascloud_template()["base_url"],
            "provider_type": _atlascloud_template().get("provider_type"),
        }
    )
    assert normalized["provider_type"] == "openai"


def test_credential_status_follows_the_env_var(monkeypatch):
    """环境变量缺失时必须是 warning，而不是伪装成已配置。"""
    monkeypatch.delenv("ATLASCLOUD_API_KEY", raising=False)
    assert check_credential_status(_provider()) == "warning"
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "apikey-test")
    assert check_credential_status(_provider()) == "ok"


async def test_model_discovery_uses_the_env_key_and_real_response(httpx_mock, monkeypatch):
    """按模板发现模型：命中网关真实端点、带 Bearer、解析出真实字段。"""
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "apikey-test")
    httpx_mock.add_response(
        url="https://api.atlascloud.ai/v1/models",
        json={"object": "list", "data": [_REAL_ATLASCLOUD_MODEL]},
    )

    models = await fetch_remote_models(_provider())

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer apikey-test"
    assert len(models) == 1
    model = models[0]
    assert model["id"] == "openai/gpt-4.1-mini"
    assert model["display_name"] == "GPT 4.1 mini"
    assert model["type"] == "chat"
    assert model["context_length"] == 1047576
    assert model["pricing"]["prompt"] == "0.0000004"


async def test_model_discovery_sends_no_bearer_without_a_key(httpx_mock, monkeypatch):
    """负向：环境变量缺失时不能凭空造出 Authorization 头。"""
    monkeypatch.delenv("ATLASCLOUD_API_KEY", raising=False)
    httpx_mock.add_response(
        url="https://api.atlascloud.ai/v1/models",
        json={"object": "list", "data": [_REAL_ATLASCLOUD_MODEL]},
    )

    await fetch_remote_models(_provider())

    assert "Authorization" not in httpx_mock.get_request().headers


async def test_embedding_endpoint_is_not_probed(httpx_mock, monkeypatch):
    """负向：模板未声明 embedding 能力，只能打 chat 端点一次。"""
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "apikey-test")
    httpx_mock.add_response(
        url="https://api.atlascloud.ai/v1/models",
        json={"object": "list", "data": [_REAL_ATLASCLOUD_MODEL]},
    )

    await fetch_remote_models(_provider())

    assert len(httpx_mock.get_requests()) == 1


async def test_non_list_response_fails_loudly(httpx_mock, monkeypatch):
    """负向：网关返回非列表时必须报错，而不是静默当成 0 个模型。"""
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "apikey-test")
    httpx_mock.add_response(
        url="https://api.atlascloud.ai/v1/models",
        json={"object": "list", "data": {"id": "openai/gpt-4.1-mini"}},
    )

    with pytest.raises(ValueError):
        await fetch_remote_models(_provider())


async def test_modalities_are_not_read_from_this_gateway(httpx_mock, monkeypatch):
    """已知差异：Atlas Cloud 把模态放在顶层，归一化只读 architecture 子对象。

    这里把当前行为钉住，避免以后有人误以为模态已经可用；要接上得改
    `_normalize_remote_model` 的公共取值路径，属于独立变更。
    """
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "apikey-test")
    httpx_mock.add_response(
        url="https://api.atlascloud.ai/v1/models",
        json={"object": "list", "data": [_REAL_ATLASCLOUD_MODEL]},
    )

    models = await fetch_remote_models(_provider())

    assert models[0]["input_modalities"] == []
    assert models[0]["output_modalities"] == []
    # 原始响应仍然完整保留，需要时可从 raw_metadata 取回。
    assert models[0]["raw_metadata"]["input_modalities"] == ["text"]
