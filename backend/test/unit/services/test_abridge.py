"""验证 aBridge 凭据用途、地址修订与初始化产物。"""

import importlib.util
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from yuxi.modules.extensions.abridge import client
from yuxi.modules.extensions.abridge.options import AbridgeConnection, abridge_options
from yuxi.shared.errors import StateConflictError


def test_options_only_expose_service_address(monkeypatch):
    """删除的凭据不再进入解析结果，地址覆盖保留原有语义。"""
    monkeypatch.setenv("ABRIDGE_URL", "http://abridge:8765")
    monkeypatch.setenv("ABRIDGE_ADMIN_TOKEN", "environment-admin")
    assert abridge_options.resolve({"admin_token": "retired-secret"}) == {"url": "http://abridge:8765"}
    assert abridge_options.resolve({"url": "https://bridge.example"}) == {"url": "https://bridge.example"}


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
    from yuxi.modules.extensions.abridge.options import connection_settings

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
    from yuxi.modules.extensions.abridge.options import connection_settings

    async def stored(self, db=None):
        """提供含尾斜杠的服务根地址。"""
        return {"url": "http://abridge:8765/"}

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
    from yuxi.modules.extensions.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "online", "last_seen": last_seen}
    result = DeviceList.model_validate({"devices": [device]}).model_dump()
    assert result["devices"][0]["last_seen"] == last_seen


@pytest.mark.parametrize("platform", [None, "macOS", "Windows 11", "Android 15", "Browser", "Linux"])
def test_device_platform_is_preserved_for_display(platform):
    """平台来自远端设备，不因展示投影丢失或限制未知系统。"""
    from yuxi.modules.extensions.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "online", "platform": platform}
    result = DeviceList.model_validate({"devices": [device]}).model_dump()
    assert result["devices"][0]["platform"] == platform


@pytest.mark.parametrize("field,value", [("paused", "false"), ("name", None), ("id", 123), ("last_seen", "1730000000.125")])
def test_device_wire_values_are_not_coerced(field, value):
    """远端类型错误不得被转换成貌似有效的设备状态。"""
    from pydantic import ValidationError

    from yuxi.modules.extensions.abridge.service import DeviceList

    device = {"id": "one", "name": "电脑", "kind": "computer", "status": "offline", "paused": False}
    with pytest.raises(ValidationError):
        DeviceList.model_validate({"devices": [device | {field: value}]})


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
