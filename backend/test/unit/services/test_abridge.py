"""验证 aBridge 凭据用途、地址修订与初始化产物。"""

import hashlib
import importlib.util
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from yuxi.modules.agents.repositories.sandbox import RegisteredSandbox
from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.extensions.mcp.runtime import MCPUnavailableError
from yuxi.modules.extensions.mcp.service import runtime_mcp_failure
from yuxi.modules.extensions.plugins.abridge import browser_enrollment, client, managed_browser
from yuxi.modules.extensions.plugins.abridge.options import AbridgeConnection, abridge_options
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import PermissionDeniedError, StateConflictError, UpstreamError


def test_options_expose_service_address_and_default_off_opt_in(monkeypatch):
    """地址覆盖保持原有语义，托管浏览器开关默认关闭，旧凭据不再暴露。"""
    monkeypatch.setenv("ABRIDGE_URL", "http://abridge:8765")
    monkeypatch.setenv("ABRIDGE_ADMIN_TOKEN", "environment-admin")
    assert abridge_options.resolve({"admin_token": "retired-secret"}) == {
        "url": "http://abridge:8765",
        "allow_shadow_managed_browser": False,
    }
    assert abridge_options.resolve({"url": "https://bridge.example", "allow_shadow_managed_browser": True}) == {
        "url": "https://bridge.example",
        "allow_shadow_managed_browser": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "not-a-url",
        "ftp://bridge.example",
        "https://user:secret@bridge.example",
        "https://bridge.example/path",
        "https://bridge.example?q=1",
        "https://bridge.example#fragment",
    ],
)
async def test_invalid_origin_is_rejected_before_outbound(monkeypatch, origin):
    """环境地址与数据库旧值均不能绕过实际出站边界。"""
    from yuxi.modules.extensions.plugins.abridge.options import connection_settings

    async def stored(self, db=None):
        """模拟数据库已保存的地址。"""
        return {"url": origin}

    monkeypatch.setattr(type(abridge_options), "get", stored)
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example")
    with pytest.raises(StateConflictError, match="地址配置无效"):
        await connection_settings(db=object())


@pytest.mark.asyncio
async def test_connection_normalizes_root_slashes_and_checks_public_origin(monkeypatch):
    """合法根地址不会产生双斜杠，公开地址同样必须是 origin。"""
    from yuxi.modules.extensions.plugins.abridge.options import connection_settings

    async def stored(self, db=None):
        """提供含尾斜杠的服务根地址。"""
        return {"url": "http://abridge:8765/", "allow_shadow_managed_browser": False}

    monkeypatch.setattr(type(abridge_options), "get", stored)
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example/")
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_ENABLED", "true")
    settings = await connection_settings(db=object())
    assert settings.configured
    assert settings.service_origin + "/mcp" == "http://abridge:8765/mcp"
    assert settings.public_origin == "https://bridge.example"
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example/path")
    with pytest.raises(StateConflictError, match="地址配置无效"):
        await connection_settings(db=object())


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_auth_does_not_send_delegation_to_other_endpoint_or_changed_configuration(monkeypatch, changed):
    """旧对象和非 MCP 请求均在签发前明确拒绝。"""
    captured = AbridgeConnection("https://bridge.example", "https://bridge.example", True)

    async def settings():
        """模拟下一次出站时已提交的地址配置。"""
        return AbridgeConnection("https://replacement.example", captured.public_origin, True) if changed else captured

    monkeypatch.setattr(client, "connection_settings", settings)
    auth = client.AbridgeAuth("actor", captured)
    request = httpx.Request("POST", captured.service_origin + ("/mcp" if changed else "/api/admin/users"))
    with pytest.raises(StateConflictError):
        await anext(auth.async_auth_flow(request))
    assert "Authorization" not in request.headers
    assert "admin-private" not in repr(auth.__dict__)


