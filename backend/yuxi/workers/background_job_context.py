"""向领域执行方提供进度上报、取消检查与租约保护。"""

from __future__ import annotations

import asyncio
from typing import Any

from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.modules.background_jobs.service import JobTracker

PROGRESS_PERSIST_DELTA = 2.0


class BackgroundJobContext:
    """向领域 Handler 提供受当前 attempt lease 保护的进度与取消边界。"""

    def __init__(self, job_id: str, worker_id: str, payload: dict[str, Any] | None = None):
        self.job_id = job_id
        self.worker_id = worker_id
        self.payload = payload or {}
        self.cancellation_reason: str | None = None
        self._cancel_requested = False
        self._last_persisted_progress: float | None = None
        self._last_persisted_message: str | None = None
        self._repo = BackgroundJobRepository()
        self._reporter = JobTracker()

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        normalized = float(progress)
        progress_is_throttled = (
            self._last_persisted_progress is not None and abs(normalized - self._last_persisted_progress) < PROGRESS_PERSIST_DELTA
        )
        if progress_is_throttled and (message is None or message == self._last_persisted_message):
            return
        data: dict[str, Any] = {"progress": normalized}
        if message is not None:
            data["message"] = message
        await self._update(data)
        self._last_persisted_progress = normalized
        if message is not None:
            self._last_persisted_message = message

    async def set_message(self, message: str) -> None:
        await self._update({"message": message})
        self._last_persisted_message = message

    async def set_result(self, result: Any) -> None:
        await self._update({"result": result})

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    async def run_owned_transaction(self, operation, *, check_cancelled: bool = False) -> None:
        """在 attempt lease 行锁内提交领域 checkpoint，可在写入前响应取消。"""

        async def write_checkpoint(session, record) -> None:
            """使用当前行锁下的取消状态决定是否进入领域写入。"""
            if check_cancelled:
                self._raise_if_cancel_requested(bool(record.cancel_requested))
            await operation(session, record)

        if not await self._repo.run_owned_transaction(
            self.job_id,
            worker_id=self.worker_id,
            operation=write_checkpoint,
        ):
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("BackgroundJob lease was lost")

    async def raise_if_cancelled(self) -> None:
        owns_lease, cancel_requested = await self._repo.check_control(
            self.job_id,
            worker_id=self.worker_id,
        )
        if not owns_lease:
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("BackgroundJob lease was lost")
        self._raise_if_cancel_requested(cancel_requested)

    def _raise_if_cancel_requested(self, cancel_requested: bool) -> None:
        """记录正常取消原因，交由 worker 收敛终态。"""
        if cancel_requested:
            self._request_cancel("cancelled")
            raise asyncio.CancelledError("BackgroundJob was cancelled")

    def _request_cancel(self, reason: str) -> None:
        self._cancel_requested = reason == "cancelled"
        self.cancellation_reason = reason

    async def _update(self, data: dict[str, Any]) -> None:
        if not await self._reporter.report(self.job_id, worker_id=self.worker_id, **data):
            self.cancellation_reason = "lease_lost"
            raise asyncio.CancelledError("BackgroundJob lease was lost")
