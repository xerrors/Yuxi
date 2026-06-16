from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.template_resolver import resolve_template_value

# 注释必须使用简体中文，符合 RULE[user_global]
# NOTE: 所有获取 Token 的具体策略需要继承 ITokenFetcher 并实现 fetch_token 方法。

_DEFAULT_TOKEN_RESPONSE_MAP = {
    "access_token": "access_token",
    "refresh_token": "refresh_token",
    "expires_in": "expires_in",
    "expires_at": "expires_at",
    "scope": "scope",
    "token_type": "token_type",
}


def extract_path(payload: dict[str, Any], path: str) -> Any:
    """从 payload 中根据点分路径提取字段值"""
    current: Any = payload
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current[segment]
            continue
        raise KeyError(path)
    return current


async def fetch_custom_http_token(
    request_config: dict[str, Any],
    *,
    response_map: dict[str, str] | None,
    context_payload: dict[str, Any],
    secret_values: dict[str, Any],
    token_values: dict[str, Any],
    http_client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    """执行自定义 HTTP 请求获取 Token"""
    from yuxi.services.mcp_auth.orchestrator import _normalize_token_payload

    response_map = response_map or dict(_DEFAULT_TOKEN_RESPONSE_MAP)
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0))
        should_close = True
    else:
        should_close = False

    try:
        headers = resolve_template_value(
            request_config.get("headers") or {},
            context=context_payload,
            secret=secret_values,
            token=token_values,
            access_token=token_values.get("access_token"),
        )
        body = resolve_template_value(
            request_config.get("body_template") or {},
            context=context_payload,
            secret=secret_values,
            token=token_values,
            access_token=token_values.get("access_token"),
        )
        body_type = request_config.get("body_type", "json")
        request_kwargs: dict[str, Any] = {
            "method": (request_config.get("method") or "POST").upper(),
            "url": request_config["url"],
            "headers": headers,
        }
        if body_type == "json":
            request_kwargs["json"] = body
        else:
            request_kwargs["data"] = body

        request_kwargs["timeout"] = httpx.Timeout(10.0, read=30.0)

        response = await http_client.request(**request_kwargs)
        response.raise_for_status()
        payload = response.json()
        resolved = {}
        for field_name, path in response_map.items():
            try:
                resolved[field_name] = extract_path(payload, path)
            except KeyError:
                continue
        return _normalize_token_payload(resolved)
    except Exception as exc:
        import traceback

        from yuxi.utils import logger

        logger.error(f"fetch_custom_http_token failure: {exc}, traceback: {traceback.format_exc()}")
        raise
    finally:
        if should_close:
            await http_client.aclose()


class ITokenFetcher(ABC):
    """Token 获取接口"""

    @abstractmethod
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
        """
        获取或刷新 Access Token
        """
        pass


class BaseTokenFetcher(ITokenFetcher, ABC):
    """带自动 Refresh 逻辑的 Token 获取基类"""

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
        # NOTE: 优先检查是否有可用 refresh token，并进行刷新
        token_request = auth_config.token_request or {}
        refresh_request = token_request.get("refresh")
        if (
            token_values
            and refresh_request
            and (token_values.get("refresh_token") or credential_payload.get("refresh_token"))
        ):
            refresh_token_values = dict(token_values)
            if not refresh_token_values.get("refresh_token") and credential_payload.get("refresh_token"):
                refresh_token_values["refresh_token"] = credential_payload["refresh_token"]

            refreshed = await fetch_custom_http_token(
                refresh_request,
                response_map=(refresh_request.get("response_map") or token_request.get("response_map")),
                context_payload=context_payload,
                secret_values=secret_values,
                token_values=refresh_token_values,
                http_client=http_client,
            )
            if not refreshed.get("refresh_token") and refresh_token_values.get("refresh_token"):
                refreshed["refresh_token"] = refresh_token_values["refresh_token"]
            return refreshed

        # NOTE: 如果不满足刷新条件，则获取全新 Token
        return await self._fetch_new_token(
            auth_config,
            context_payload=context_payload,
            secret_values=secret_values,
            credential_payload=credential_payload,
            token_values=token_values,
            http_client=http_client,
        )

    @abstractmethod
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
        """获取全新的 Token"""
        pass
