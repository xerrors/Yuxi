"""
Integration tests for system router endpoints.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.infrastructure.runtime_settings import get_runtime_dir
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.repositories.options import get_option
from yuxi.modules.system.services.options import invalidate_option_cache

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_health_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_logs_endpoint_returns_only_api_process_log(test_client, admin_headers):
    """管理员日志接口应明确读取当前 API 进程拥有的日志文件。"""
    from yuxi.infrastructure.observability.logging import LOG_FILE

    api_marker = f"api-log-contract-{uuid4()}"
    worker_marker = f"worker-log-contract-{uuid4()}"
    log_path = Path(LOG_FILE)
    worker_log_path = get_runtime_dir().parent / "worker" / "logs" / log_path.name
    worker_log_original = worker_log_path.read_bytes() if worker_log_path.exists() else None

    assert log_path.parent == get_runtime_dir() / "logs"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"2026-08-17 20:00:00 - INFO - test:1 - {api_marker}\n")
    worker_log_path.parent.mkdir(parents=True, exist_ok=True)
    with worker_log_path.open("a", encoding="utf-8") as worker_log:
        worker_log.write(f"2026-08-17 20:00:00 - INFO - worker:1 - {worker_marker}\n")

    try:
        response = await test_client.get("/api/system/logs?levels=INFO", headers=admin_headers)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["scope"] == "api"
        assert payload["log_file"] == LOG_FILE
        assert api_marker in payload["log"]
        assert worker_marker not in payload["log"]
    finally:
        if worker_log_original is None:
            worker_log_path.unlink(missing_ok=True)
        else:
            worker_log_path.write_bytes(worker_log_original)


async def test_readiness_endpoint_proves_core_runtime_dependencies(test_client):
    response = await test_client.get("/api/system/ready")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert response.json()["checks"] == {
        "startup": {"status": "ok"},
        "postgres": {"status": "ok"},
        "redis": {"status": "ok"},
        "worker": {"status": "ok"},
    }
    assert response.json()["degraded"] is False
    assert response.json()["components"]
    assert all(component["status"] == "ok" for component in response.json()["components"].values() if component["required"])


async def test_discovery_and_openapi_declare_full_knowledge_capabilities(test_client):
    response = await test_client.get("/api/system/discovery")
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["features"]["knowledge"] is True
    cli_capabilities = capabilities["cli"]
    assert cli_capabilities["agent_list"] is True
    assert cli_capabilities["agent_show"] is True
    for capability in ("kb_list", "kb_files", "kb_query", "kb_open", "kb_find"):
        assert cli_capabilities.get(capability) is True, capability
    assert "kb_parse" not in cli_capabilities
    assert "kb_index" not in cli_capabilities

    openapi_response = await test_client.get("/openapi.json")
    assert openapi_response.status_code == 200, openapi_response.text
    openapi_paths = openapi_response.json()["paths"]
    assert {
        "/api/knowledge/knowledge-bases",
        "/api/dashboard/stats/knowledge",
        "/api/workspace/knowledge/tree",
        "/api/workspace/knowledge/file",
        "/api/workspace/knowledge/download",
    }.issubset(openapi_paths)


async def test_info_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert set(payload["data"]) == {"branding", "footer"}
    assert set(payload["data"]["branding"]) == {"name", "logo", "title", "login_bg"}
    assert all(isinstance(value, str) for value in payload["data"]["branding"].values())


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
        "remote_skill_source_policy",
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


@pytest.mark.parametrize(
    ("key", "values", "invalid_values"),
    [
        ("mineru_ocr_host_opts", {"tier": "advanced"}, [{"tier": "unsupported"}, {"tier": ""}]),
        ("mineru_official_api_opts", {"model_version": "vlm"}, [{"model_version": "hybrid"}, {"model_version": ""}]),
    ],
)
async def test_mineru_defaults_round_trip_and_reject_invalid_choices(
    test_client, admin_headers, postgres_sessions, key, values, invalid_values
):
    """通过真实 HTTP 保存默认值，独立回读 PG 并确认非法值不改变配置。"""
    async with postgres_sessions() as db:
        record = await get_option(db, key)
        previous = {"value": deepcopy(record.value), "updated_by": record.updated_by, "updated_at": record.updated_at}

    try:
        saved = await test_client.put(
            f"/api/system/config/options/{key}",
            json={"value": values},
            headers=admin_headers,
        )
        assert saved.status_code == 200, saved.text
        reloaded = await test_client.get("/api/system/config/options", headers=admin_headers)
        assert reloaded.status_code == 200, reloaded.text
        option = next(item for item in reloaded.json()["options"] if item["key"] == key)
        for field, value in values.items():
            assert option["value"][field] == value
        if key == "mineru_official_api_opts":
            assert option["value"]["api_key"] == ""

        for invalid in invalid_values:
            rejected = await test_client.put(f"/api/system/config/options/{key}", json={"value": invalid}, headers=admin_headers)
            assert rejected.status_code == 400, rejected.text
            assert "不支持的" in rejected.text

        async with postgres_sessions() as db:
            record = await get_option(db, key)
            for field, value in values.items():
                assert record.value[field] == value
            unchanged = all(record.value.get(field) == value for field, value in (previous["value"] or {}).items() if field not in values)
            assert unchanged, "更新模型不得改变凭据或其他配置"
    finally:
        async with postgres_sessions() as db:
            await db.execute(update(ConfigOption).where(ConfigOption.key == key).values(**previous))
            await db.commit()
        await invalidate_option_cache(key)


async def test_remote_skill_policy_explicit_empty_list_is_visible_and_restored(test_client, admin_headers):
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            record = await get_option(db, "remote_skill_source_policy")
            assert record is not None
            previous_value = deepcopy(record.value)
            previous_updated_by = record.updated_by
            previous_updated_at = record.updated_at

        try:
            update_response = await test_client.put(
                "/api/system/config/options/remote_skill_source_policy",
                json={"value": {"allowed_hosts": []}},
                headers=admin_headers,
            )
            assert update_response.status_code == 200, update_response.text
            assert update_response.json()["option"]["value"]["allowed_hosts"] == []
        finally:
            async with session_factory() as db:
                await db.execute(
                    update(ConfigOption)
                    .where(ConfigOption.key == "remote_skill_source_policy")
                    .values(
                        value=previous_value,
                        updated_by=previous_updated_by,
                        updated_at=previous_updated_at,
                    )
                )
                await db.commit()
    finally:
        await engine.dispose()


async def test_ocr_health_is_available_to_logged_in_users_and_returns_all_methods(
    test_client,
    standard_user,
    monkeypatch,
):
    async def fake_health(db):
        del db
        return {"rapid_ocr": {"status": "healthy", "message": "ok"}}

    monkeypatch.setattr("yuxi.modules.documents.service.check_all_ocr_health", fake_health)

    response = await test_client.get("/api/system/ocr/health", headers=standard_user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["health"]["rapid_ocr"]["status"] == "healthy"


async def test_admin_can_fetch_config_and_reload_info(test_client, admin_headers):
    config_response = await test_client.get("/api/system/config", headers=admin_headers)
    assert config_response.status_code == 200, config_response.text
    assert isinstance(config_response.json(), dict)

    reload_response = await test_client.post("/api/system/info/reload", headers=admin_headers)
    assert reload_response.status_code == 200, reload_response.text
    reload_payload = reload_response.json()
    assert reload_payload["success"] is True
    assert "data" in reload_payload


async def test_retired_system_config_field_is_hidden_rejected_and_preserved(test_client, admin_headers):
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    retired_field = "retired_test_field"

    try:
        async with session_factory() as db:
            record = await get_option(db, "system_options")
            assert record is not None
            previous_value = deepcopy(record.value)
            previous_updated_by = record.updated_by
            previous_updated_at = record.updated_at
            seeded_value = {**previous_value, retired_field: True}
            await db.execute(update(ConfigOption).where(ConfigOption.key == "system_options").values(value=seeded_value))
            await db.commit()
        await invalidate_option_cache("system_options")

        config_response = await test_client.get("/api/system/config", headers=admin_headers)
        assert config_response.status_code == 200, config_response.text
        config = config_response.json()
        assert retired_field not in config
        assert retired_field not in config["_config_items"]

        single_response = await test_client.post(
            "/api/system/config",
            json={"key": retired_field, "value": False},
            headers=admin_headers,
        )
        assert single_response.status_code == 400
        assert single_response.json()["detail"] == f"未知配置项: {retired_field}"

        batch_response = await test_client.post(
            "/api/system/config/update",
            json={retired_field: False},
            headers=admin_headers,
        )
        assert batch_response.status_code == 400
        assert batch_response.json()["detail"] == f"未知配置字段: {retired_field}"

        previous_model = config["default_model"]
        updated_model = "test-provider:test-model" if previous_model != "test-provider:test-model" else "test-provider:alternate-model"
        update_response = await test_client.post(
            "/api/system/config",
            json={"key": "default_model", "value": updated_model},
            headers=admin_headers,
        )
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["default_model"] == updated_model

        async with session_factory() as db:
            record = await get_option(db, "system_options")
            assert record is not None
            assert record.value["default_model"] == updated_model
            assert record.value[retired_field] is True
    finally:
        if "previous_value" in locals():
            async with session_factory() as db:
                await db.execute(
                    update(ConfigOption)
                    .where(ConfigOption.key == "system_options")
                    .values(
                        value=previous_value,
                        updated_by=previous_updated_by,
                        updated_at=previous_updated_at,
                    )
                )
                await db.commit()
            await invalidate_option_cache("system_options")
        await engine.dispose()


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
