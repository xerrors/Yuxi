"""个人 OAuth 配置、部署密钥与配置修订。"""

import hashlib
import json
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuxi.infrastructure.crypto import get_encryption_cipher
from yuxi.infrastructure.runtime_settings import get_public_url
from yuxi.shared.errors import StateConflictError

STATIC_AUTH_MODES = {"bearer", "api_key"}
PERSONAL_AUTH_MODES = STATIC_AUTH_MODES | {"oauth"}


class MCPOAuthConfig(BaseModel):
    """由超管固定授权信任根，远端发现不能扩大允许来源。"""

    model_config = ConfigDict(extra="forbid")
    issuer_url: str = Field(max_length=500)
    registration: Literal["static", "dynamic"] = "static"
    client_id: str = Field(default="", max_length=500)
    client_secret: str | None = Field(default=None, max_length=4096, repr=False)
    scopes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_registration(self):
        """静态注册必须提供客户端标识。"""
        validate_url(self.issuer_url)
        if urlsplit(self.issuer_url).query:
            raise ValueError("OAuth issuer 不得包含查询参数")
        if self.registration == "static" and not self.client_id:
            raise ValueError("预注册 OAuth 需要 client_id")
        return self


def configure_mcp_auth(server, auth_mode: str, oauth_config: dict | None, api_key_header: str | None = None) -> None:
    """声明认证方式，个人秘密不进入共享请求头。"""
    if auth_mode == "delegation":
        raise ValueError("平台委托 MCP 由内置清单同步，不能通过管理接口配置")
    if auth_mode not in {"none", "headers"} | PERSONAL_AUTH_MODES:
        raise ValueError("MCP 认证方式无效")
    if auth_mode == "api_key":
        api_key_header = api_key_header if api_key_header is not None else server.api_key_header
        if (
            not api_key_header
            or len(api_key_header) > 128
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", api_key_header)
            or api_key_header.lower()
            in {
                "host",
                "content-length",
                "transfer-encoding",
                "connection",
                "content-type",
                "accept",
                "mcp-session-id",
                "mcp-protocol-version",
            }
        ):
            raise ValueError("API Key 请求头名称无效")
    else:
        api_key_header = None
    server.auth_mode = auth_mode
    server.api_key_header = api_key_header
    if auth_mode in PERSONAL_AUTH_MODES:
        validate_url(server.url)
    reserved_headers = {"authorization"}
    if api_key_header:
        reserved_headers.add(api_key_header.lower())
    if auth_mode != "headers" and reserved_headers.intersection(key.lower() for key in (server.headers or {})):
        raise ValueError("公共请求头不能包含所选方式的认证头")
    if auth_mode != "oauth":
        server.oauth_config, server.oauth_secret, server.oauth_registration = None, None, None
        return
    if server.transport != "streamable_http":
        raise ValueError("个人 OAuth 仅支持 Streamable HTTP")
    config = MCPOAuthConfig.model_validate(oauth_config if oauth_config is not None else server.oauth_config)
    previous = server.oauth_config or {}
    if any(previous.get(field) != getattr(config, field) for field in ("issuer_url", "client_id", "registration")):
        server.oauth_secret, server.oauth_registration = None, None
    if config.client_secret is not None:
        server.oauth_secret = encrypt({"client_secret": config.client_secret}) if config.client_secret else None
    server.oauth_config = config.model_dump(exclude={"client_secret"})


def validate_url(value: str, *, origins: set[str] | None = None) -> str:
    """只允许 HTTPS 与部署明确允许的实验 HTTP 来源。"""
    parsed = urlsplit(value)
    # urlsplit 延迟校验端口，出站前在同一配置边界拒绝非法值。
    _ = parsed.port
    origin = f"{parsed.scheme}://{parsed.netloc}"
    lab_origins = set(os.getenv("MCP_OAUTH_LAB_HTTP_ORIGINS", "").split(","))
    production = os.getenv("YUXI_ENV", "").lower() in {"prod", "production"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or any(c.isspace() for c in value)
        or parsed.scheme not in {"http", "https"}
        or (parsed.scheme == "http" and (production or origin not in lab_origins))
        or (origins is not None and origin not in origins)
    ):
        raise ValueError("OAuth 地址必须为可信来源的 HTTPS 地址；实验 HTTP 需要部署显式允许")
    return value


def origin(value: str) -> str:
    """提取用于精确来源比较的 scheme 和 authority。"""
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def redirect_uri() -> str:
    """公开站点地址由部署固定，回调路径由代码固定。"""
    value = get_public_url() + "/api/system/mcp-servers/oauth/callback"
    validate_url(value)
    return value


def cipher() -> Fernet:
    """凭据密钥只来自部署；缺失时明确拒绝个人授权。"""
    try:
        return get_encryption_cipher()
    except ValueError as exc:
        raise StateConflictError("连接器个人凭证未配置加密密钥，请联系超级管理员", code="mcp_oauth_unconfigured") from exc


def encrypt(value: dict) -> str:
    """将凭据和短期授权材料封装为认证加密文本。"""
    return cipher().encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def decrypt(value: str) -> dict:
    """拒绝损坏或不属于当前部署的密文，不回显凭据。"""
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError) as exc:
        raise StateConflictError("连接器授权凭据无法读取，请重新连接", code="mcp_reconnect_required") from exc


def config_revision(server) -> str:
    """服务地址、请求头或授权配置改变后不能继续使用旧凭据。"""
    payload = [
        server.url,
        server.transport,
        server.headers,
        server.auth_mode,
        server.oauth_config,
        server.oauth_secret,
    ]
    # 空的新字段保持旧 OAuth 修订不变，升级不要求既有用户重新授权。
    if server.api_key_header is not None:
        payload.append(server.api_key_header)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
