from __future__ import annotations
from typing import Any
import httpx
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.fetchers.base import BaseTokenFetcher, fetch_custom_http_token


class CustomHttpTokenFetcher(BaseTokenFetcher):
    """自定义 HTTP 方式获取 Token"""

    async def _fetch_new_token(
        self,
        auth_config: MCPAuthConfig,
        *,
        context_payload: dict[str, Any],
        secret_values: dict[str, Any],
        credential_payload: dict[str, Any],
        token_values: dict[str, Any],
        http_client: httpx.AsyncClient | None,
    ) -> dict[str, Any]:
        token_request = auth_config.token_request or {}
        resolved = await fetch_custom_http_token(
            token_request,
            response_map=token_request.get("response_map"),
            context_payload=context_payload,
            secret_values=secret_values,
            token_values=token_values,
            http_client=http_client,
        )
        if not resolved.get("refresh_token") and credential_payload.get("refresh_token"):
            resolved["refresh_token"] = credential_payload["refresh_token"]
        return resolved


class ClientCredentialsFetcher(CustomHttpTokenFetcher):
    """客户端凭证 (Client Credentials) 方式获取 Token"""
    # NOTE: 当前其底层获取逻辑与 CustomHttpTokenFetcher 相同
    pass