def test_init_writes_persistent_independent_key_and_preserves_secrets(tmp_path):
    """重复配置保留实际密钥字节与凭据，不触碰其他环境项。"""
    script = Path(__file__).resolve().parents[4] / "scripts/init_abridge.py"
    if not script.is_file():
        pytest.skip("API 镜像未挂载仓库脚本，宿主配置测试负责该契约")
    spec = importlib.util.spec_from_file_location("init_abridge", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env_file = tmp_path / "settings.env"
    key_file = tmp_path / "identity/provider.pem"
    env_file.write_text(
        f"UNRELATED=keep\nYUXI_OIDC_PROVIDER_PRIVATE_KEY_FILE={key_file}\n"
        "ABRIDGE_ADMIN_TOKEN=fixture#hash\nYUXI_OIDC_PROVIDER_CLIENT_SECRET=fixture with spaces\n"
    )
    module.initialize(env_file, "http://localhost:35173", "http://localhost:18765")
    first_env, first_key = env_file.read_bytes(), key_file.read_bytes()
    module.initialize(env_file, "http://localhost:35173", "http://localhost:18765")
    assert env_file.read_bytes() == first_env
    assert key_file.read_bytes() == first_key
    assert serialization.load_pem_private_key(first_key, password=None).key_size >= 2048
    assert key_file.stat().st_mode & 0o777 == 0o640
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert b"UNRELATED=keep" in first_env
    from dotenv import dotenv_values

    values = dotenv_values(env_file, interpolate=False)
    assert values["ABRIDGE_ADMIN_TOKEN"] == "fixture#hash"
    assert values["YUXI_OIDC_PROVIDER_CLIENT_SECRET"] == "fixture with spaces"


@pytest.mark.parametrize("last_seen", [None, 1730000000, 1730000000.125])
def test_device_last_seen_preserves_numeric_timestamp(last_seen):
    """设备心跳时间支持整数和小数秒，不截断远端精度。"""
    from yuxi.modules.extensions.plugins.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "online", "last_seen": last_seen}
    result = DeviceList.model_validate({"devices": [device]}).model_dump()
    assert result["devices"][0]["last_seen"] == last_seen


@pytest.mark.parametrize("platform", [None, "macOS", "Windows 11", "Android 15", "Browser", "Linux"])
def test_device_platform_is_preserved_for_display(platform):
    """平台来自远端设备，不因展示投影丢失或限制未知系统。"""
    from yuxi.modules.extensions.plugins.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "online", "platform": platform}
    result = DeviceList.model_validate({"devices": [device]}).model_dump()
    assert result["devices"][0]["platform"] == platform


@pytest.mark.parametrize("field,value", [("paused", "false"), ("name", None), ("id", 123), ("last_seen", "1730000000.125")])
def test_device_wire_values_are_not_coerced(field, value):
    """远端类型错误不得被转换成貌似有效的设备状态。"""
    from pydantic import ValidationError

    from yuxi.modules.extensions.plugins.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "offline", "paused": False}
    with pytest.raises(ValidationError):
        DeviceList.model_validate({"devices": [device | {field: value}]})


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "key_issued"), [("yes", True), (True, "on")])
async def test_read_binding_rejects_malformed_protocol_flags(monkeypatch, enabled, key_issued):
    """上游协议中的非布尔值不能确认部署可用或凭据已兑换。"""
    origin = "https://bridge.example"
    binding = {"installation_id": "vm", "revision": 1, "device_id": "browser", "status": "online", "key_issued": key_issued}
    reply = {"version": 2, "enabled": enabled, "browser_origin": origin, "binding": binding}
    monkeypatch.setattr(browser_enrollment, "_request", AsyncMock(return_value=reply))
    with pytest.raises(UpstreamError) as rejected:
        await browser_enrollment.read_binding(ActorScope(uid="user", app_id=None), AbridgeConnection(origin, origin, True))
    assert rejected.value.code == "abridge_managed_browser_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("public_origin,enabled", [("", True), ("https://bridge.example", False)])
