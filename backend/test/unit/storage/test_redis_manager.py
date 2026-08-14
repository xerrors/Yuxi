from __future__ import annotations

import pytest
import yuxi.storage.redis.manager as redis_manager
from yuxi.storage.redis import RedisConfig

pytestmark = pytest.mark.unit


class _FakeSyncRedis:
    def __init__(self, *, ping_error: Exception | None = None):
        self.ping_error = ping_error
        self.ping_calls = 0
        self.closed = False

    def ping(self):
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error

    def close(self):
        self.closed = True


class _FakeAsyncRedis:
    def __init__(self, *, ping_error: Exception | None = None):
        self.ping_error = ping_error
        self.ping_calls = 0
        self.closed = False

    async def ping(self):
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error

    async def aclose(self):
        self.closed = True


def test_create_sync_redis_client_uses_config_and_pings(monkeypatch: pytest.MonkeyPatch):
    redis = pytest.importorskip("redis")
    fake_client = _FakeSyncRedis()
    captured: dict = {}

    def fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_client

    monkeypatch.setattr(redis, "from_url", fake_from_url)

    client = redis_manager.create_sync_redis_client(
        RedisConfig(
            url="redis://redis:6379/1",
            max_connections=7,
            decode_responses=True,
            socket_timeout=0.2,
            socket_connect_timeout=0.3,
        )
    )

    assert client is fake_client
    assert fake_client.ping_calls == 1
    assert captured["url"] == "redis://redis:6379/1"
    assert captured["kwargs"]["decode_responses"] is True


def test_create_sync_redis_client_closes_on_ping_failure(monkeypatch: pytest.MonkeyPatch):
    redis = pytest.importorskip("redis")
    fake_client = _FakeSyncRedis(ping_error=RuntimeError("redis unavailable"))
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: fake_client)

    with pytest.raises(RuntimeError) as exc_info:
        redis_manager.create_sync_redis_client(RedisConfig(url="redis://:secret@redis:6379/0"))

    assert "secret" not in str(exc_info.value)
    assert "Redis connection failed" in str(exc_info.value)
    assert fake_client.closed is True


def test_sync_redis_client_closes_after_context(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeSyncRedis()
    monkeypatch.setattr(redis_manager, "create_sync_redis_client", lambda *args, **kwargs: fake_client)

    with redis_manager.sync_redis_client(RedisConfig(url="redis://redis:6379/1")) as client:
        assert client is fake_client

    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_create_async_redis_client_closes_on_ping_failure(monkeypatch: pytest.MonkeyPatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    fake_client = _FakeAsyncRedis(ping_error=RuntimeError("redis unavailable"))
    monkeypatch.setattr(redis_asyncio.Redis, "from_url", staticmethod(lambda *args, **kwargs: fake_client))

    with pytest.raises(RuntimeError) as exc_info:
        await redis_manager.create_async_redis_client(RedisConfig(url="redis://:secret@redis:6379/0"))

    assert "secret" not in str(exc_info.value)
    assert "Redis connection failed" in str(exc_info.value)
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_get_async_redis_client_caches_and_closes_client(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeAsyncRedis()
    create_calls = 0

    async def fake_create_async_client(config: RedisConfig | None = None):
        nonlocal create_calls
        assert config and config.url == "redis://redis:6379/2"
        create_calls += 1
        return fake_client

    monkeypatch.setattr(redis_manager, "_async_redis_client", None)
    monkeypatch.setattr(redis_manager, "_async_redis_lock", None)
    monkeypatch.setattr(redis_manager, "create_async_redis_client", fake_create_async_client)

    client_1 = await redis_manager.get_async_redis_client(RedisConfig(url="redis://redis:6379/2"))
    client_2 = await redis_manager.get_async_redis_client(RedisConfig(url="redis://redis:6379/2"))
    await redis_manager.close_async_redis_client()

    assert client_1 is fake_client
    assert client_2 is fake_client
    assert create_calls == 1
    assert fake_client.closed is True
