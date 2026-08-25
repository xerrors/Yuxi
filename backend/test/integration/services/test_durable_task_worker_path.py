"""真实 ARQ worker 恢复未发布 PostgreSQL Durable Task 的 assembled path。"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import delete

from yuxi.knowledge.eval.service import EvaluationService
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services import task_service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件直接使用 shipping PostgreSQL 与 worker，不依赖 HTTP API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    yield


def _gate_identity() -> tuple[str, str]:
    suffix = os.getenv("DURABLE_TASK_GATE_ID", "local").replace("-", "_")[:24]
    return f"kb_task_worker_{suffix}", f"durable-task-worker-{suffix}"


async def _delete_gate_facts(kb_id: str) -> None:
    datasets = await EvaluationRepository().list_datasets(kb_id)
    task_ids = [str((dataset.build_metadata or {}).get("task_id") or "") for dataset in datasets]
    async with pg_manager.get_async_session_context() as session:
        if task_ids:
            await session.execute(delete(TaskRecord).where(TaskRecord.id.in_(task_ids)))
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))


async def test_prepare_task_with_failed_initial_arq_publication(monkeypatch) -> None:
    """在 worker 停止时留下已提交但尚未发布的 Task intent。"""
    kb_id, dataset_name = _gate_identity()
    await _delete_gate_facts(kb_id)
    async with pg_manager.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name=dataset_name, kb_type="milvus"))

    async def fail_initial_publication(_task_id: str) -> None:
        raise ConnectionError("simulated Redis publication failure")

    monkeypatch.setattr(task_service, "publish_task", fail_initial_publication)
    submitted = await EvaluationService().generate_dataset(
        kb_id=kb_id,
        name=dataset_name,
        description="deterministic worker path",
        count=0,
        neighbors_count=1,
        concurrency_count=1,
        llm_model_spec="unused:model",
        created_by="integration",
    )

    task = await TaskRepository().get_by_id(submitted["task_id"])
    dataset = await EvaluationRepository().get_dataset(submitted["dataset_id"])
    assert task.status == "pending"
    assert (dataset.build_metadata or {}).get("status") == "pending"


async def test_shipping_worker_startup_recovers_pending_publication() -> None:
    """由真实 worker startup publisher 恢复前一进程遗留的 pending intent。"""
    kb_id, _dataset_name = _gate_identity()
    datasets = await EvaluationRepository().list_datasets(kb_id)
    assert len(datasets) == 1
    dataset = datasets[0]
    task_id = str((dataset.build_metadata or {}).get("task_id") or "")
    assert task_id

    try:
        for _attempt in range(150):
            task = await TaskRepository().get_by_id(task_id)
            dataset = await EvaluationRepository().get_dataset(dataset.dataset_id)
            if task and task.status == "success" and (dataset.build_metadata or {}).get("status") == "completed":
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("Durable Task did not converge through worker startup publication")

        assert task.attempt_count == 1
        assert task.worker_id is None
        assert (dataset.build_metadata or {}).get("task_id") == task_id
    finally:
        await _delete_gate_facts(kb_id)
