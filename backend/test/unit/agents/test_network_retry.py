"""NetworkRetryMiddleware 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.exceptions import ModelError

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


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("max_retries", [0, 2])
@pytest.mark.asyncio
async def test_rate_limit_exhaustion_preserves_original_error(sync, max_retries):
    """429 按配置重试，耗尽后保留原异常供 Run 失败通道处理。"""
    import httpx
    import openai

    error = openai.RateLimitError(
        "rate limit exhausted",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid/v1/chat/completions")),
        body=None,
    )
    mw = NetworkRetryMiddleware(max_retries=max_retries, initial_delay=0, jitter=False)
    calls = 0

    def handler(_request):
        """重复抛出同一个 provider 异常。"""
        nonlocal calls
        calls += 1
        raise error

    async def async_handler(request):
        """异步入口保留同一异常对象。"""
        return handler(request)

    with pytest.raises(openai.RateLimitError) as raised:
        if sync:
            mw.wrap_model_call(object(), handler)
        else:
            await mw.awrap_model_call(object(), async_handler)
    assert raised.value is error
    assert calls == max_retries + 1


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


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503, 504])
def test_http_status_takes_precedence_over_error_text(status):
    """真实 SDK 状态码决定分类，不受响应文本中的网络关键词影响。"""
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    error = openai.APIStatusError(
        "Unsupported timeout parameter" if status < 500 else "upstream overloaded",
        response=httpx.Response(status, request=request),
        body=None,
    )
    assert _is_network_error(error) is (status >= 500)


def test_standard_model_errors_take_precedence_over_text_and_cause():
    """标准模型错误保留业务分类，外层未知包装不会覆盖内层状态。"""
    from langchain_core.exceptions import ModelAPIError, ModelInvalidRequestError

    invalid = ModelInvalidRequestError("Unsupported parameter: timeout")
    invalid.__cause__ = ConnectionError("Connection refused")
    assert _is_network_error(invalid) is False
    wrapper = RuntimeError("Connection timeout in model wrapper")
    wrapper.__cause__ = invalid
    assert _is_network_error(wrapper) is False
    assert _is_network_error(ModelAPIError("upstream overloaded")) is True


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("kind", ["server", "stream"])
@pytest.mark.asyncio
async def test_real_network_errors_exhaust_budget_without_error_message(sync, kind):
    """真实服务端和流式传输异常预算耗尽后抛出原异常，不返回错误消息。"""
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    error = (
        openai.InternalServerError("Internal server error", response=httpx.Response(500, request=request), body=None)
        if kind == "server"
        else httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body (incomplete chunked read)"
        )
    )
    mw = NetworkRetryMiddleware(network_budget_seconds=0, initial_delay=0, jitter=False)

    def handler(_request):
        """持续抛出同一个真实异常。"""
        raise error

    async def async_handler(_request):
        """异步调用复用相同异常。"""
        return handler(_request)

    with pytest.raises(type(error)) as raised:
        if sync:
            mw.wrap_model_call(object(), handler)
        else:
            await mw.awrap_model_call(object(), async_handler)
    assert raised.value is error


@pytest.mark.asyncio
async def test_invalid_model_request_with_timeout_text_is_not_retried():
    """非法参数即使包含 timeout 也立即失败，不进入网络等待。"""
    from langchain_core.exceptions import ModelInvalidRequestError

    error = ModelInvalidRequestError("Unsupported parameter: timeout")
    calls = 0
    mw = NetworkRetryMiddleware(network_budget_seconds=0.01, network_initial_delay=0)

    async def handler(_request):
        """记录非法请求实际执行次数。"""
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(ModelInvalidRequestError) as raised:
        await mw.awrap_model_call(object(), handler)
    assert raised.value is error
    assert calls == 1
