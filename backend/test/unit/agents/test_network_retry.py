"""NetworkRetryMiddleware 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.exceptions import ModelError
from langchain_core.messages import AIMessage

from yuxi.agents.middlewares.network_retry import NetworkRetryMiddleware, _is_network_error

pytestmark = [pytest.mark.unit]


class FakeError(Exception):
    pass


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Connection error.", True),
        ("OpenAIConnectionError: Connection error", True),
        ("Connection refused to api host", True),
        ("APITimeoutError: Request timed out", True),
        ("httpx.ReadTimeout while reading", True),
        ("503 Service Unavailable", True),
        ("502 Bad Gateway from upstream", True),
        ("RateLimitError: 429 too many requests", False),
        ("AuthenticationError: invalid api key", False),
        ("NotFoundError: model not found", False),
        ("invalid_request_error: bad parameter", False),
        ("This is a logic bug", False),
    ],
)
def test_is_network_error_classifies(message, expected):
    assert _is_network_error(FakeError(message)) is expected


def test_is_network_error_inspects_cause_chain():
    inner = FakeError("Connection reset by peer")
    outer = FakeError("model call wrapper failed")
    outer.__cause__ = inner
    assert _is_network_error(outer) is True


@pytest.mark.asyncio
async def test_retries_network_error_until_success():
    mw = NetworkRetryMiddleware(network_budget_seconds=30, network_initial_delay=0.01, network_max_delay=0.02)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeError("OpenAIConnectionError: Connection error")
        return "ok"

    result = await mw.awrap_model_call(object(), handler)
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_budget_exhaustion_raises():
    mw = NetworkRetryMiddleware(network_budget_seconds=0.05, network_initial_delay=0.03, network_max_delay=0.03)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        raise FakeError("Connection refused")

    with pytest.raises(FakeError):
        await mw.awrap_model_call(object(), handler)
    # 预算内至少重试过一次
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_cancellation_not_swallowed():
    mw = NetworkRetryMiddleware(network_budget_seconds=30, network_initial_delay=0.01, network_max_delay=0.01)

    async def handler(request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await mw.awrap_model_call(object(), handler)


@pytest.mark.asyncio
async def test_non_network_error_retried_by_max_retries_then_continue():
    """非网络错误仍按 max_retries 次数重试，耗尽后 on_failure=continue 返回错误 AIMessage。"""
    mw = NetworkRetryMiddleware(max_retries=2, initial_delay=0.0, jitter=False)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        raise FakeError("AuthenticationError: invalid api key")

    result = await mw.awrap_model_call(object(), handler)

    # max_retries=2 → 1 次初始 + 2 次重试 = 3 次调用
    assert calls["n"] == 3
    assert isinstance(result.result[0], AIMessage)


@pytest.mark.asyncio
async def test_non_retryable_model_error_propagates():
    """非网络错误但不可重试(ModelError.is_retryable=False)时立即抛出，不消耗重试次数。"""
    mw = NetworkRetryMiddleware(max_retries=2, initial_delay=0.0)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        raise ModelError("bad request")  # ModelError.is_retryable 默认为 False

    with pytest.raises(ModelError):
        await mw.awrap_model_call(object(), handler)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_network_budget_honored_and_fails_explicitly(monkeypatch):
    """持续网络错误下，单中间件按预算退避重试，耗尽后显式抛出而非"假完成"。"""
    import yuxi.agents.middlewares.network_retry as module

    clock = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    mw = NetworkRetryMiddleware(network_budget_seconds=600, network_initial_delay=2, network_max_delay=30)

    async def handler(_request):
        raise FakeError("OpenAIConnectionError: Connection error")

    with pytest.raises(FakeError):
        await mw.awrap_model_call(object(), handler)

    # 总等待受单次预算约束，不被放大，且异常显式抛出(没有返回 AIMessage)。
    assert 0 < sum(sleeps) <= 600


@pytest.mark.asyncio
async def test_non_network_error_retry_succeeds_after_backoff():
    """非网络错误重试成功后正常返回，语义未被网络重试分支改变。"""
    mw = NetworkRetryMiddleware(max_retries=2, initial_delay=0.0, jitter=False)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeError("This is a logic bug")
        return "ok"

    assert await mw.awrap_model_call(object(), handler) == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_network_then_non_network_error_routes_to_parent_retry():
    """网络异常重试后遇到非网络异常：非网络异常交给父类按 max_retries 重试，最终成功。"""
    mw = NetworkRetryMiddleware(
        max_retries=2,
        network_budget_seconds=30,
        network_initial_delay=0.0,
        network_max_delay=0.0,
        initial_delay=0.0,
        jitter=False,
    )
    calls = {"n": 0}

    async def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeError("OpenAIConnectionError: Connection error")  # 网络 → wrapped 重试
        if calls["n"] in (2, 3):
            raise FakeError("AuthenticationError: invalid api key")  # 非网络 → 交给父类
        return "ok"  # calls=4

    result = await mw.awrap_model_call(object(), handler)

    assert result == "ok"
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_network_budget_survives_parent_retry(monkeypatch):
    """网络异常 → 非网络异常 → 网络异常：预算起点跨父类重试保持，不被重置放大。"""
    import yuxi.agents.middlewares.network_retry as module

    clock = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    mw = NetworkRetryMiddleware(
        max_retries=1,  # 父类非网络重试 1 次
        network_budget_seconds=5,
        network_initial_delay=2,
        network_max_delay=2,
        initial_delay=0.0,
        jitter=False,
    )
    calls = {"n": 0}

    async def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeError("OpenAIConnectionError: Connection error")  # 网络，sleep 2
        if calls["n"] == 2:
            raise FakeError("AuthenticationError: invalid api key")  # 非网络 → 交给父类
        raise FakeError("OpenAIConnectionError: Connection error")  # calls=3+ 持续网络

    with pytest.raises(FakeError):
        await mw.awrap_model_call(object(), handler)

    # 预算起点在包装创建时固定，网络 sleep 累计不超过 5s（不是 5 × 父类重试份数）。
    assert 0 < sum(sleeps) <= 5
