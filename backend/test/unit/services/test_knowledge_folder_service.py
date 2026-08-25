import pytest

from yuxi.services.knowledge_folder_service import KnowledgeFolderService

pytestmark = pytest.mark.asyncio


class FakeContext:
    def __init__(self) -> None:
        self.transactions = 0
        self.result = None

    async def raise_if_cancelled(self) -> None:
        return None

    async def run_owned_transaction(self, operation) -> None:
        self.transactions += 1
        await operation("owned-session", object())

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        self.result = result


class FakeRepository:
    def __init__(self) -> None:
        self.batches = [
            {
                "scanned": 1,
                "processed": 1,
                "created_folders": 1,
                "conflict_file_ids": [],
                "last_file_id": "file-1",
            },
            {"scanned": 0, "processed": 0, "created_folders": 0, "conflict_file_ids": []},
            {"scanned": 0, "processed": 0, "created_folders": 0, "conflict_file_ids": []},
        ]
        self.sessions = []

    async def detect_virtual_folder_data(self, kb_id: str) -> dict:
        if self.batches:
            return {"remaining_steps": 1, "file_count": 1}
        return {"remaining_steps": 0, "file_count": 0}

    async def migrate_virtual_folder_batch(self, session, **kwargs) -> dict:
        self.sessions.append(session)
        return self.batches.pop(0)


async def test_virtual_folder_batches_run_inside_task_owned_transaction() -> None:
    repository = FakeRepository()
    context = FakeContext()

    result = await KnowledgeFolderService(repository).migrate_virtual_folder_data(
        context,
        kb_id="kb-1",
        operator_id="user-1",
    )

    assert context.transactions == 3
    assert repository.sessions == ["owned-session"] * 3
    assert result == {
        "processed_steps": 1,
        "created_folders": 1,
        "conflict_files": 0,
        "remaining_files": 0,
    }
