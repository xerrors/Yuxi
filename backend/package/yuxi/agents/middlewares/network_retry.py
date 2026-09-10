"""网络类错误的持续重试中间件，同时保留非网络错误的次数重试语义。

断网/APIC 连接抖动恢复后任务应自动继续(对标 Claude Code 的行为)：
网络类异常(连接拒绝/超时/DNS)按指数退避持续重试，总预算内不向 graph 抛错；
预算耗尽显式抛出，Run 以 failed 结束，不再出现"假完成"。

非网络错误(逻辑错误/鉴权/限流/参数错误)仍按 ModelRetryMiddleware 的 max_retries
次数重试，语义不变。两类错误的重试维度(预算 vs 次数)在同一个中间件内区分，
避免拆成两个中间件后因装配顺序/外层重试网络错误而放大预算。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware._retry import (
    OnFailure,
    calculate_delay,
    default_retry_on,
    should_retry_exception,
)
from langchain.agents.middleware.model_retry import ModelRetryMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.errors import GraphBubbleUp

from yuxi.utils.logging_config import logger

# 网络恢复类异常：特点是"网络/服务端恢复后重试大概率成功"。
# 通过类名字符串匹配，避免硬依赖各 SDK 的异常类(跨 openai/httpx/anthropic 等)。
_NETWORK_ERROR_MARKERS = (
    "connectionerror",
    "connection error",
    "connection refused",
    "connection reset",
    "connecttimeout",
    "readtimeout",
    "apitimeouterror",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "remote_protocol",
)

# 明确非网络的错误：重试无意义，立即放行。
# 空字符串永远不匹配任何 marker，保持行为一致。
_NON_NETWORK_MARKERS = ("ratelimit", "authentication", "permission", "invalid_request", "not_found", "context_length")


def is_network_error(exc: BaseException) -> bool:
    """判定异常是否为网络恢复类（连接/超时/5xx 网关），可安全持续重试。"""
    chain: list[BaseException] = []
    seen: set[int] = set()
    cursor: BaseException | None = exc
    while cursor is not None and id(cursor) not in seen:
        chain.append(cursor)
        seen.add(id(cursor))
        cursor = cursor.__cause__ or cursor.__context__
    for e in chain:
        detail = f"{type(e).__name__} {e}".lower()
        if any(m in detail for m in _NON_NETWORK_MARKERS):
            return False
        if any(m in detail for m in _NETWORK_ERROR_MARKERS):
            return True
    return False


class NetworkRetryMiddleware(ModelRetryMiddleware):
    """网络错误按预算重试、非网络错误按次数重试的统一中间件。

    继承 ``ModelRetryMiddleware``：非网络错误复用其 ``max_retries``/``retry_on``/
    ``on_failure`` 语义；网络错误在 ``wrap_model_call``/``awrap_model_call`` 里按
    ``network_budget_seconds`` 预算退避重试，耗尽后显式抛出(而非被 ``on_failure``
    吞成含错误文本的 AIMessage)。
    """

    def __init__(
        self,
        *,
        max_retries: int = 2,
        network_budget_seconds: float | None = None,
        network_initial_delay: float = 2.0,
        network_max_delay: float = 30.0,
        on_failure: OnFailure = "continue",
        backoff_factor: float = 2.0,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ) -> None:
        super().__init__(
            max_retries=max_retries,
            retry_on=default_retry_on,
            on_failure=on_failure,
            backoff_factor=backoff_factor,
            initial_delay=initial_delay,
            max_delay=max_delay,
            jitter=jitter,
        )
        self._network_budget = (
            network_budget_seconds
            if network_budget_seconds is not None
            else float(os.getenv("YUXI_NETWORK_RETRY_BUDGET_SECONDS", "600"))
        )
        self._network_initial_delay = network_initial_delay
        self._network_max_delay = network_max_delay

    def _handle_network_retry(self, exc: BaseException, *, elapsed: float, delay: float, attempt: int) -> bool:
        """预算内返回 True 继续重试，预算耗尽返回 False 由调用方抛出。"""
        if self._network_budget <= 0 or elapsed + delay > self._network_budget:
            logger.warning(
                f"[network-retry] 预算耗尽({self._network_budget:.0f}s)，抛出网络错误: {type(exc).__name__}: {exc}",
            )
            return False
        logger.warning(
            f"[network-retry] 网络错误(第{attempt}次，已等待{elapsed:.0f}s，{delay:.0f}s后重试): "
            f"{type(exc).__name__}: {exc}",
        )
        return True

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        started = time.monotonic()
        network_delay = self._network_initial_delay
        network_attempt = 0
        non_network_attempt = 0
        while True:
            try:
                return handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:  # noqa: BLE001 — 需要拦截底层 SDK 的各种异常类型
                if is_network_error(exc):
                    elapsed = time.monotonic() - started
                    if not self._handle_network_retry(
                        exc, elapsed=elapsed, delay=network_delay, attempt=network_attempt + 1
                    ):
                        raise
                    network_attempt += 1
                    time.sleep(network_delay)
                    network_delay = min(network_delay * 2, self._network_max_delay)
                    continue
                if not should_retry_exception(exc, self.retry_on):
                    raise
                non_network_attempt += 1
                if non_network_attempt > self.max_retries:
                    return self._handle_failure(exc, non_network_attempt)
                delay = calculate_delay(
                    non_network_attempt - 1,
                    backoff_factor=self.backoff_factor,
                    initial_delay=self.initial_delay,
                    max_delay=self.max_delay,
                    jitter=self.jitter,
                )
                if delay > 0:
                    time.sleep(delay)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        started = time.monotonic()
        network_delay = self._network_initial_delay
        network_attempt = 0
        non_network_attempt = 0
        while True:
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 需要拦截底层 SDK 的各种异常类型
                if is_network_error(exc):
                    elapsed = time.monotonic() - started
                    if not self._handle_network_retry(
                        exc, elapsed=elapsed, delay=network_delay, attempt=network_attempt + 1
                    ):
                        raise
                    network_attempt += 1
                    await asyncio.sleep(network_delay)
                    network_delay = min(network_delay * 2, self._network_max_delay)
                    continue
                if not should_retry_exception(exc, self.retry_on):
                    raise
                non_network_attempt += 1
                if non_network_attempt > self.max_retries:
                    return self._handle_failure(exc, non_network_attempt)
                delay = calculate_delay(
                    non_network_attempt - 1,
                    backoff_factor=self.backoff_factor,
                    initial_delay=self.initial_delay,
                    max_delay=self.max_delay,
                    jitter=self.jitter,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
