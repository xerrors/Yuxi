from unittest.mock import AsyncMock

import pytest

from yuxi.services import run_worker
from yuxi.services.document_trash_service import document_trash_service


@pytest.mark.asyncio
async def test_existing_worker_registers_and_executes_hourly_trash_cleanup(monkeypatch):
    job = next(
        job for job in run_worker.WorkerSettings.cron_jobs if job.coroutine is run_worker.purge_expired_documents
    )
    assert job.minute == {0}
    cleanup = AsyncMock(return_value={"completed": 1, "failed": 0})
    monkeypatch.setattr(document_trash_service, "purge_due", cleanup)
    assert await job.coroutine({}) == {"completed": 1, "failed": 0}
    cleanup.assert_awaited_once_with()
