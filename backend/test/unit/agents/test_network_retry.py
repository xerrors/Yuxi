"""NetworkRetryMiddleware 单元测试。"""

from __future__ import annotations

import asyncio

import pytest
from yuxi.agents.middlewares.network_retry import NetworkRetryMiddleware, is_network_error

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
    assert is_network_error(FakeError(message)) is expected


def test_is_network_error_inspects_cause_chain():
    inner = FakeError("Connection reset by peer")
    outer = FakeError("model call wrapper failed")
    outer.__cause__ = inner
    assert is_network_error(outer) is True


@pytest.mark.asyncio
async def test_retries_network_error_until_success():
    mw = NetworkRetryMiddleware(budget_seconds=30, initial_delay=0.01, max_delay=0.02)
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
async def test_non_network_error_propagates_immediately():
    mw = NetworkRetryMiddleware(budget_seconds=30, initial_delay=0.01, max_delay=0.02)
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        raise FakeError("AuthenticationError: bad key")

    with pytest.raises(FakeError):
        await mw.awrap_model_call(object(), handler)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_raises():
    mw = NetworkRetryMiddleware(budget_seconds=0.05, initial_delay=0.03, max_delay=0.03)
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
    mw = NetworkRetryMiddleware(budget_seconds=30, initial_delay=0.01, max_delay=0.01)

    async def handler(request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await mw.awrap_model_call(object(), handler)


@pytest.mark.asyncio
async def test_composed_middlewares_honor_budget_and_fail_explicitly(monkeypatch):
    """按真实装配顺序组合两个中间件：预算不被外层重置，耗尽后显式失败而非"假完成"。

    ModelRetryMiddleware 在 NetworkRetryMiddleware 之外（中间件列表排后者为内层）。
    若外层也重试网络错误，每轮都会开启一个全新的预算(600s × 3)且最终被 on_failure
    吞成含错误文本的 AIMessage；本用例断言预算只被消费一次且异常显式抛出。
    """
    from types import SimpleNamespace

    from langchain.agents.middleware import ModelRetryMiddleware
    from yuxi.agents.middlewares import network_retry as network_retry_module
    from yuxi.agents.middlewares.network_retry import retry_non_network_errors

    clock = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(network_retry_module, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(network_retry_module.asyncio, "sleep", fake_sleep)

    inner = NetworkRetryMiddleware(budget_seconds=600, initial_delay=2, max_delay=30)
    outer = ModelRetryMiddleware(max_retries=2, retry_on=retry_non_network_errors)

    async def innermost(_request):
        raise FakeError("OpenAIConnectionError: Connection error")

    async def handler(request):
        return await inner.awrap_model_call(request, innermost)

    with pytest.raises(FakeError):
        await outer.awrap_model_call(object(), handler)

    # 总等待受单次预算约束，没有被外层放大成 3 份。
    assert 0 < sum(sleeps) <= 600


@pytest.mark.asyncio
async def test_non_network_error_still_retried_by_outer_model_retry():
    """非网络错误仍由外层 ModelRetry 按 max_retries 重试，语义未被改变。"""
    from langchain.agents.middleware import ModelRetryMiddleware
    from yuxi.agents.middlewares.network_retry import retry_non_network_errors

    calls = {"n": 0}
    outer = ModelRetryMiddleware(max_retries=2, retry_on=retry_non_network_errors, initial_delay=0.0, jitter=False)

    async def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeError("This is a logic bug")
        return "ok"

    assert await outer.awrap_model_call(object(), handler) == "ok"
    assert calls["n"] == 3
