from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_model_provider_api_persists_and_validates_request_body_overrides(
    test_client,
    admin_headers,
):
    provider_id = f"pytest-request-overrides-{uuid.uuid4().hex[:8]}"

    try:
        create_response = await test_client.post(
            "/api/system/model-providers",
            json={
                "provider_id": provider_id,
                "display_name": "Pytest Request Overrides",
                "provider_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "sk-test",
                "capabilities": ["chat"],
                "enabled_models": [
                    {
                        "id": "chat-model",
                        "type": "chat",
                        "source": "manual",
                        "request_body_overrides": {"enable_thinking": False},
                    }
                ],
                "is_enabled": False,
            },
            headers=admin_headers,
        )
        assert create_response.status_code == 200, create_response.text

        get_response = await test_client.get(
            f"/api/system/model-providers/{provider_id}",
            headers=admin_headers,
        )
        assert get_response.status_code == 200, get_response.text
        model = get_response.json()["data"]["enabled_models"][0]
        assert model["request_body_overrides"] == {"enable_thinking": False}

        clear_response = await test_client.put(
            f"/api/system/model-providers/{provider_id}",
            json={
                "enabled_models": [
                    {
                        "id": "chat-model",
                        "type": "chat",
                        "source": "manual",
                        "request_body_overrides": {},
                    }
                ]
            },
            headers=admin_headers,
        )
        assert clear_response.status_code == 200, clear_response.text
        model = clear_response.json()["data"]["enabled_models"][0]
        assert model["request_body_overrides"] == {}

        invalid_response = await test_client.put(
            f"/api/system/model-providers/{provider_id}",
            json={
                "enabled_models": [
                    {
                        "id": "chat-model",
                        "type": "chat",
                        "source": "manual",
                        "request_body_overrides": {"messages": []},
                    }
                ]
            },
            headers=admin_headers,
        )
        assert invalid_response.status_code == 400
        assert "受保护字段" in invalid_response.json()["detail"]
    finally:
        await test_client.delete(f"/api/system/model-providers/{provider_id}", headers=admin_headers)


async def test_model_provider_api_rejects_request_body_overrides_outside_openai_chat(
    test_client,
    admin_headers,
):
    anthropic_response = await test_client.post(
        "/api/system/model-providers",
        json={
            "provider_id": f"pytest-anthropic-overrides-{uuid.uuid4().hex[:8]}",
            "display_name": "Pytest Anthropic Overrides",
            "provider_type": "anthropic",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "capabilities": ["chat"],
            "enabled_models": [
                {
                    "id": "claude-sonnet",
                    "type": "chat",
                    "source": "manual",
                    "request_body_overrides": {"thinking_budget": 1024},
                }
            ],
            "is_enabled": False,
        },
        headers=admin_headers,
    )
    assert anthropic_response.status_code == 400
    assert "仅支持 OpenAI 兼容供应商" in anthropic_response.json()["detail"]

    rerank_response = await test_client.post(
        "/api/system/model-providers",
        json={
            "provider_id": f"pytest-rerank-overrides-{uuid.uuid4().hex[:8]}",
            "display_name": "Pytest Rerank Overrides",
            "provider_type": "openai",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "capabilities": ["rerank"],
            "enabled_models": [
                {
                    "id": "rerank-model",
                    "type": "rerank",
                    "source": "manual",
                    "request_body_overrides": {"top_n": 5},
                }
            ],
            "is_enabled": False,
        },
        headers=admin_headers,
    )
    assert rerank_response.status_code == 400
    assert "仅支持 chat 模型" in rerank_response.json()["detail"]
