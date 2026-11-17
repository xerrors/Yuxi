"""可选身份提供端的部署配置与授权请求契约。"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel, Field

from yuxi.infrastructure.runtime_settings import get_public_url
from yuxi.modules.identity.oidc import OIDCConfig


@dataclass
class ProviderError(Exception):
    """由 HTTP 入口转换成 OAuth 标准错误。"""

    error: str
    description: str
    status: int = 400


@dataclass
class ProviderConfig:
    """启用后只信任静态登记的单个客户端和独立持久签名密钥。"""

    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    private_key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def from_env(cls):
        """配置缺失或私钥不安全时显式失败，不生成临时身份。"""
        prefix = "YUXI_OIDC_PROVIDER_"
        if os.getenv(prefix + "ENABLED", "false").lower() != "true":
            raise ProviderError("access_denied", "身份提供端未启用", 404)
        issuer = (os.getenv(prefix + "ISSUER") or get_public_url() + "/api/auth/provider").rstrip("/")
        redirect = os.getenv(prefix + "REDIRECT_URI", "")
        secret = os.getenv(prefix + "CLIENT_SECRET", "")
        client_id = os.getenv(prefix + "CLIENT_ID", "")
        if not redirect or len(secret) < 32 or not client_id:
            raise ValueError("OIDC Provider 需要回调地址、客户端 ID 和至少 32 字符的客户端密钥")
        OIDCConfig.validate_url(issuer)
        OIDCConfig.validate_url(redirect)
        if urlsplit(issuer).query or urlsplit(redirect).query:
            raise ValueError("OIDC Provider issuer 与回调不允许查询参数")
        key = serialization.load_pem_private_key(Path(os.environ[prefix + "PRIVATE_KEY_FILE"]).read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("OIDC Provider 必须使用至少 2048 位的独立 RSA 私钥")
        return cls(issuer, client_id, secret, redirect, key)


class AuthorizationRequest(BaseModel):
    """只接收静态客户端的授权码与 S256 PKCE 请求。"""

    client_id: str = Field(min_length=1, max_length=200)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    response_type: str
    scope: str = Field(max_length=200)
    state: str = Field(min_length=1, max_length=1024)
    nonce: str = Field(min_length=1, max_length=256)
    code_challenge: str = Field(min_length=43, max_length=43)
    code_challenge_method: str

    def validate_client(self, config: ProviderConfig):
        """任何失败均在站内返回，不向未经确认的回调跳转。"""
        if self.client_id != config.client_id or self.redirect_uri != config.redirect_uri:
            raise ProviderError("invalid_request", "客户端或回调地址未登记")
        if self.response_type != "code":
            raise ProviderError("unsupported_response_type", "只支持授权码")
        scopes = set(self.scope.split())
        if "openid" not in scopes or scopes - {"openid", "profile"}:
            raise ProviderError("invalid_scope", "仅支持 openid profile")
        if self.code_challenge_method != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", self.code_challenge):
            raise ProviderError("invalid_request", "必须使用 S256 PKCE")


def require_provider_config():
    """启动时校验已启用的身份信任配置，关闭时不需要密钥。"""
    if os.getenv("YUXI_OIDC_PROVIDER_ENABLED", "false").lower() == "true":
        ProviderConfig.from_env()