async def test_incomplete_configuration_blocks_direct_connection_and_existing_auth(monkeypatch, public_origin, enabled):
    """公开来源缺失或关闭签发时，直接连接与已有认证对象都不能继续出站。"""
    captured = AbridgeConnection("http://abridge:8765", "https://bridge.example", True)

    async def settings():
        """返回出站时的部署状态。"""
        return AbridgeConnection(captured.service_origin, public_origin, enabled)

    monkeypatch.setattr(client, "connection_settings", settings)
    with pytest.raises(StateConflictError, match="未配置"):
        await client.abridge_mcp_connection(uid="actor")
    request = httpx.Request("POST", captured.service_origin + "/mcp")
    with pytest.raises(StateConflictError, match="未配置"):
        await anext(client.AbridgeAuth("actor", captured).async_auth_flow(request))
    assert "Authorization" not in request.headers


def test_managed_browser_http_requires_deployment_opt_in(monkeypatch):
    """只对部署明确配置的公开来源开启 HTTP。"""
    from yuxi.modules.extensions.plugins.abridge.managed_browser import _managed_origin_allowed

    http_origin = AbridgeConnection("http://abridge:8765", "http://bridge.example", True)
    https_origin = AbridgeConnection("http://abridge:8765", "https://bridge.example", True)
    monkeypatch.delenv("ABRIDGE_ALLOW_INSECURE_HTTP", raising=False)
    assert _managed_origin_allowed(https_origin)
    assert not _managed_origin_allowed(http_origin)
    monkeypatch.setenv("ABRIDGE_ALLOW_INSECURE_HTTP", "true")
    assert _managed_origin_allowed(http_origin)


