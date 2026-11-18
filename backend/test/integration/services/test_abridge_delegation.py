"""验证 aBridge 平台委托的用途、资源与用户绑定。"""

import jwt
import pytest
from sqlalchemy import select, update

from test.integration.services.test_oidc_provider import scenario  # noqa: F401
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.abridge.client import AbridgeAuth, issue_abridge_delegation
from yuxi.modules.extensions.abridge.options import connection_settings
from yuxi.modules.extensions.abridge.service import personal_devices
from yuxi.modules.extensions.mcp.models import MCPConnection, MCPServer
from yuxi.modules.extensions.mcp.service import available_mcp_servers, get_enabled_mcp_server_slugs
from yuxi.modules.identity.models import User
from yuxi.modules.identity.provider import ProviderError
from yuxi.modules.system.models import ConfigOption

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_origin_configuration_rejects_invalid_http_input_and_normalizes_root(scenario, monkeypatch):  # noqa: F811
    """实际 HTTP 写入拒绝非根地址，数据库回读确认失败不覆盖有效值。"""
    from yuxi.api.routers.system import system
    from yuxi.modules.extensions.abridge.options import abridge_options

    scenario["app"].include_router(system, prefix="/api")
    monkeypatch.setattr(pg_manager, "get_async_session_context", scenario["db"])
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example/")
    async with scenario["db"]() as db:
        await db.run_sync(lambda session: ConfigOption.__table__.create(session.connection()))
        await db.execute(update(User).where(User.id == scenario["id"]).values(role="superadmin"))
        db.add(
            ConfigOption(
                key=abridge_options.key, name=abridge_options.name, params=abridge_options.params, value={"url": "https://old.example"}
            )
        )
        await db.commit()
    headers = {"Authorization": "Bearer " + scenario["token"]}
    for invalid in [
        "not-a-url",
        "ftp://bridge.example",
        "https://user:secret@bridge.example",
        "https://bridge.example/path",
        "https://bridge.example?q=1",
        "https://bridge.example#fragment",
    ]:
        response = await scenario["http"].put(
            "/api/system/config/options/abridge_options", headers=headers, json={"value": {"url": invalid}}
        )
        assert response.status_code == 400
        async with scenario["db"]() as db:
            option = await db.scalar(select(ConfigOption).where(ConfigOption.key == abridge_options.key))
            assert option.value == {"url": "https://old.example"}
    response = await scenario["http"].put(
        "/api/system/config/options/abridge_options", headers=headers, json={"value": {"url": "http://abridge:8765/"}}
    )
    assert response.status_code == 200
    async with scenario["db"]() as db:
        option = await db.scalar(select(ConfigOption).where(ConfigOption.key == abridge_options.key))
        assert option.value == {"url": "http://abridge:8765"}
    settings = await connection_settings()
    assert settings.service_origin + "/mcp" == "http://abridge:8765/mcp"
    assert settings.public_origin == "https://bridge.example"


async def test_delegation_has_resource_and_client_binding_and_rechecks_user(scenario, monkeypatch):  # noqa: F811
    """委托只签给内部指定资源，删除用户后不再签发。"""
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_CLIENT_ID", "abridge")
    monkeypatch.setattr(pg_manager, "get_async_session_context", scenario["db"])
    token = await issue_abridge_delegation("stable-person", "https://bridge.example/mcp")
    claims = jwt.decode(
        token,
        scenario["key"].public_key(),
        algorithms=["RS256"],
        audience="https://bridge.example/mcp",
        issuer=scenario["origin"] + "/api/auth/provider",
    )
    assert claims["sub"] == "stable-person" and claims["purpose"] == "abridge-delegation"
    assert claims["client_id"] == "abridge" and claims["exp"] - claims["iat"] == 60
    wrong = await scenario["http"].get("/api/auth/provider/userinfo", headers={"Authorization": "Bearer " + token})
    assert wrong.status_code == 401 and wrong.json()["error"] == "invalid_token"
    async with scenario["db"]() as db:
        await db.execute(update(User).where(User.id == scenario["id"]).values(is_deleted=1))
        await db.commit()
    with pytest.raises(ProviderError, match="access_denied"):
        await issue_abridge_delegation("stable-person", "https://bridge.example/mcp")


