from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.document_trash_service import DocumentTrashService


def row(**changes):
    values = dict(
        file_id="f",
        kb_id="kb",
        filename="合同.pdf",
        is_folder=False,
        deleted_at=datetime.now(UTC),
        purge_after=datetime.now(UTC) + timedelta(days=30),
        purge_error=None,
        purge_started_at=None,
        deletion_id="d",
        purge_token="t",
    )
    return SimpleNamespace(**(values | changes))


@pytest.mark.asyncio
async def test_list_projects_lifecycle_without_exposing_internal_error():
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service._refresh_stats = AsyncMock()
    service.repository.list_trashed.return_value = [row(), row(purge_error="secret storage endpoint")]
    service.repository.count_trashed.return_value = 31
    result = await service.list_items("kb", offset=10, limit=10)
    assert result["total"] == 31
    assert result["items"][0]["status"] == "trashed"
    assert result["items"][1]["status"] == "purge_failed"
    assert "secret" not in str(result)
    service.repository.list_trashed.assert_awaited_once_with("kb", offset=10, limit=10)


@pytest.mark.asyncio
async def test_trash_and_restore_delegate_without_destroying_artifacts():
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service._refresh_stats = AsyncMock()
    service.repository.trash.return_value = [row()]
    service.repository.restore.return_value = [row()]
    service._purge_one = AsyncMock()
    assert await service.trash("kb", ["f"], deleted_by="user") == 1
    assert await service.restore("kb", "f") == 1
    service._purge_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_failure_is_recorded_and_never_reported_complete():
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service._refresh_stats = AsyncMock()
    service.repository.claim_due.side_effect = [[row()], []]
    service._purge_one = AsyncMock(side_effect=OSError("private connection info"))
    result = await service.purge_due(limit=2)
    assert result == {"completed": 0, "failed": 1}
    service.repository.fail_purge.assert_awaited_once_with("f", "d", "t", "OSError")
    service.repository.complete_purge.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_empty_batch_does_no_work():
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service._refresh_stats = AsyncMock()
    service.repository.claim_due.return_value = []
    service._purge_one = AsyncMock()
    assert await service.purge_due() == {"completed": 0, "failed": 0}
    service._purge_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_namespaced_image_requires_active_owner():
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service._refresh_stats = AsyncMock()
    service.repository.list_storage_references.return_value = [row()]
    assert not await service.image_is_active("kb", "kb-images/f/image.png")
    service.repository.list_storage_references.return_value = [row(deleted_at=None)]
    assert await service.image_is_active("kb", "kb-images/f/image.png")
    assert not await service.image_is_active("kb", "kb-images/other/image.png")


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_storage_locks_until_thread_finishes(monkeypatch):
    """两次取消仍不释放引用锁，直到真实同步I/O线程返回。"""
    import asyncio
    import threading
    from contextlib import asynccontextmanager
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    lock = asyncio.Lock()
    tree_lock = asyncio.Lock()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    @asynccontextmanager
    async def storage_lock(_self):
        async with lock:
            yield

    @asynccontextmanager
    async def file_lock(_self, _kb):
        async with tree_lock:
            yield

    def storage_io():
        started.set()
        release.wait(5)
        finished.set()

    async def purge_locked(_row, _executor):
        await asyncio.to_thread(storage_io)

    monkeypatch.setattr(KnowledgeFileRepository, "lock_storage_references", storage_lock)
    monkeypatch.setattr(KnowledgeFileRepository, "lock_file_tree", file_lock)
    service = DocumentTrashService()
    monkeypatch.setattr(service, "_purge_locked", purge_locked)
    pending = asyncio.create_task(service._purge_one(row(is_folder=True)))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        assert lock.locked() and tree_lock.locked()
        assert not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert finished.is_set()
    assert not lock.locked() and not tree_lock.locked()
