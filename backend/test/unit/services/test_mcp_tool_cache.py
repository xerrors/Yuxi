from __future__ import annotations

import pytest

from yuxi.services.mcp_tool_cache import RedisMcpToolCache


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expire_calls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expire_calls[key] = ex

    async def incr(self, key: str) -> int:
        next_value = int(self.data.get(key) or "0") + 1
        self.data[key] = str(next_value)
        return next_value


@pytest.mark.asyncio
@pytest.mark.unit
async def test_redis_mcp_tool_cache_revision_and_manifest_roundtrip():
    fake_redis = _FakeRedis()

    async def fake_redis_factory():
        return fake_redis

    cache = RedisMcpToolCache(redis_client_factory=fake_redis_factory)

    assert await cache.get_server_revision("demo") == 0
    assert await cache.get_partition_revision("demo", "connection:7") == 0

    assert await cache.bump_server_revision("demo") == 1
    assert await cache.bump_partition_revision("demo", "connection:7") == 1

    assert await cache.get_server_revision("demo") == 1
    assert await cache.get_partition_revision("demo", "connection:7") == 1

    manifest = {
        "server_name": "demo",
        "cache_partition": "connection:7",
        "cache_key": "demo:connection:7:s1:p1:abc123",
        "tools": [
            {
                "name": "alpha_tool",
                "id": "mcp__demo__alphaTool",
                "description": "alpha",
                "parameters": {"city": {"type": "string"}},
                "required": ["city"],
            }
        ],
    }
    await cache.set_manifest("demo:connection:7:s1:p1:abc123", manifest)

    assert await cache.get_manifest("demo:connection:7:s1:p1:abc123") == manifest
