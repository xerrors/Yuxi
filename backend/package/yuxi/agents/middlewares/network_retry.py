"""网络类错误的持续重试中间件，非网络错误沿用 ModelRetryMiddleware 的次数重试。

断网/APIC 连接抖动恢复后任务应自动继续(对标 Claude Code 的行为)：
网络类异常(连接拒绝/超时/DNS)按指数退避持续重试，总预算内不向 graph 抛错；
预算耗尽显式抛出，Run 以 failed 结束，不再出现"假完成"。

网络重试通过 handler 包装实现：wrapped handler 在预算内吞掉网络异常退避重试，
预算耗尽或非网络异常原样抛出，交给父类的 wrap_model_call/awrap_model_call 按
retry_on(排除网络异常)处理。预算起点在包装创建时固定，跨父类非网络重试保持，
不会因外层重试而放大。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.model_retry import ModelRetryMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ModelError
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
_NON_NETWORK_MARKERS = ("ratelimit", "authentication", "permission", "invalid_request", "not_found", "context_length")


def _is_network_error(exc: BaseException) -> bool:
    """判定异常是否为网络恢复类（连接/超时/5xx 网关），边遍历异常链边判断。"""
    seen: set[int] = set()
    cursor: BaseException | None = exc
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        detail = f"{type(cursor).__name__} {cursor}".lower()
        if any(m in detail for m in _NON_NETWORK_MARKERS):
            return False
        if any(m in detail for m in _NETWORK_ERROR_MARKERS):
            return True
        cursor = cursor.__cause__ or cursor.__context__
    return False


def _retry_non_network_errors(exc: BaseException) -> bool:
    """父类 retry_on 谓词：网络异常已由 handler 包装处理，这里排除；其余按默认语义。"""
    if _is_network_error(exc):
        return False
    if isinstance(exc, ModelError):
        return exc.is_retryable
    return True


class NetworkRetryMiddleware(ModelRetryMiddleware):
    """网络错误按预算重试、非网络错误按次数重试的统一中间件。

    网络重试通过 handler 包装实现，非网络错误复用父类 ``ModelRetryMiddleware`` 的
    ``max_retries``/``retry_on``/``on_failure`` 语义。网络预算起点在包装创建时固定，
    跨父类的非网络重试保持，不会被放大成多份。
    """

    def __init__(
        self,
        *,
        max_retries: int = 2,
        network_budget_seconds: float | None = None,
        network_initial_delay: float = 2.0,
        network_max_delay: float = 30.0,
        **kwargs,
    ) -> None:
        super().__init__(max_retries=max_retries, retry_on=_retry_non_network_errors, **kwargs)
        self._network_budget = (
            network_budget_seconds
            if network_budget_seconds is not None
            else float(os.getenv("YUXI_NETWORK_RETRY_BUDGET_SECONDS", "600"))
        )
        self._network_initial_delay = network_initial_delay
        self._network_max_delay = network_max_delay

    def _network_retry_exhausted(self, exc: BaseException, *, elapsed: float, delay: float, attempt: int) -> bool:
        """预算耗尽返回 True（调用方抛出），否则记日志并返回 False 继续重试。"""
        if self._network_budget <= 0 or elapsed + delay > self._network_budget:
            logger.warning(
                f"[network-retry] 预算耗尽({self._network_budget:.0f}s)，抛出网络错误: {type(exc).__name__}: {exc}",
            )
            return True
        logger.warning(
            f"[network-retry] 网络错误(第{attempt}次，已等待{elapsed:.0f}s，{delay:.0f}s后重试): "
            f"{type(exc).__name__}: {exc}",
        )
        return False

    def _wrap_network_retry(self, handler):
        started = time.monotonic()
        delay = self._network_initial_delay
        attempt = 0

        def wrapped(request):
            nonlocal delay, attempt
            while True:
                try:
                    return handler(request)
                except GraphBubbleUp:
                    raise
                except Exception as exc:  # noqa: BLE001 — 需要拦截底层 SDK 的各种异常类型
                    if not _is_network_error(exc):
                        raise
                    if self._network_retry_exhausted(
                        exc, elapsed=time.monotonic() - started, delay=delay, attempt=attempt + 1
                    ):
                        raise
                    attempt += 1
                    time.sleep(delay)
                    delay = min(delay * 2, self._network_max_delay)

        return wrapped

    def _awrap_network_retry(self, handler):
        started = time.monotonic()
        delay = self._network_initial_delay
        attempt = 0

        async def wrapped(request):
            nonlocal delay, attempt
            while True:
                try:
                    return await handler(request)
                except GraphBubbleUp:
                    raise
                except Exception as exc:  # noqa: BLE001 — 需要拦截底层 SDK 的各种异常类型
                    if not _is_network_error(exc):
                        raise
                    if self._network_retry_exhausted(
                        exc, elapsed=time.monotonic() - started, delay=delay, attempt=attempt + 1
                    ):
                        raise
                    attempt += 1
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._network_max_delay)

        return wrapped

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return super().wrap_model_call(request, self._wrap_network_retry(handler))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await super().awrap_model_call(request, self._awrap_network_retry(handler))
