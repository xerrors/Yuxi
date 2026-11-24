import asyncio
from copy import deepcopy

import pytest

from yuxi.modules.knowledge.evaluation import service as evaluation_module
from yuxi.modules.knowledge.evaluation.service import EvaluationService


class FakeContext:
    def __init__(self, payload: dict, *, cancel_requested: bool, cancellation_reason: str | None = None):
        self.payload = payload
        self.job_id = "job-1"
        self.cancel_requested = cancel_requested
        self.cancellation_reason = cancellation_reason
        self.messages: list[str] = []

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_message(self, message: str) -> None:
        self.messages.append(message)

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested

    async def run_owned_transaction(self, operation, *, check_cancelled=False) -> None:
        await operation(None, None)


class FakeEvaluationRepository:
    def __init__(self):
        self.dataset_updates: list[tuple[str, dict]] = []
        self.run_updates: list[tuple[str, dict]] = []

    async def count_dataset_items(self, dataset_id: str) -> int:
        return 0

    async def update_dataset_in_session(self, session, dataset_id: str, data: dict):
        await asyncio.sleep(0)
        self.dataset_updates.append((dataset_id, deepcopy(data)))
        return object()

    async def update_dataset(self, dataset_id: str, data: dict) -> None:
        await self.update_dataset_in_session(None, dataset_id, data)

    async def get_dataset(self, dataset_id: str):
        raise asyncio.CancelledError

    async def update_run_in_session(self, session, run_id: str, data: dict):
        """记录在执行权事务内发生的 Run 写入。"""
        self.run_updates.append((run_id, deepcopy(data)))
        return object()


async def test_dataset_cancellation_defers_terminal_status_to_job_hook(monkeypatch):
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

    async def cancelled_get_config(kb_id: str):
        loading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(evaluation_module.kb_manager, "get_kb_config", cancelled_get_config)

    job = asyncio.create_task(service._generate_dataset_job(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    build_metadata = repo.dataset_updates[-1][1]["build_metadata"]
    assert build_metadata["status"] == "running"
    assert "error_message" not in build_metadata


async def test_evaluation_timeout_defers_run_terminal_to_job_hook():
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
    job = asyncio.create_task(service._run_evaluation_job(context))
    await asyncio.wait_for(loading.wait(), timeout=1)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert repo.run_updates == []
    assert context.messages == ["评估执行中断，请查看业务资源状态或日志"]
