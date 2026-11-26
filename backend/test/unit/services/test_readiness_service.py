from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import yuxi.infrastructure.worker_health as job_health
import yuxi.modules.system.readiness as readiness_service

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def reset_readiness_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离各测试的进程内 readiness cache。"""

    monkeypatch.setattr(readiness_service, "_readiness_cache", None)
    readiness_service._readiness_inflight.clear()

    class HealthyWorkerRedis:
        async def get(self, key: str) -> str:
            return f"healthy:{key}"

        async def pttl(self, key: str) -> int:
            if key == readiness_service.WORKER_HEALTH_KEY:
                return readiness_service.WORKER_HEALTH_MAX_TTL_MS
            if key == readiness_service.WORKER_RECONCILIATION_HEALTH_KEY:
                return readiness_service.WORKER_RECONCILIATION_HEALTH_TTL_SECONDS * 1000
            return readiness_service.JOB_RECONCILIATION_HEALTH_TTL_SECONDS * 1000

    async def healthy_redis() -> HealthyWorkerRedis:
        return HealthyWorkerRedis()

    monkeypatch.setattr(readiness_service, "get_redis_client", healthy_redis)


async def test_readiness_requires_startup_postgres_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok() -> None:
        return None

    monkeypatch.setattr(readiness_service, "_probe_postgres", ok)
    monkeypatch.setattr(readiness_service, "_probe_redis", ok)

    result = await readiness_service.get_readiness(startup_complete=True)

    assert result == {
        "status": "ready",
        "degraded": False,
        "checks": {
            "startup": {"status": "ok"},
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
            "worker": {"status": "ok"},
        },
        "components": {},
    }


async def test_readiness_preserves_each_failed_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    async def postgres_failed() -> None:
        raise ConnectionError("database secret must not be returned")

    async def redis_ok() -> None:
        return None

    monkeypatch.setattr(readiness_service, "_probe_postgres", postgres_failed)
    monkeypatch.setattr(readiness_service, "_probe_redis", redis_ok)

    result = await readiness_service.get_readiness(startup_complete=False)

    assert result["status"] == "not_ready"
    assert result["checks"] == {
        "startup": {"status": "error", "code": "not_complete"},
        "postgres": {"status": "error", "code": "ConnectionError"},
        "redis": {"status": "ok"},
        "worker": {"status": "ok"},
    }
    assert result["degraded"] is False
    assert "secret" not in str(result)


async def test_readiness_probe_timeout_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow() -> None:
        await asyncio.sleep(0.02)

    async def ok() -> None:
        return None

    monkeypatch.setattr(readiness_service, "READINESS_PROBE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(readiness_service, "_probe_postgres", slow)
    monkeypatch.setattr(readiness_service, "_probe_redis", ok)

    result = await readiness_service.get_readiness(startup_complete=True)

    assert result["status"] == "not_ready"
    assert result["checks"]["postgres"] == {"status": "error", "code": "timeout"}


async def test_missing_worker_health_lease_blocks_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok() -> None:
        return None

    async def missing_worker() -> None:
        raise readiness_service.WorkerUnavailableError("must not leak")

    monkeypatch.setattr(readiness_service, "_probe_postgres", ok)
    monkeypatch.setattr(readiness_service, "_probe_redis", ok)
    monkeypatch.setattr(readiness_service, "_probe_worker", missing_worker)

    result = await readiness_service.get_readiness(startup_complete=True)

    assert result["status"] == "not_ready"
    assert result["checks"]["worker"] == {"status": "error", "code": "WorkerUnavailableError"}
    assert "must not leak" not in str(result)


@pytest.mark.parametrize("lease_value,ttl_ms", [(None, -2), ("stale", -1), ("expired", 0)])
async def test_worker_probe_rejects_missing_or_non_expiring_health_lease(
    monkeypatch: pytest.MonkeyPatch,
    lease_value: str | None,
    ttl_ms: int,
) -> None:
    class InvalidWorkerRedis:
        async def get(self, _key: str) -> str | None:
            return lease_value

        async def pttl(self, _key: str) -> int:
            return ttl_ms

    async def invalid_redis() -> InvalidWorkerRedis:
        return InvalidWorkerRedis()

    monkeypatch.setattr(readiness_service, "get_redis_client", invalid_redis)

    with pytest.raises(readiness_service.WorkerUnavailableError):
        await readiness_service._probe_worker()


async def test_worker_probe_requires_arq_and_reconciliation_leases_with_bounded_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[tuple[str, str]] = []

    class WorkerRedis:
        async def get(self, key: str) -> str:
            requested.append(("get", key))
            return "healthy"

        async def pttl(self, key: str) -> int:
            requested.append(("pttl", key))
            if key == readiness_service.WORKER_HEALTH_KEY:
                return readiness_service.WORKER_HEALTH_MAX_TTL_MS
            if key == readiness_service.WORKER_RECONCILIATION_HEALTH_KEY:
                return readiness_service.WORKER_RECONCILIATION_HEALTH_TTL_SECONDS * 1000
            return readiness_service.JOB_RECONCILIATION_HEALTH_TTL_SECONDS * 1000

    async def worker_redis() -> WorkerRedis:
        return WorkerRedis()

    monkeypatch.setattr(readiness_service, "get_redis_client", worker_redis)

    await readiness_service._probe_worker()

    assert requested == [
        ("get", readiness_service.WORKER_HEALTH_KEY),
        ("pttl", readiness_service.WORKER_HEALTH_KEY),
        ("get", readiness_service.WORKER_RECONCILIATION_HEALTH_KEY),
        ("pttl", readiness_service.WORKER_RECONCILIATION_HEALTH_KEY),
        ("get", job_health.JOB_RECONCILIATION_HEALTH_KEY),
        ("pttl", job_health.JOB_RECONCILIATION_HEALTH_KEY),
    ]


async def test_required_component_blocks_readiness_while_optional_failure_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok() -> None:
        return None

    monkeypatch.setattr(readiness_service, "_probe_postgres", ok)
    monkeypatch.setattr(readiness_service, "_probe_redis", ok)

    required_failure = await readiness_service.get_readiness(
        startup_complete=True,
        startup_components={
            "default_agents": {"status": "error", "required": True, "code": "RuntimeError"},
            "builtin_mcp_servers": {"status": "error", "required": False, "code": "ConnectionError"},
        },
    )
    optional_failure = await readiness_service.get_readiness(
        startup_complete=True,
        startup_components={
            "default_agents": {"status": "ok", "required": True},
            "builtin_mcp_servers": {"status": "error", "required": False, "code": "ConnectionError"},
        },
    )

    assert required_failure["status"] == "not_ready"
    assert required_failure["degraded"] is True
    assert optional_failure["status"] == "ready"
    assert optional_failure["degraded"] is True
    assert "ConnectionError" in str(optional_failure["components"])


async def test_readiness_cache_is_single_flight_and_returns_independent_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"postgres": 0, "redis": 0}

    async def postgres() -> None:
        calls["postgres"] += 1
        await asyncio.sleep(0)

    async def redis() -> None:
        calls["redis"] += 1
        await asyncio.sleep(0)

    monkeypatch.setattr(readiness_service, "_probe_postgres", postgres)
    monkeypatch.setattr(readiness_service, "_probe_redis", redis)
    monkeypatch.setattr(readiness_service, "READINESS_CACHE_TTL_SECONDS", 10)
    components = {"single_flight": {"status": "ok", "required": True}}

    results = await asyncio.gather(
        *(readiness_service.get_readiness(startup_complete=True, startup_components=components) for _ in range(5))
    )
    results[0]["checks"]["postgres"]["status"] = "mutated"
    cached = await readiness_service.get_readiness(startup_complete=True, startup_components=components)

    assert calls == {"postgres": 1, "redis": 1}
    assert cached["checks"]["postgres"]["status"] == "ok"


@pytest.mark.parametrize("elapsed,expected_status", [(0.5, "ready"), (2.0, "not_ready")])
async def test_readiness_probe_expires_even_if_request_is_cancelled(monkeypatch, elapsed, expected_status):
    """共享探针独自完成后，接流量证据仍从探针结束时开始过期。"""
    started = asyncio.Event()
    release_probe = asyncio.Event()
    current_time = 10.0
    is_healthy = True

    async def probe_postgres():
        """暂停首次数据库探针，后续读取当前可用性。"""
        started.set()
        await release_probe.wait()
        if not is_healthy:
            raise ConnectionError("database unavailable")

    async def probe_redis():
        """保持 Redis 正常以独立观察数据库失败。"""
        pass

    monkeypatch.setattr(readiness_service, "_probe_postgres", probe_postgres)
    monkeypatch.setattr(readiness_service, "_probe_redis", probe_redis)
    monkeypatch.setattr(readiness_service, "READINESS_CACHE_TTL_SECONDS", 1)
    monkeypatch.setattr(readiness_service, "time", SimpleNamespace(monotonic=lambda: current_time))

    request = asyncio.create_task(readiness_service.get_readiness(startup_complete=True))
    await asyncio.wait_for(started.wait(), timeout=5)
    probe = next(iter(readiness_service._readiness_inflight.values()))
    request.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        current_time += 5
        release_probe.set()
        await probe

    current_time += elapsed
    is_healthy = False
    result = await readiness_service.get_readiness(startup_complete=True)
    assert result["status"] == expected_status
    assert result["checks"]["postgres"] == (
        {"status": "ok"} if expected_status == "ready" else {"status": "error", "code": "ConnectionError"}
    )
