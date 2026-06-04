from __future__ import annotations

import asyncio
import contextvars
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.crypto import decrypt_credential_blob
from yuxi.services.mcp_auth.template_resolver import resolve_template_value
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer
from yuxi.utils import logger


@dataclass(slots=True)
class AuthContext:
    user_id: str | None = None
    department_id: str | None = None


mcp_auth_context_var: contextvars.ContextVar[AuthContext | None] = contextvars.ContextVar(
    "mcp_auth_context_var", default=None
)

_DEFAULT_TOKEN_RESPONSE_MAP = {
    "access_token": "access_token",
    "refresh_token": "refresh_token",
    "expires_in": "expires_in",
    "expires_at": "expires_at",
    "scope": "scope",
    "token_type": "token_type",
}
_REFRESH_LOCK_WAIT_SECONDS = 1.0
_REFRESH_LOCK_POLL_INTERVAL_SECONDS = 0.05


def _parse_credential_blob(connection: MCPConnection | None) -> dict[str, Any]:
    if connection is None or not connection.credential_blob:
        return {}
    if isinstance(connection.credential_blob, dict):
        return dict(connection.credential_blob)
    decrypted = decrypt_credential_blob(connection.credential_blob)
    if not decrypted:
        return {}
    try:
        return json.loads(decrypted)
    except json.JSONDecodeError:
        return {
            "access_token": decrypted,
            "secrets": {"access_token": decrypted},
        }


def _extract_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current[segment]
            continue
        raise KeyError(path)
    return current


def _base_server_config(server: MCPServer) -> dict[str, Any]:
    config = server.to_mcp_config()
    config.pop("auth_config", None)
    return config


