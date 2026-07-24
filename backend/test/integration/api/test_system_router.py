"""
Integration tests for system router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_health_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_discovery_declares_cli_knowledge_capabilities(test_client):
    response = await test_client.get("/api/system/discovery")
    assert response.status_code == 200
    cli_capabilities = response.json()["capabilities"]["cli"]
    for capability in ("kb_list", "kb_files", "kb_query", "kb_open", "kb_find"):
        assert cli_capabilities.get(capability) is True, capability
    assert "kb_parse" not in cli_capabilities
    assert "kb_index" not in cli_capabilities


async def test_info_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "data" in payload


async def test_config_get_requires_login_and_update_requires_admin(test_client, standard_user):
    assert (await test_client.get("/api/system/config")).status_code == 401
    user_config_response = await test_client.get("/api/system/config", headers=standard_user["headers"])
    assert user_config_response.status_code == 200, user_config_response.text

    update_response = await test_client.post(
        "/api/system/config/update",
        json={"default_ocr_engine": "rapid_ocr"},
        headers=standard_user["headers"],
    )
    assert update_response.status_code == 403


async def test_ocr_options_and_config_options_permissions(
    test_client,
    standard_user,
    admin_headers,
):
    options_response = await test_client.get("/api/system/ocr/options", headers=standard_user["headers"])
    assert options_response.status_code == 200, options_response.text
    options = options_response.json()
    assert options["default_engine"]
    assert options["engines"]
    assert {"endpoint", "credential_source", "credential_ref", "default_params"}.isdisjoint(options["engines"][0])

    denied_response = await test_client.get("/api/system/config/options", headers=standard_user["headers"])
    assert denied_response.status_code == 403

    configs_response = await test_client.get("/api/system/config/options", headers=admin_headers)
    assert configs_response.status_code == 200, configs_response.text
    configs = configs_response.json()["options"]
    assert {item["key"] for item in configs} == {
        "mineru_ocr_host_opts",
        "mineru_official_api_opts",
        "pp_structure_v3_ocr_host_opts",
        "paddleocr_api_opts",
    }
    assert all("deepseek" not in item["key"] for item in configs)
    official = next(item for item in configs if item["key"] == "mineru_official_api_opts")
    assert official["params"]["fields"][0]["sensitive"] is True
    assert official["value"]["api_key"] == ""
    assert isinstance(official["sensitive_configured"]["api_key"], bool)
    assert official["sensitive_state"]["api_key"]["source"] in {"database", "environment", "none"}
    assert "configured" in official["sensitive_state"]["api_key"]


async def test_config_option_update_is_visible_and_restored(test_client, admin_headers):
    response = await test_client.get("/api/system/config/options", headers=admin_headers)
    option = next(item for item in response.json()["options"] if item["key"] == "mineru_ocr_host_opts")
    previous_url = option["value"].get("server_url", "")

    try:
        update_response = await test_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": {"server_url": "http://integration-mineru:30001"}},
            headers=admin_headers,
        )
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["option"]["value"]["server_url"] == "http://integration-mineru:30001/"
    finally:
        restore_response = await test_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": {"server_url": previous_url}},
            headers=admin_headers,
        )
        assert restore_response.status_code == 200, restore_response.text


async def test_ocr_health_is_available_to_logged_in_users_and_returns_all_methods(
    test_client,
    standard_user,
    admin_headers,
    monkeypatch,
):
    async def fake_health(db):
        del db
        return {"rapid_ocr": {"status": "healthy", "message": "ok"}}

    monkeypatch.setattr("yuxi.services.ocr_service.check_all_ocr_health", fake_health)

    response = await test_client.get("/api/system/ocr/health", headers=standard_user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["health"]["rapid_ocr"]["status"] == "healthy"

    admin_response = await test_client.get("/api/system/ocr/health", headers=admin_headers)
    assert admin_response.status_code == 200, admin_response.text


async def test_admin_can_fetch_config_and_reload_info(test_client, admin_headers):
    config_response = await test_client.get("/api/system/config", headers=admin_headers)
    assert config_response.status_code == 200, config_response.text
    assert isinstance(config_response.json(), dict)

    reload_response = await test_client.post("/api/system/info/reload", headers=admin_headers)
    assert reload_response.status_code == 200, reload_response.text
    reload_payload = reload_response.json()
    assert reload_payload["success"] is True
    assert "data" in reload_payload


async def test_sandbox_config_is_environment_only(test_client, admin_headers):
    config_response = await test_client.get("/api/system/config", headers=admin_headers)
    assert config_response.status_code == 200, config_response.text
    sandbox_fields = {
        "sandbox_provider",
        "sandbox_provisioner_url",
        "sandbox_provisioner_token",
        "sandbox_virtual_path_prefix",
        "sandbox_exec_timeout_seconds",
        "sandbox_max_output_bytes",
        "sandbox_keepalive_interval_seconds",
    }
    assert sandbox_fields.isdisjoint(config_response.json())
    assert sandbox_fields.isdisjoint(config_response.json()["_config_items"])

    update_response = await test_client.post(
        "/api/system/config",
        json={"key": "sandbox_provisioner_url", "value": "http://other:8002"},
        headers=admin_headers,
    )
    assert update_response.status_code == 400
    assert update_response.json()["detail"] == "未知配置项: sandbox_provisioner_url"


async def test_admin_can_fetch_tools_with_config_guide_field(test_client, admin_headers):
    response = await test_client.get("/api/system/tools", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], list)
    assert payload["data"]
    assert "config_guide" in payload["data"][0]
