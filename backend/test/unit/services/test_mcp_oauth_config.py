"""OAuth 信任根、凭据边界与管理员配置回归。"""

from types import SimpleNamespace
import json

import pytest
from cryptography.fernet import Fernet

from yuxi.modules.extensions.mcp.oauth_config import MCPOAuthConfig, configure_mcp_auth, decrypt, encrypt, redirect_uri, validate_url
from yuxi.shared.errors import StateConflictError


def test_missing_or_wrong_encryption_key_rejects_credentials(monkeypatch):
    """缺密钥不能保存明文，换密钥后也不能伪装为已连接。"""
    monkeypatch.delenv("YUXI_ENCRYPTION_KEY", raising=False)
    with pytest.raises(StateConflictError, match="加密密钥"):
        encrypt({"access_token": "secret"})
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encoded = encrypt({"access_token": "secret"})
    assert "secret" not in encoded
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(StateConflictError, match="重新连接"):
        decrypt(encoded)


def test_credentials_use_common_encryption_key(monkeypatch):
    """凭据使用通用部署密钥加密，独立解密结果包含完整载荷。"""
    key = Fernet.generate_key()
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", key.decode())
    credentials = {"access_token": "token", "refresh_token": "refresh"}
    encoded = encrypt(credentials)
    assert json.loads(Fernet(key).decrypt(encoded.encode())) == credentials
    assert decrypt(encoded) == credentials


@pytest.mark.parametrize("value", ["", "invalid", "非密钥"])
def test_invalid_common_encryption_key_does_not_fall_back_to_jwt(monkeypatch, value):
    """通用凭据密钥缺失或非法时，不使用可用的 JWT 密钥代替。"""
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", value)
    monkeypatch.setenv("JWT_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(StateConflictError) as error:
        encrypt({"access_token": "secret"})
    assert error.value.code == "mcp_oauth_unconfigured"


@pytest.mark.parametrize(
    "url",
    [
        "http://sso.example/issuer",
        "https://user:secret@sso.example/issuer",
        "https://sso.example/#fragment",
        "https://sso.example/with space",
        "https://sso.example:abc/issuer",
        "https://sso.example:65536/issuer",
    ],
)
def test_oauth_url_boundary_rejects_untrusted_urls(monkeypatch, url):
    """来源、协议和 URL 结构在配置及出站消费处都校验。"""
    monkeypatch.delenv("MCP_OAUTH_LAB_HTTP_ORIGINS", raising=False)
    with pytest.raises(ValueError):
        validate_url(url)
    with pytest.raises(ValueError):
        MCPOAuthConfig(issuer_url=url, client_id="fixture")


def test_lab_http_allowance_is_exact_origin_and_callback_is_fixed(monkeypatch):
    """实验 HTTP 不能放开另一个来源或任意回调路径。"""
    monkeypatch.setenv("MCP_OAUTH_LAB_HTTP_ORIGINS", "http://sso.example:39091")
    validate_url("http://sso.example:39091/issuer")
    with pytest.raises(ValueError):
        validate_url("http://sso.example:39092/issuer")
    monkeypatch.setenv("YUXI_PUBLIC_URL", "https://platform.example/evil")
    with pytest.raises(ValueError):
        redirect_uri()
    monkeypatch.setenv("YUXI_PUBLIC_URL", "https://platform.example/")
    assert redirect_uri() == "https://platform.example/api/system/mcp-servers/oauth/callback"
    with pytest.raises(ValueError):
        validate_url("https://evil.example/token", origins={"https://sso.example"})


def test_removed_enterprise_policy_is_rejected():
    """配置边界拒绝已移除的企业策略，避免产生无效安全承诺。"""
    with pytest.raises(ValueError, match="Extra inputs"):
        MCPOAuthConfig(issuer_url="https://sso.example", client_id="fixture", require_same_identity=True)


def test_oauth_configuration_rejects_shared_authorization_and_does_not_reuse_other_provider_secret(monkeypatch):
    """切换提供商不能把旧客户端 secret 发往新授权服务器。"""
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    server = SimpleNamespace(
        url="https://mcp.example/mcp",
        transport="streamable_http",
        headers={"Authorization": "Bearer admin"},
        auth_mode="headers",
        oauth_config=None,
        oauth_secret=None,
        oauth_registration=None,
    )
    config = {"issuer_url": "https://sso.example", "client_id": "first", "client_secret": "private-secret"}
    with pytest.raises(ValueError, match="公共请求头不能包含所选方式的认证头"):
        configure_mcp_auth(server, "oauth", config)
    server.headers = {}
    configure_mcp_auth(server, "oauth", config)
    assert server.oauth_config.get("client_secret") is None
    assert decrypt(server.oauth_secret) == {"client_secret": "private-secret"}
    configure_mcp_auth(server, "oauth", {"issuer_url": "https://other.example", "client_id": "second"})
    assert server.oauth_secret is None and server.oauth_registration is None


@pytest.mark.parametrize(
    "changes",
    [
        {"token_type": "mac"},
        {"access_token": "bad\r\nheader"},
        {"expires_in": -1},
        {"expires_in": "3600"},
    ],
)
def test_invalid_token_response_cannot_be_saved_as_connected(changes):
    """远端 token 响应不能注入 HTTP header 或伪造有效授权。"""
    from yuxi.modules.extensions.mcp.oauth_service import MCPOAuthService
    from yuxi.shared.errors import UpstreamError

    connection = SimpleNamespace(status="disconnected", credentials=None)
    with pytest.raises(UpstreamError):
        MCPOAuthService.store_tokens(connection, {"access_token": "valid", "token_type": "Bearer", "expires_in": 3600} | changes, {}, {})
    assert connection.status == "disconnected" and connection.credentials is None


@pytest.mark.parametrize("environment", ["prod", "production"])
def test_production_rejects_http_even_when_lab_origins_are_present(monkeypatch, environment):
    """实验来源列表不能放开生产环境明文授权。"""
    monkeypatch.setenv("YUXI_ENV", environment)
    monkeypatch.setenv("MCP_OAUTH_LAB_HTTP_ORIGINS", "http://sso.example")
    with pytest.raises(ValueError):
        validate_url("http://sso.example/issuer")


def test_nullable_api_key_header_preserves_existing_oauth_revision():
    """新增空字段不能使业务库 v1 保存的 OAuth 修订失效。"""
    from yuxi.modules.extensions.mcp.models import MCPServer
    from yuxi.modules.extensions.mcp.oauth_config import config_revision

    server = MCPServer(
        url="https://example.com/mcp",
        transport="streamable_http",
        headers={},
        auth_mode="oauth",
        oauth_config={"issuer_url": "https://sso.example", "client_id": "fixture"},
        oauth_secret=None,
    )
    assert config_revision(server) == "1d3730d3b3fe9ede84c0c02f29ea197489f1bb8c143fec1065f5cb2c445e28f7"