def _context_payload(context: AuthContext) -> dict[str, Any]:
    return {
        "user_id": context.user_id,
        "department_id": context.department_id,
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_token_payload(token_values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(token_values)
    expires_at = normalized.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            # 若无时区信息则默认为 UTC，避免 astimezone() 将其视作本地时区转换
            expires_at = expires_at.replace(tzinfo=UTC)
        normalized["expires_at"] = expires_at.astimezone(UTC).isoformat()
        return normalized
    if isinstance(expires_at, str):
        parsed = _parse_datetime(expires_at)
        if parsed is not None:
            normalized["expires_at"] = parsed.isoformat()
            return normalized
    expires_in = normalized.get("expires_in")
    if isinstance(expires_in, str) and expires_in.isdigit():
        expires_in = int(expires_in)
        normalized["expires_in"] = expires_in
    if isinstance(expires_in, (int, float)):
        normalized["expires_at"] = (datetime.now(tz=UTC) + timedelta(seconds=int(expires_in))).isoformat()
    return normalized


def _is_token_expiring_soon(token_values: dict[str, Any], *, pre_refresh_seconds: int) -> bool:
    expires_at = _parse_datetime(token_values.get("expires_at"))
    if expires_at is None:
        return False
    return expires_at <= datetime.now(tz=UTC) + timedelta(seconds=max(pre_refresh_seconds, 0))


def _merge_injected_entries(
    config: dict[str, Any],
    *,
    inject_target: str,
    inject_entries: list[dict[str, str]],
    context: AuthContext,
    secret_values: dict[str, Any],
    token_values: dict[str, Any],
    access_token: str | None,
) -> dict[str, Any]:
    target_values = dict(config.get(inject_target) or {})
    for entry in inject_entries:
        target_values[entry["name"]] = resolve_template_value(
            entry["value_template"],
            context={
                "user_id": context.user_id,
                "department_id": context.department_id,
            },
            secret=secret_values,
            token=token_values,
            access_token=access_token,
        )
    config[inject_target] = target_values
    return config


async def _load_cached_token(
    *,
    token_cache: Any | None,
    connection_id: int | None,
) -> dict[str, Any] | None:
    if token_cache is None or connection_id is None:
        return None
    try:
        cached = await token_cache.get_access_token(connection_id)
    except Exception as exc:
        logger.warning(f"Failed to load MCP access token cache for connection {connection_id}: {exc}")
        return None
    if not cached:
        return None
    return _normalize_token_payload(cached)


async def _store_cached_token(
    *,
    token_cache: Any | None,
    connection_id: int | None,
    token_payload: dict[str, Any],
) -> None:
    if token_cache is None or connection_id is None:
        return
    try:
        await token_cache.set_access_token(connection_id, token_payload)
    except Exception as exc:
        logger.warning(f"Failed to persist MCP access token cache for connection {connection_id}: {exc}")


async def _acquire_refresh_lock(
    *,
    token_cache: Any | None,
    connection_id: int | None,
) -> bool:
    if token_cache is None or connection_id is None:
        return True
    acquire_method = getattr(token_cache, "acquire_refresh_lock", None)
    if acquire_method is None:
        return True
    try:
        return bool(await acquire_method(connection_id))
    except Exception as exc:
        logger.warning(f"Failed to acquire MCP refresh lock for connection {connection_id}: {exc}")
        return True


async def _release_refresh_lock(
    *,
    token_cache: Any | None,
    connection_id: int | None,
    acquired: bool,
) -> None:
    if not acquired or token_cache is None or connection_id is None:
        return
    release_method = getattr(token_cache, "release_refresh_lock", None)
    if release_method is None:
        return
    try:
        await release_method(connection_id)
    except Exception as exc:
        logger.warning(f"Failed to release MCP refresh lock for connection {connection_id}: {exc}")


async def _wait_for_refreshed_token(
    *,
    token_cache: Any | None,
    connection_id: int | None,
    pre_refresh_seconds: int,
) -> dict[str, Any] | None:
    if token_cache is None or connection_id is None:
        return None

    remaining = _REFRESH_LOCK_WAIT_SECONDS
    while remaining > 0:
        await asyncio.sleep(_REFRESH_LOCK_POLL_INTERVAL_SECONDS)
        cached_token = await _load_cached_token(token_cache=token_cache, connection_id=connection_id)
        if cached_token and not _is_token_expiring_soon(
            cached_token,
            pre_refresh_seconds=pre_refresh_seconds,
        ):
            return cached_token
        remaining -= _REFRESH_LOCK_POLL_INTERVAL_SECONDS
    return None


async def _request_dynamic_token_values(
    auth_config: MCPAuthConfig,
    *,
    context: AuthContext,
    connection: MCPConnection | None,
    secret_values: dict[str, Any],
    credential_payload: dict[str, Any],
    http_client: httpx.AsyncClient | None,
    token_cache: Any | None,
    token_values: dict[str, Any],
) -> dict[str, Any]:
    from yuxi.services.mcp_auth.fetchers.factory import TokenFetcherFactory

    fetcher = TokenFetcherFactory.get_fetcher(auth_config.provider)
    resolved = await fetcher.fetch_token(
        auth_config,
        context_payload={
            "user_id": context.user_id,
            "department_id": context.department_id,
        },
        secret_values=secret_values,
        credential_payload=credential_payload,
        token_values=token_values,
        http_client=http_client,
    )

    await _store_cached_token(
        token_cache=token_cache,
        connection_id=getattr(connection, "id", None),
        token_payload=resolved,
    )
    return resolved


async def _resolve_dynamic_token_values(
    auth_config: MCPAuthConfig,
    *,
    context: AuthContext,
    connection: MCPConnection | None,
    secret_values: dict[str, Any],
    credential_payload: dict[str, Any],
    http_client: httpx.AsyncClient | None,
    token_cache: Any | None,
) -> dict[str, Any]:
    if token_cache is None and connection is not None:
        from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache

        token_cache = RedisTokenCache()

    cached_token = await _load_cached_token(
        token_cache=token_cache,
        connection_id=getattr(connection, "id", None),
    )
    pre_refresh_seconds = auth_config.refresh_policy.pre_refresh_seconds
    if cached_token and not _is_token_expiring_soon(cached_token, pre_refresh_seconds=pre_refresh_seconds):
        return cached_token

    token_values = dict(cached_token or {})
    if not token_values:
        token_values.update(
            {
                key: value
                for key, value in credential_payload.items()
                if key in {"access_token", "refresh_token", "expires_in", "expires_at", "scope", "token_type"}
            }
        )
        token_values = _normalize_token_payload(token_values)
        if token_values.get("access_token") and not _is_token_expiring_soon(
            token_values,
            pre_refresh_seconds=pre_refresh_seconds,
        ):
            return token_values
    connection_id = getattr(connection, "id", None)
    lock_acquired = await _acquire_refresh_lock(token_cache=token_cache, connection_id=connection_id)
    if not lock_acquired:
        refreshed_from_cache = await _wait_for_refreshed_token(
            token_cache=token_cache,
            connection_id=connection_id,
            pre_refresh_seconds=pre_refresh_seconds,
        )
        if refreshed_from_cache:
            return refreshed_from_cache

    try:
        return await _request_dynamic_token_values(
            auth_config,
            context=context,
            connection=connection,
            secret_values=secret_values,
            credential_payload=credential_payload,
            http_client=http_client,
            token_cache=token_cache,
            token_values=token_values,
        )
    finally:
        await _release_refresh_lock(
            token_cache=token_cache,
            connection_id=connection_id,
            acquired=lock_acquired,
        )


async def resolve_runtime_mcp_config(
    server: MCPServer,
    *,
    auth_context: AuthContext,
    connection: MCPConnection | None = None,
    http_client: httpx.AsyncClient | None = None,
    token_cache: Any | None = None,
) -> dict[str, Any]:
    config = _base_server_config(server)
    auth_payload = server.auth_config_json or {}
    if not auth_payload:
        return config

    auth_config = MCPAuthConfig.model_validate(auth_payload)
    inject_entries = [entry.model_dump() for entry in auth_config.inject.entries]
    credential_payload = _parse_credential_blob(connection)
    secret_values = credential_payload.get("secrets") or {}

    if auth_config.provider == "legacy_static":
        return config

    if auth_config.provider in {"bound_secret", "stdio_env"}:
        return _merge_injected_entries(
            config,
            inject_target=auth_config.inject.target,
            inject_entries=inject_entries,
            context=auth_context,
            secret_values=secret_values,
            token_values=credential_payload,
            access_token=None,
        )

    if auth_config.provider in {"custom_http_token", "client_credentials", "authorization_code"}:
        token_values = await _resolve_dynamic_token_values(
            auth_config,
            context=auth_context,
            connection=connection,
            secret_values=secret_values,
            credential_payload=credential_payload,
            http_client=http_client,
            token_cache=token_cache,
        )
        return _merge_injected_entries(
            config,
            inject_target=auth_config.inject.target,
            inject_entries=inject_entries,
            context=auth_context,
            secret_values=secret_values,
            token_values=token_values,
            access_token=token_values.get("access_token"),
        )

    raise ValueError(f"Unsupported MCP auth provider: {auth_config.provider}")