@pytest.mark.asyncio
@pytest.mark.parametrize(("exit_code", "payload"), [(1, b'{"status":"running"}'), (0, b"invalid"), (0, b'{"status":"running"}')])
async def test_browser_client_requires_a_successful_exit_and_complete_status(exit_code, payload):
    """进程失败、回执损坏和缺少绑定均不能报告已连接。"""

    def run_guest_program(uid, generation, *, command, **options):
        return (0, b"") if command == "/usr/bin/test" else (exit_code, payload)

    with pytest.raises(UpstreamError) as failure:
        await managed_browser.browser_status(SimpleNamespace(run_guest_program=run_guest_program), "owner", 1)
    assert failure.value.code == "sandbox_service_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "remote", "expected"),
    [
        ("running", "online", "ready"),
        ("running", "offline", "offline"),
        ("paused", "online", "offline"),
        ("failed", "online", "not_connected"),
    ],
)
async def test_personal_browser_status_reads_registered_device_without_guest_commands(monkeypatch, state, remote, expected):
    """普通刷新回读登记和在线状态，已休眠的 VM 即使还留有上游心跳也显示离线。"""
    settings = AbridgeConnection("http://abridge:8765", "https://bridge.example", True)
    binding = managed_browser.BrowserBinding(
        installation_id=hashlib.sha256(b"existing-vm").hexdigest(),
        revision=1,
        device_id="browser",
        status=remote,
        key_issued=True,
    )

    @asynccontextmanager
    async def database():
        """不使用真实连接，仅替换此测试的外部状态读取。"""
        yield None

    guest = AsyncMock(side_effect=AssertionError("查看状态不调用来宾"))
    monkeypatch.setattr(managed_browser.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(managed_browser, "_require_actor", AsyncMock())
    monkeypatch.setattr(managed_browser, "connection_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(managed_browser, "registered_sandbox", AsyncMock(return_value=RegisteredSandbox(1, "existing-vm", state)))
    monkeypatch.setattr(managed_browser, "get_sandbox_provider", lambda: SimpleNamespace(backend="cube"))
    monkeypatch.setattr(managed_browser, "read_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(managed_browser, "browser_status", guest)

    assert (await managed_browser.personal_browser_status("owner")).status == expected
    guest.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_run_does_not_enter_managed_delegation(monkeypatch):
    """APP 开关关闭后，已有 Run 和 Worker 的个人会话仍直接签普通委托。"""
    settings = AbridgeConnection("http://abridge:8765", "https://bridge.example", True, allow_shadow_managed_browser=True)
    context = BaseContext(
        uid="owner",
        actor_scope=ActorScope(uid="owner", app_id=None),
        run_id="run",
        worker_id="worker",
    )
    monkeypatch.setattr(managed_browser, "managed_run_delegation", AsyncMock(side_effect=AssertionError("个人运行不检查 Run 或 VM")))
    monkeypatch.setattr(
        client,
        "connection_settings",
        AsyncMock(side_effect=[settings, replace(settings, allow_shadow_managed_browser=False)]),
    )
    issue = AsyncMock(return_value="fixture-platform-token")
    monkeypatch.setattr(client, "issue_abridge_delegation", issue)

    config = await client.abridge_mcp_connection(uid=context.uid, context=context)
    request = httpx.Request("POST", config["url"])
    authenticated = await anext(config["auth"].async_auth_flow(request))

    assert authenticated.headers["Authorization"] == "Bearer fixture-platform-token"
    issue.assert_awaited_once_with("owner", settings.public_origin + "/mcp")


@pytest.mark.parametrize(
    ("scope_uid", "run_id", "worker_id"),
    [("other", "run", "worker"), ("actor", None, "worker"), ("actor", "run", None)],
)
@pytest.mark.asyncio
async def test_abridge_rejects_incomplete_app_run_identity(monkeypatch, scope_uid, run_id, worker_id):
    """APP 身份缺少执行绑定时，任何签发路径都不可用。"""
    settings = AbridgeConnection("http://abridge:8765", "https://bridge.example", True)
    context = BaseContext(uid="actor", actor_scope=ActorScope(scope_uid, "app", 1), run_id=run_id, worker_id=worker_id)
    monkeypatch.setattr(client, "connection_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(client, "issue_abridge_delegation", AsyncMock(return_value="unexpected-personal-token"))
    monkeypatch.setattr(managed_browser, "managed_run_delegation", AsyncMock(return_value="unexpected-app-token"))

    config = await client.abridge_mcp_connection(uid=context.uid, context=context)
    request = httpx.Request("POST", config["url"])
    with pytest.raises(PermissionDeniedError, match="执行身份"):
        await anext(config["auth"].async_auth_flow(request))
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    ("failure", "unavailable"),
    [
        (PermissionDeniedError("授权已撤销", code="abridge_managed_browser_revoked"), ("授权已撤销", True)),
        (StateConflictError("设备已离线", code="abridge_managed_browser_offline"), ("设备已离线", False)),
        (PermissionDeniedError("执行租约已失效"), None),
    ],
)
@pytest.mark.asyncio
async def test_abridge_app_delegation_distinguishes_connector_and_execution_failures(monkeypatch, failure, unavailable):
    """设备拒绝和离线保留局部失败语义，执行撤权穿透 MCP 的异常包装。"""
    settings = AbridgeConnection("http://abridge:8765", "https://bridge.example", True)
    context = BaseContext(uid="actor", actor_scope=ActorScope("actor", "app", 1), run_id="run", worker_id="worker")
    monkeypatch.setattr(client, "connection_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(client, "issue_abridge_delegation", AsyncMock(return_value="unexpected-personal-token"))
    monkeypatch.setattr(managed_browser, "managed_run_delegation", AsyncMock(side_effect=failure))

    config = await client.abridge_mcp_connection(uid=context.uid, context=context)
    request = httpx.Request("POST", config["url"])
    with pytest.raises((MCPUnavailableError, PermissionDeniedError)) as rejected:
        await anext(config["auth"].async_auth_flow(request))
    wrapped = ExceptionGroup("MCP HTTP task", [rejected.value])
    if unavailable is None:
        with pytest.raises(PermissionDeniedError, match="租约已失效"):
            runtime_mcp_failure(wrapped)
    else:
        assert runtime_mcp_failure(wrapped) == unavailable
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    ("sandbox_state", "remote_state", "local_binding_present", "lease_lost", "expected_actions"),
    [
        ("running", "offline", True, False, ["resume", "status", "start", "status"]),
        ("running", "online", True, False, []),
        ("paused", "online", True, False, ["resume", "status", "start", "status"]),
        ("paused", "online", False, False, ["resume", "status"]),
        ("paused", "online", True, True, ["resume"]),
    ],
)
@pytest.mark.asyncio
async def test_app_run_recovers_only_the_existing_browser(
    monkeypatch, sandbox_state, remote_state, local_binding_present, lease_lost, expected_actions
):
    """APP 续跑可恢复休眠实例，丢失绑定需用户连接，Worker 不发票据。"""
    settings = AbridgeConnection("http://abridge:8765", "https://bridge.example", True, allow_shadow_managed_browser=True)
    scope = ActorScope(uid="owner", app_id="app", api_key_id=1)
    instance = (3, "existing-vm")
    binding = managed_browser.BrowserBinding(
        installation_id=hashlib.sha256(b"existing-vm").hexdigest(), revision=2, device_id="device", status=remote_state, key_issued=True
    )
    run = SimpleNamespace(
        app_id="app",
        api_key_id=1,
        status="running",
        worker_id="worker",
        lease_expires_at=utc_now() + timedelta(minutes=1),
    )
    actions = []

    @asynccontextmanager
    async def database():
        yield None

    def prepare(uid, *, expected):
        assert uid == "owner" and expected == instance
        actions.append("resume")
        if lease_lost:
            raise StateConflictError("Run 执行租约已失效", code="lease_lost")
        return expected

    def run_guest_program(uid, generation, *, user, command, arguments, timeout, stdin_data=None, touch_idle=False):
        assert uid == "owner" and generation == instance[0]
        if command == "/usr/bin/test":
            return 0, b""
        action, handoff = arguments[-1], stdin_data
        actions.append(action)
        if action == "start":
            assert json.loads(handoff) == {"revision": 2, "device_id": "device", "browser_origin": settings.public_origin}
            binding.status = "online"
        if not local_binding_present:
            return 0, b'{"status":"unbound"}'
        return 0, json.dumps(
            {
                "status": "running" if "start" in actions else "bound",
                "revision": 2,
                "device_id": "device",
                "browser_origin": settings.public_origin,
                "connected": "start" in actions,
            }
        ).encode()

    provider = SimpleNamespace(backend="cube", prepare_instance=prepare, run_guest_program=run_guest_program)
    monkeypatch.setattr(managed_browser.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(managed_browser.AgentRunRepository, "get_run_for_user", AsyncMock(return_value=run))
    monkeypatch.setattr(managed_browser, "_require_actor", AsyncMock())
    monkeypatch.setattr(managed_browser, "connection_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(managed_browser, "registered_sandbox", AsyncMock(return_value=RegisteredSandbox(*instance, sandbox_state)))
    monkeypatch.setattr(managed_browser, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(managed_browser, "read_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(managed_browser, "sign_browser_request", lambda *args, **claims: claims)

    if lease_lost:
        with pytest.raises(PermissionDeniedError, match="租约已失效"):
            await managed_browser.managed_run_delegation(scope, "run", "worker", settings)
    elif local_binding_present:
        token = await managed_browser.managed_run_delegation(scope, "run", "worker", settings)
        assert token == {"installation_id": binding.installation_id, "revision": 2}
    else:
        with pytest.raises(PermissionDeniedError, match="重新连接") as denied:
            await managed_browser.managed_run_delegation(scope, "run", "worker", settings)
        assert denied.value.code == "abridge_managed_browser_binding_missing"
    assert actions == expected_actions
