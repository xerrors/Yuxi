"""网络类错误的持续重试中间件。

断网/APIC 连接抖动恢复后任务应自动继续(对标 Claude Code 的行为)：
网络类异常(连接拒绝/超时/DNS)按指数退避持续重试，总预算内不向 graph 抛错；
预算耗尽或非网络错误交给内层 ModelRetryMiddleware 按原有语义处理。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ContextT, ModelRequest, ModelResponse, ResponseT

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

# 明确非网络的错误：重试无意义，立即放行给内层处理。
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


def retry_non_network_errors(exc: BaseException) -> bool:
    """外层 ModelRetryMiddleware 的重试谓词：网络类错误一律不由它重试。

    中间件列表排后者为内层(先拦截)，网络错误先经 NetworkRetryMiddleware 按自身预算
    退避重试。若外层也判定可重试，每轮都会为同一次模型调用开启全新的预算
    (600s × (max_retries+1))，且预算耗尽后会被 on_failure 吞成含错误文本的 AIMessage
    ——即"假完成"。这里显式排除，让预算耗尽按异常显式失败。
    """
    if is_network_error(exc):
        return False
    from langchain.agents.middleware.model_retry import default_retry_on

    return default_retry_on(exc)


class NetworkRetryMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """对网络类模型调用错误做预算内持续重试的中间件。

    挂在 ModelRetryMiddleware 之内（中间件列表排后者为内层）：网络错误在此按预算
    退避消化，其余错误原样透传给内层重试/失败语义；预算耗尽后抛出的网络错误由外层
    ModelRetryMiddleware 的 retry_on=retry_non_network_errors 判定为不可重试，直接失败。
    """

    def __init__(
        self,
        *,
        budget_seconds: float | None = None,
        initial_delay: float = 2.0,
        max_delay: float = 30.0,
    ) -> None:
        super().__init__()
        self._budget = (
            budget_seconds
            if budget_seconds is not None
            else float(os.getenv("YUXI_NETWORK_RETRY_BUDGET_SECONDS", "600"))
        )
        self._initial_delay = initial_delay
        self._max_delay = max_delay

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        started = time.monotonic()
        delay = self._initial_delay
        attempt = 0
        while True:
            try:
                return await handler(request)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 — 需要拦截底层 SDK 的各种异常类型
                if not is_network_error(exc):
                    raise
                attempt += 1
                elapsed = time.monotonic() - started
                if self._budget <= 0 or elapsed + delay > self._budget:
                    logger.warning(
                        f"[network-retry] 预算耗尽({self._budget:.0f}s)，放行网络错误: {type(exc).__name__}: {exc}",
                    )
                    raise
                logger.warning(
                    f"[network-retry] 网络错误(第{attempt}次，已等待{elapsed:.0f}s，{delay:.0f}s后重试): "
                    f"{type(exc).__name__}: {exc}",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_delay)
