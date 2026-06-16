from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from yuxi.services.run_queue_service import get_redis_client
from yuxi.utils import logger

SERVER_REVISION_KEY_PREFIX = "yuxi:mcp:tool_cache:server_revision:v1"
PARTITION_REVISION_KEY_PREFIX = "yuxi:mcp:tool_cache:partition_revision:v1"
MANIFEST_KEY_PREFIX = "yuxi:mcp:tool_cache:manifest:v1"
MANIFEST_TTL_SECONDS = int(os.getenv("YUXI_MCP_TOOL_MANIFEST_TTL_SECONDS", "3600"))


def _server_revision_key(server_name: str) -> str:
    return f"{SERVER_REVISION_KEY_PREFIX}:{server_name}"


def _partition_revision_key(server_name: str, cache_partition: str) -> str:
    return f"{PARTITION_REVISION_KEY_PREFIX}:{server_name}:{cache_partition}"


def _manifest_key(cache_key: str) -> str:
    return f"{MANIFEST_KEY_PREFIX}:{cache_key}"


class RedisMcpToolCache:
    def __init__(self, redis_client_factory: Callable[[], Awaitable[Any]] | None = None):
        self._redis_client_factory = redis_client_factory or get_redis_client

    async def _get_redis(self):
        return await self._redis_client_factory()

    async def get_server_revision(self, server_name: str) -> int:
        return await self._get_revision(_server_revision_key(server_name))

    async def get_partition_revision(self, server_name: str, cache_partition: str) -> int:
        return await self._get_revision(_partition_revision_key(server_name, cache_partition))

    async def bump_server_revision(self, server_name: str) -> int:
        return await self._bump_revision(_server_revision_key(server_name))

    async def bump_partition_revision(self, server_name: str, cache_partition: str) -> int:
        return await self._bump_revision(_partition_revision_key(server_name, cache_partition))

    async def get_manifest(self, cache_key: str) -> dict[str, Any] | None:
        try:
            redis = await self._get_redis()
            raw = await redis.get(_manifest_key(cache_key))
        except Exception as exc:
            logger.warning(f"Failed to read MCP tool manifest cache for '{cache_key}': {exc}")
            return None
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except Exception as exc:
            logger.warning(f"Failed to decode MCP tool manifest cache for '{cache_key}': {exc}")
            return None

    async def set_manifest(self, cache_key: str, manifest: dict[str, Any]) -> None:
        try:
            redis = await self._get_redis()
            await redis.set(
                _manifest_key(cache_key),
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
                ex=MANIFEST_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning(f"Failed to write MCP tool manifest cache for '{cache_key}': {exc}")

    async def _get_revision(self, key: str) -> int:
        try:
            redis = await self._get_redis()
            raw = await redis.get(key)
        except Exception as exc:
            logger.warning(f"Failed to read MCP tool revision cache for '{key}': {exc}")
            return 0
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(f"Invalid MCP tool revision cache value for '{key}': {raw}")
            return 0

    async def _bump_revision(self, key: str) -> int:
        try:
            redis = await self._get_redis()
            return int(await redis.incr(key))
        except Exception as exc:
            logger.warning(f"Failed to bump MCP tool revision cache for '{key}': {exc}")
            return 0