async def test_oidc_alone_does_not_enable_abridge_and_option_override_is_effective(scenario, monkeypatch):  # noqa: F811
    """真实配置回读区分独立 OIDC、部署地址与数据库覆盖，不连接外部设备。"""
    monkeypatch.delenv("ABRIDGE_URL", raising=False)
    monkeypatch.delenv("ABRIDGE_PUBLIC_ORIGIN", raising=False)
    async with scenario["db"]() as db:
        for model in (ConfigOption, MCPServer, MCPConnection):
            await db.run_sync(lambda session, model=model: model.__table__.create(session.connection()))
        server = MCPServer(
            slug="abridge",
            name="aBridge",
            transport="streamable_http",
            url="http://abridge:8765/mcp",
            auth_mode="delegation",
            enabled=1,
            created_by="system",
            updated_by="system",
        )
        option = ConfigOption(key="abridge_options", name="设备管理", value={})
        db.add_all([server, option])
        await db.commit()
        assert "abridge" not in await get_enabled_mcp_server_slugs(db=db)
        assert await available_mcp_servers(db, ["abridge"], user_id=scenario["id"]) == {}

        option.value = {"url": "https://bridge.example"}
        await db.commit()
        assert "abridge" not in await get_enabled_mcp_server_slugs(db=db)
        monkeypatch.setattr(pg_manager, "get_async_session_context", scenario["db"])
        incomplete = await connection_settings()
        assert incomplete.public_origin == "" and not incomplete.configured
        assert (await personal_devices("stable-person"))["state"] == "not_configured"
        monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example")
        assert "abridge" in await get_enabled_mcp_server_slugs(db=db)
        assert list(await available_mcp_servers(db, ["abridge"], user_id=scenario["id"])) == ["abridge"]

        option.value = {"url": ""}
        await db.commit()
        assert "abridge" not in await get_enabled_mcp_server_slugs(db=db)
        monkeypatch.setenv("ABRIDGE_URL", "https://environment.example")
        assert "abridge" in await get_enabled_mcp_server_slugs(db=db)
        with monkeypatch.context() as disabled:
            disabled.setenv("YUXI_OIDC_PROVIDER_ENABLED", "false")
            assert "abridge" not in await get_enabled_mcp_server_slugs(db=db)

        db.add(
            MCPServer(
                slug="ordinary",
                name="Ordinary",
                transport="streamable_http",
                url="https://ordinary.example/mcp",
                auth_mode="headers",
                enabled=1,
                created_by="system",
                updated_by="system",
            )
        )
        option.value = {"url": "https://bridge.example/not-an-origin"}
        await db.commit()
        assert await get_enabled_mcp_server_slugs(db=db) == ["ordinary"]
        assert list(await available_mcp_servers(db, ["abridge", "ordinary"], user_id=scenario["id"])) == ["ordinary"]


async def test_outbound_configuration_reads_database_even_with_stale_option_cache(scenario, monkeypatch):  # noqa: F811
    """缓存失效失败也不能延长旧端点的有效期，已有工具在签发前被拒绝。"""
    import httpx

    from yuxi.modules.system.services import options
    from yuxi.shared.errors import StateConflictError

    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example")
    monkeypatch.setattr(pg_manager, "get_async_session_context", scenario["db"])

    async def stale_cache(key):
        """模拟失效通知丢失后仍保留旧地址。"""
        return {"url": "http://old-bridge:8765"}

    monkeypatch.setattr(options, "_load_cached_value", stale_cache)
    async with scenario["db"]() as db:
        await db.run_sync(lambda session: ConfigOption.__table__.create(session.connection()))
        option = ConfigOption(key="abridge_options", name="设备管理", value={"url": "http://old-bridge:8765"})
        db.add(option)
        await db.commit()
    captured = await connection_settings()
    async with scenario["db"]() as db:
        option = await db.scalar(select(ConfigOption).where(ConfigOption.key == "abridge_options"))
        option.value = {"url": "http://new-bridge:8765"}
        await db.commit()
    assert (await connection_settings()).service_origin == "http://new-bridge:8765"
    request = httpx.Request("POST", captured.service_origin + "/mcp")
    with pytest.raises(StateConflictError, match="地址已修改"):
        await anext(AbridgeAuth("stable-person", captured).async_auth_flow(request))
    assert "Authorization" not in request.headers
