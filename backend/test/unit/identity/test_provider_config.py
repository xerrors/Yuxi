"""身份提供端只在明确配置完整时启用。"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from yuxi.modules.identity.provider import ProviderConfig, ProviderError, require_provider_config


@pytest.fixture
def configured(monkeypatch, tmp_path):
    """仅用测试临时密钥，不依赖部署凭据。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "provider.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for name, value in {
        "ENABLED": "true",
        "CLIENT_SECRET": "x" * 32,
        "CLIENT_ID": "test-client",
        "ISSUER": "https://yuxi.example/api/auth/provider",
        "REDIRECT_URI": "https://client.example/auth/callback",
        "PRIVATE_KEY_FILE": str(pem),
    }.items():
        monkeypatch.setenv("YUXI_OIDC_PROVIDER_" + name, value)
    return pem


def test_disabled_provider_requires_no_key(configured, monkeypatch):
    """独立 Yuxi 部署不因可选接入缺配置而失败。"""
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_ENABLED", "false")
    monkeypatch.delenv("YUXI_OIDC_PROVIDER_PRIVATE_KEY_FILE")
    require_provider_config()
    with pytest.raises(ProviderError, match="access_denied"):
        ProviderConfig.from_env()


@pytest.mark.parametrize(
    "name,value",
    [
        ("CLIENT_SECRET", "short"),
        ("REDIRECT_URI", ""),
        ("ISSUER", "https://yuxi.example/api/auth/provider?x=1"),
        ("REDIRECT_URI", "https://client.example/callback#fragment"),
        ("REDIRECT_URI", "https://user:secret@bridge.example/callback"),
    ],
)
def test_enabled_provider_rejects_incomplete_or_unsafe_config(configured, monkeypatch, name, value):
    """不能以弱客户端认证或不可信地址启动身份提供端。"""
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_" + name, value)
    with pytest.raises(ValueError):
        require_provider_config()


def test_provider_requires_dedicated_rsa_key(configured):
    """错误算法的密钥不能被公布为 RS256 信任根。"""
    key = ec.generate_private_key(ec.SECP256R1())
    configured.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(ValueError, match="RSA"):
        require_provider_config()


def test_provider_requires_explicit_client_id(configured, monkeypatch):
    """身份提供端不默认绑定任何业务客户端。"""
    monkeypatch.delenv("YUXI_OIDC_PROVIDER_CLIENT_ID")
    with pytest.raises(ValueError, match="客户端 ID"):
        require_provider_config()
