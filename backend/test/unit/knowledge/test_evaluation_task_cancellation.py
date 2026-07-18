import asyncio
from copy import deepcopy

import pytest

from yuxi.knowledge.eval import service as evaluation_module
from yuxi.knowledge.eval.service import EvaluationService


class FakeContext:
    def __init__(self, payload: dict, *, cancel_requested: bool, cancellation_reason: str | None = None):
        self.payload = payload
        self.task_id = "task-1"
        self.cancel_requested = cancel_requested
        self.cancellation_reason = cancellation_reason
        self.messages: list[str] = []

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_message(self, message: str) -> None:
        self.messages.append(message)

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested


class FakeEvaluationRepository:
    def __init__(self):
        self.dataset_updates: list[tuple[str, dict]] = []
        self.run_updates: list[tuple[str, dict]] = []

    async def count_dataset_items(self, dataset_id: str) -> int:
        return 0

    async def update_dataset(self, dataset_id: str, data: dict) -> None:
        await asyncio.sleep(0)
        self.dataset_updates.append((dataset_id, deepcopy(data)))

    async def get_dataset(self, dataset_id: str):
        raise asyncio.CancelledError

    async def update_run(self, run_id: str, data: dict) -> None:
        await asyncio.sleep(0)
        self.run_updates.append((run_id, deepcopy(data)))


async def test_dataset_cancellation_updates_build_status(monkeypatch):
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "dataset_id": "dataset-1",
            "kb_id": "kb-1",
            "count": 10,
            "neighbors_count": 1,
            "concurrency_count": 1,
            "llm_model_spec": "provider:model",
            "generation_mode": "vector",
            "graph_expand_top_k": 1,
        },
        cancel_requested=True,
    )

    loading = asyncio.Event()

    async def cancelled_get_kb(kb_id: str):
        loading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(evaluation_module.knowledge_base, "aget_kb", cancelled_get_kb)

    task = asyncio.create_task(service._generate_dataset_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    build_metadata = repo.dataset_updates[-1][1]["build_metadata"]
    assert build_metadata["status"] == "failed"
    assert build_metadata["error_message"] == "任务已取消"


async def test_evaluation_timeout_updates_run_status():
    repo = FakeEvaluationRepository()
    service = EvaluationService.__new__(EvaluationService)
    service.eval_repo = repo
    context = FakeContext(
        {
            "run_id": "run-1",
            "kb_id": "kb-1",
            "dataset_id": "dataset-1",
            "retrieval_config": {},
        },
        cancel_requested=False,
        cancellation_reason="timeout",
    )

    loading = asyncio.Event()

    async def cancelled_get_dataset(dataset_id: str):
        loading.set()
        await asyncio.Event().wait()

    repo.get_dataset = cancelled_get_dataset
    task = asyncio.create_task(service._run_evaluation_task(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run_id, update = repo.run_updates[-1]
    assert run_id == "run-1"
    assert update["status"] == "failed"
    assert update["metrics"] == {"error": "任务执行超时"}
    assert context.messages == ["Error: 任务执行超时"]
