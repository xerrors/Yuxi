"""API startup/readiness 组件与补偿清理测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

import yuxi.api.lifespan as lifespan_module
import yuxi.bootstrap.api as api_bootstrap

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def shared_instance(monkeypatch):
    """默认启用知识库时，API 测试也提供显式共享身份。"""
    monkeypatch.setenv("YUXI_INSTANCE_ID", "api-startup-test")


async def test_optional_startup_component_failure_is_structured_without_raw_message() -> None:
    app = FastAPI()
    app.state.startup_components = {}

    async def fail() -> None:
        raise RuntimeError("database-password-must-not-leak")

    await api_bootstrap._initialize_startup_component(
        app,
        name="builtin_mcp_servers",
        required=False,
        operation=fail,
    )

    assert app.state.startup_components == {"builtin_mcp_servers": {"status": "error", "required": False, "code": "RuntimeError"}}
    assert "password" not in str(app.state.startup_components)


async def test_invalid_security_secrets_fail_before_database_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "false")
    monkeypatch.delenv("API_KEY_DERIVATION_SECRET", raising=False)

    def forbidden_initialize() -> None:
        raise AssertionError("database startup must not begin")

    monkeypatch.setattr(lifespan_module.pg_manager, "initialize", forbidden_initialize)
    app = FastAPI()

    with pytest.raises(
        api_bootstrap.RequiredStartupComponentError,
        match="component=security_secrets, type=ValueError",
    ):
        await lifespan_module.startup(app)

    assert app.state.startup_components == {"security_secrets": {"status": "error", "required": True, "code": "ValueError"}}


async def test_api_startup_validates_full_schema_without_running_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "schema-test-api-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("JWT_SECRET_KEY", "schema-test-jwt-secret-at-least-thirty-two-characters")
    monkeypatch.setattr(lifespan_module.pg_manager, "initialize", lambda: calls.append("initialize"))

    async def require_current_schema(_manager) -> None:
        calls.append("require_current_schema")
        raise RuntimeError("stop after schema assertion")

    monkeypatch.setattr(api_bootstrap, "require_current_schema", require_current_schema)

    with pytest.raises(RuntimeError, match="stop after schema assertion"):
        await lifespan_module.startup(FastAPI())

    assert calls == ["initialize", "require_current_schema"]


async def test_required_startup_component_failure_still_releases_every_runtime_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[str] = []

    async def fail_startup(app: FastAPI) -> None:
        async def fail() -> None:
            raise RuntimeError("database-password-must-not-leak")

        await api_bootstrap._initialize_startup_component(
            app,
            name="default_agents",
            required=True,
            operation=fail,
        )

    async def record_shutdown(name: str, operation) -> None:
        del operation
        released.append(name)

    monkeypatch.setattr(lifespan_module, "startup", fail_startup)
    monkeypatch.setattr(lifespan_module, "shutdown_component", record_shutdown)

    app = FastAPI()
    with pytest.raises(
        api_bootstrap.RequiredStartupComponentError,
        match="component=default_agents, type=RuntimeError",
    ) as exc_info:
        async with lifespan_module.lifespan(app):
            raise AssertionError("startup failure must prevent yield")

    assert app.state.startup_components == {"default_agents": {"status": "error", "required": True, "code": "RuntimeError"}}
    assert "password" not in str(exc_info.value)
    assert released == ["queue_clients", "neo4j", "postgres"]


async def test_invalid_oidc_configuration_blocks_startup_before_database(monkeypatch):
    """延迟配置读取仍在启动阶段拒绝不完整的 OIDC 信任根。"""
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "fixture-api-key-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-jwt-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "")

    def forbidden_initialize():
        """无效配置不能进入数据库装配。"""
        raise AssertionError("database startup must not begin")

    monkeypatch.setattr(api_bootstrap.pg_manager, "initialize", forbidden_initialize)
    app = FastAPI()
    with pytest.raises(api_bootstrap.RequiredStartupComponentError, match="component=oidc_config, type=ValueError"):
        await api_bootstrap.startup(app)
    assert app.state.startup_complete is False
    assert app.state.startup_components["oidc_config"] == {"status": "error", "required": True, "code": "ValueError"}


async def test_disabled_knowledge_does_not_initialize_existing_backends(monkeypatch):
    """关闭部署跳过已有知识库连接，但仍完成其余必需组件装配。"""
    from types import SimpleNamespace

    from yuxi.modules.system.services import options

    initialized = []

    async def initialize(app, *, name, required, operation):
        """只执行装配选择，不取得本测试无关的真实资源。"""
        if name == "knowledge_base":
            raise AssertionError("关闭知识库后不应初始化已有知识库后端")
        initialized.append(name)
        app.state.startup_components[name] = {"status": "ok", "required": required}

    async def done(*args):
        """替换本测试不涉及的共享依赖。"""

    async def redis():
        """提供预热 Redis 的最小协议。"""
        return SimpleNamespace(ping=done)

    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "false")
    monkeypatch.setattr(api_bootstrap, "_initialize_startup_component", initialize)
    monkeypatch.setattr(api_bootstrap.pg_manager, "initialize", lambda: None)
    monkeypatch.setattr(api_bootstrap, "require_current_schema", done)
    monkeypatch.setattr(options, "invalidate_option_cache", done)
    monkeypatch.setattr(api_bootstrap, "get_redis_client", redis)
    app = FastAPI()

    await api_bootstrap.startup(app)

    assert app.state.startup_complete is True
    assert app.state.startup_components["knowledge_base"] == {"status": "disabled", "required": False}
    assert initialized == ["security_secrets", "oidc_config", "oidc_provider_config", "model_cache", "sandbox_provider"]


@pytest.mark.parametrize("process", ["api", "worker"])
async def test_invalid_oidc_provider_blocks_startup_before_database(monkeypatch, process):
    """已启用的提供端缺少强客户端凭证时，API 与 worker 都不能接流量。"""
    import yuxi.bootstrap.worker as worker_bootstrap

    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "false")
    monkeypatch.setenv("OIDC_ENABLED", "false")
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "fixture-api-key-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("JWT_SECRET_KEY", "fixture-jwt-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("YUXI_PUBLIC_URL", "https://yuxi.example")
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_CLIENT_SECRET", "short")

    def forbidden_initialize():
        """未通过身份信任配置校验前，不得取得数据库资源。"""
        raise AssertionError("database startup must not begin")

    monkeypatch.setattr(api_bootstrap.pg_manager, "initialize", forbidden_initialize)
    if process == "api":
        app = FastAPI()
        with pytest.raises(api_bootstrap.RequiredStartupComponentError, match="component=oidc_provider_config, type=ValueError"):
            await api_bootstrap.startup(app)
        assert app.state.startup_complete is False
        assert app.state.startup_components["oidc_provider_config"] == {"status": "error", "required": True, "code": "ValueError"}
    else:
        with pytest.raises(ValueError, match="客户端"):
            await worker_bootstrap.startup({})
