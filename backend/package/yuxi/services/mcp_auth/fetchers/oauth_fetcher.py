from __future__ import annotations
from typing import Any
import httpx
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.fetchers.base import ITokenFetcher, fetch_custom_http_token, _DEFAULT_TOKEN_RESPONSE_MAP


class AuthorizationCodeFetcher(ITokenFetcher):
    """授权码 (Authorization Code) 模式下的后台 Token 刷新获取"""

    async def _resolve_token_request_config(
        self,
        token_request: dict[str, Any],
        secret_values: dict[str, Any],
        token_values: dict[str, Any],
        http_client: httpx.AsyncClient,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        issuer_url = (
            token_request.get("issuer_url")
            or secret_values.get("issuer_url")
            or token_values.get("issuer_url")
        )
        if not issuer_url:
            raise ValueError("authorization_code provider requires token_request.issuer_url")
        discovery_url = f"{str(issuer_url).rstrip('/')}/.well-known/openid-configuration"
        response = await http_client.get(discovery_url)
        response.raise_for_status()
        payload = response.json()
        token_endpoint = payload.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("authorization_code provider discovery missing token_endpoint")
        
        return {
            "url": token_endpoint,
            "method": "POST",
            "body_type": "form",
            "headers": {
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "body_template": {
                "grant_type": "refresh_token",
                "refresh_token": "${token.refresh_token}",
                "client_id": token_request.get("client_id", "${secret.client_id}"),
                "client_secret": token_request.get("client_secret", "${secret.client_secret}"),
            },
        }, dict(_DEFAULT_TOKEN_RESPONSE_MAP)

    async def fetch_token(
        self,
        auth_config: MCPAuthConfig,
        *,
        context_payload: dict[str, Any],
        secret_values: dict[str, Any],
        credential_payload: dict[str, Any],
        token_values: dict[str, Any],
        http_client: httpx.AsyncClient | None,
    ) -> dict[str, Any]:
        if http_client is None:
            http_client = httpx.AsyncClient()
            should_close = True
        else:
            should_close = False

        try:
            token_request = auth_config.token_request or {}
            authorization_request, response_map = await self._resolve_token_request_config(
                token_request=token_request,
                secret_values=secret_values,
                token_values=token_values or credential_payload,
                http_client=http_client,
            )
            authorization_token_values = dict(token_values or credential_payload)
            if not authorization_token_values.get("refresh_token") and credential_payload.get("refresh_token"):
                authorization_token_values["refresh_token"] = credential_payload["refresh_token"]
            
            resolved = await fetch_custom_http_token(
                authorization_request,
                response_map=response_map,
                context_payload=context_payload,
                secret_values=secret_values,
                token_values=authorization_token_values,
                http_client=http_client,
            )
            if not resolved.get("refresh_token") and authorization_token_values.get("refresh_token"):
                resolved["refresh_token"] = authorization_token_values["refresh_token"]
            return resolved
        finally:
            if should_close:
                await http_client.aclose()
