from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from yuxi.services.run_queue_service import get_redis_client
from yuxi.utils import logger

ACCESS_TOKEN_KEY_PREFIX = "yuxi:mcp:access_token:v1"
REFRESH_LOCK_KEY_PREFIX = "yuxi:mcp:refresh_lock:v1"
DEFAULT_TOKEN_TTL_SECONDS = 300
DEFAULT_LOCK_TTL_SECONDS = 30


_PYTEST_SESSION_TOKEN = uuid.uuid4().hex[:8]


def _access_token_key(connection_id: int) -> str:
    key = f"{ACCESS_TOKEN_KEY_PREFIX}:{connection_id}"
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return f"test:{_PYTEST_SESSION_TOKEN}:{key}"
    return key


def _refresh_lock_key(connection_id: int) -> str:
    key = f"{REFRESH_LOCK_KEY_PREFIX}:{connection_id}"
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return f"test:{_PYTEST_SESSION_TOKEN}:{key}"
    return key


def _compute_token_ttl_seconds(token_payload: dict[str, Any]) -> int:
    expires_at = token_payload.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at_dt = datetime.fromisoformat(expires_at)
            if expires_at_dt.tzinfo is None:
                expires_at_dt = expires_at_dt.replace(tzinfo=UTC)
            ttl = int((expires_at_dt - datetime.now(tz=UTC)).total_seconds())
            return max(ttl, 1)
        except ValueError:
            logger.warning(f"Invalid expires_at in MCP token payload: {expires_at}")
    expires_in = token_payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and int(expires_in) > 0:
        return int(expires_in)
    return DEFAULT_TOKEN_TTL_SECONDS


class RedisTokenCache:
    def __init__(self, redis_client_factory: Callable[[], Awaitable[Any]] | None = None):
        self._redis_client_factory = redis_client_factory or get_redis_client

    async def _get_redis(self):
        return await self._redis_client_factory()

    async def get_access_token(self, connection_id: int) -> dict[str, Any] | None:
        redis = await self._get_redis()
        raw = await redis.get(_access_token_key(connection_id))
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)

    async def set_access_token(self, connection_id: int, token_payload: dict[str, Any]) -> None:
        redis = await self._get_redis()
        ttl_seconds = _compute_token_ttl_seconds(token_payload)
        await redis.set(
            _access_token_key(connection_id),
            json.dumps(token_payload, ensure_ascii=False, separators=(",", ":")),
            ex=ttl_seconds,
        )

    async def delete_access_token(self, connection_id: int) -> None:
        redis = await self._get_redis()
        await redis.delete(_access_token_key(connection_id))

    async def acquire_refresh_lock(self, connection_id: int, *, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> bool:
        redis = await self._get_redis()
        acquired = await redis.set(_refresh_lock_key(connection_id), "1", ex=ttl_seconds, nx=True)
        return bool(acquired)

    async def release_refresh_lock(self, connection_id: int) -> None:
        redis = await self._get_redis()
        await redis.delete(_refresh_lock_key(connection_id))
