"""Durable Task 的真实 PostgreSQL claim、lease、去重与失联收敛测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.knowledge.eval.service import EvaluationService, finish_dataset_generation_task
from yuxi.repositories import evaluation_repository as evaluation_repository_module
from yuxi.repositories import knowledge_file_repository as knowledge_file_repository_module
from yuxi.repositories import task_repository as task_repository_module
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services import task_queue_service
from yuxi.services.task_queue_service import finalize_task_failure
from yuxi.services.task_service import TaskContext, Tasker
from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.storage.postgres.models_knowledge import EvaluationDataset, EvaluationRun, KnowledgeBase, KnowledgeFile
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件使用隔离 Schema，不依赖运行 API 的业务 Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试没有 HTTP 知识资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 测试没有 Sandbox 资源。"""
    yield


@pytest.fixture
async def durable_task_schema(monkeypatch):
    """在独立 PostgreSQL Schema 中验证 Task lease，不修改运行实例业务表。"""
    schema = f"pytest_durable_task_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with scoped_engine.begin() as connection:
        await connection.run_sync(TaskRecord.__table__.create)
        await connection.run_sync(KnowledgeBase.__table__.create)
        await connection.run_sync(KnowledgeFile.__table__.create)
        await connection.run_sync(EvaluationDataset.__table__.create)
        await connection.run_sync(EvaluationRun.__table__.create)

    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = scoped_engine
    manager.AsyncSession = async_sessionmaker(scoped_engine, class_=AsyncSession, expire_on_commit=False)
    manager._initialized = True
    monkeypatch.setattr(task_repository_module, "pg_manager", manager)
    monkeypatch.setattr(evaluation_repository_module, "pg_manager", manager)
    monkeypatch.setattr(knowledge_file_repository_module, "pg_manager", manager)

    try:
        yield manager
    finally:
        await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def _task_data(*, dedupe_key: str | None = None, recovery_strategy: str = "fail") -> dict:
    now = utc_now_naive()
    return {
        "name": "pytest durable task",
        "type": "knowledge_parse",
        "status": "pending",
        "progress": 0.0,
        "message": "等待执行",
        "payload": {"kb_id": "pytest-kb"},
        "result": None,
        "error": None,
        "cancel_requested": 0,
        "recovery_strategy": recovery_strategy,
        "handler_version": 1,
        "dedupe_key": dedupe_key,
        "attempt_count": 0,
        "timeout_seconds": 60.0,
        "created_at": now,
        "updated_at": now,
    }


async def test_concurrent_claim_has_single_owner_and_rejects_late_writer(durable_task_schema) -> None:
    task_id = uuid.uuid4().hex
    repo = TaskRepository()
    await repo.create(task_id, _task_data())
    now = utc_now_naive()

    claims = await asyncio.gather(
        repo.claim(task_id, worker_id="owner-a", lease_seconds=30, now=now),
        repo.claim(task_id, worker_id="owner-b", lease_seconds=30, now=now),
    )
    winners = [(record.worker_id, claimed) for record, claimed in claims if claimed]
    assert winners in [
        [("owner-a", True)],
        [("owner-b", True)],
    ]
    winner = winners[0][0]

    reconciled = await repo.reconcile_expired_leases(now=now + timedelta(seconds=31))
    assert reconciled == [(task_id, "failed", 1)]
    assert (
        await repo.update_owned(
            task_id,
            worker_id=winner,
            data={"message": "迟到结果"},
            now=now + timedelta(seconds=31),
        )
        is False
    )

    persisted = await repo.get_by_id(task_id)
    assert persisted.status == "failed"
    assert persisted.error.startswith("worker_lease_expired")
    assert persisted.worker_id is None


async def test_restart_policy_requeues_expired_task(durable_task_schema) -> None:
    task_id = uuid.uuid4().hex
    repo = TaskRepository()
    await repo.create(task_id, _task_data(dedupe_key=uuid.uuid4().hex, recovery_strategy="restart"))
    now = utc_now_naive()

    _, claimed = await repo.claim(task_id, worker_id="owner-a", lease_seconds=5, now=now)
    assert claimed is True

    reconciled = await repo.reconcile_expired_leases(now=now + timedelta(seconds=6))

    assert reconciled == [(task_id, "pending", 1)]
    persisted = await repo.get_by_id(task_id)
    assert persisted.status == "pending"
    assert persisted.dedupe_key is not None
    assert persisted.worker_id is None


async def test_lock_wait_cannot_reuse_time_from_before_lease_expiry(durable_task_schema) -> None:
    task_id = uuid.uuid4().hex
    repo = TaskRepository()
    await repo.create(task_id, _task_data())
    _, claimed = await repo.claim(task_id, worker_id="owner-a", lease_seconds=0.1)
    assert claimed is True

    async with durable_task_schema.get_async_session_context() as blocker:
        await blocker.execute(text("SELECT id FROM tasks WHERE id = :task_id FOR UPDATE"), {"task_id": task_id})
        late_update = asyncio.create_task(repo.update_owned(task_id, worker_id="owner-a", data={"message": "迟到更新"}))
        await asyncio.sleep(0.2)

    assert await late_update is False
    assert (
        await repo.finish_owned(
            task_id,
            worker_id="owner-a",
            status="success",
            message="迟到完成",
        )
        is False
    )
    persisted = await repo.get_by_id(task_id)
    assert persisted.status == "running"


async def test_domain_callback_lock_wait_rolls_back_after_lease_expiry(durable_task_schema) -> None:
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(EvaluationDataset(dataset_id=dataset_id, kb_id=kb_id, name="pytest", item_count=0))

    repo = TaskRepository()
    await repo.create(task_id, _task_data())
    _record, claimed = await repo.claim(task_id, worker_id="owner-a", lease_seconds=0.1)
    assert claimed is True

    async def operation(session, _task_record) -> None:
        await EvaluationRepository.update_dataset_in_session(session, dataset_id, {"item_count": 99})

    async with durable_task_schema.get_async_session_context() as blocker:
        await blocker.execute(
            text("SELECT dataset_id FROM evaluation_datasets WHERE dataset_id = :dataset_id FOR UPDATE"),
            {"dataset_id": dataset_id},
        )
        checkpoint = asyncio.create_task(repo.run_owned_transaction(task_id, worker_id="owner-a", operation=operation))
        await asyncio.sleep(0.2)

    assert await checkpoint is False
    assert (await EvaluationRepository().get_dataset(dataset_id)).item_count == 0


async def test_terminal_hook_lock_wait_rolls_back_after_lease_expiry(durable_task_schema) -> None:
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(EvaluationDataset(dataset_id=dataset_id, kb_id=kb_id, name="pytest", item_count=0))

    repo = TaskRepository()
    await repo.create(task_id, _task_data())
    _record, claimed = await repo.claim(task_id, worker_id="owner-a", lease_seconds=0.1)
    assert claimed is True

    async def before_finish(session, _task_record) -> None:
        await EvaluationRepository.update_dataset_in_session(session, dataset_id, {"item_count": 99})

    async with durable_task_schema.get_async_session_context() as blocker:
        await blocker.execute(
            text("SELECT dataset_id FROM evaluation_datasets WHERE dataset_id = :dataset_id FOR UPDATE"),
            {"dataset_id": dataset_id},
        )
        terminal = asyncio.create_task(
            repo.finish_owned(
                task_id,
                worker_id="owner-a",
                status="success",
                message="完成",
                before_finish=before_finish,
            )
        )
        await asyncio.sleep(0.2)

    assert await terminal is False
    assert (await EvaluationRepository().get_dataset(dataset_id)).item_count == 0
    assert (await repo.get_by_id(task_id)).status == "running"


async def test_durable_capacity_reserves_worker_slots_for_agent_runs(durable_task_schema) -> None:
    repo = TaskRepository()
    task_ids = [uuid.uuid4().hex for _ in range(5)]
    for task_id in task_ids:
        await repo.create(task_id, _task_data())

    for index, task_id in enumerate(task_ids[:4]):
        _record, claimed = await repo.claim(
            task_id,
            worker_id=f"owner-{index}",
            lease_seconds=30,
            max_running=4,
        )
        assert claimed is True

    _record, claimed = await repo.claim(
        task_ids[4],
        worker_id="owner-4",
        lease_seconds=30,
        max_running=4,
    )
    assert claimed is False
    assert (await repo.get_by_id(task_ids[4])).status == "pending"


async def test_knowledge_task_failure_fences_file_intermediate_state(durable_task_schema) -> None:
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    file_id = f"file_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    owner = "owner-a"
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            KnowledgeFile(
                kb_id=kb_id,
                file_id=file_id,
                filename="pytest.txt",
                status="uploaded",
                is_folder=False,
            )
        )

    repo = TaskRepository()
    await repo.create(
        task_id,
        {
            **_task_data(),
            "type": "knowledge_parse",
            "payload": {"kb_id": kb_id, "scope": "files", "file_ids": [file_id]},
        },
    )
    _record, claimed = await repo.claim(task_id, worker_id=owner, lease_seconds=0.5)
    assert claimed is True
    claimed_file = await KnowledgeFileRepository().update_fields_if_status(
        kb_id=kb_id,
        file_id=file_id,
        allowed_statuses={"uploaded"},
        data={
            "status": "parsing",
            "processing_task_id": task_id,
            "processing_owner": owner,
        },
    )
    assert claimed_file is not None

    async with durable_task_schema.get_async_session_context() as blocker:
        await blocker.execute(
            text("SELECT id FROM knowledge_files WHERE file_id = :file_id FOR UPDATE"),
            {"file_id": file_id},
        )
        late_write = asyncio.create_task(
            KnowledgeFileRepository().update_fields_if_status(
                kb_id=kb_id,
                file_id=file_id,
                allowed_statuses={"parsing"},
                data={"status": "parsed", "processing_task_id": None, "processing_owner": None},
                processing_task_id=task_id,
                processing_owner=owner,
            )
        )
        await asyncio.sleep(0.6)

    assert await late_write is None
    assert (await KnowledgeFileRepository().get_by_file_id(file_id)).status == "parsing"

    reconciled = await repo.reconcile_expired_leases(
        now=utc_now_naive(),
        before_fail=finalize_task_failure,
    )
    assert reconciled == [(task_id, "failed", 1)]
    file_record = await KnowledgeFileRepository().get_by_file_id(file_id)
    assert file_record.status == "error_parsing"
    assert file_record.processing_task_id is None
    assert file_record.processing_owner is None


async def test_full_worker_fails_legacy_task_through_current_domain_hook(
    durable_task_schema,
    monkeypatch,
) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "pending", "task_id": task_id},
            )
        )

    await TaskRepository().create(
        task_id,
        {
            **_task_data(),
            "type": "dataset_generation",
            "handler_version": 0,
            "payload": {"dataset_id": dataset_id},
        },
    )
    published = False

    async def reject_publish(_task_id: str) -> None:
        nonlocal published
        published = True

    monkeypatch.setattr(task_queue_service, "publish_task", reject_publish)
    await task_queue_service.publish_pending_tasks()

    assert published is False
    assert (await TaskRepository().get_by_id(task_id)).status == "failed"
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.build_metadata["status"] == "failed"


async def test_full_worker_cancels_legacy_pending_cancel_intent_with_domain_hook(
    durable_task_schema,
) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "pending", "task_id": task_id},
            )
        )

    await TaskRepository().create(
        task_id,
        {
            **_task_data(),
            "type": "dataset_generation",
            "handler_version": 0,
            "cancel_requested": 1,
            "payload": {"dataset_id": dataset_id},
        },
    )

    await task_queue_service.publish_pending_tasks()

    assert (await TaskRepository().get_by_id(task_id)).status == "cancelled"
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.build_metadata["status"] == "failed"
    assert dataset.build_metadata["message"] == "任务已取消"


async def test_pending_dataset_cancel_commits_domain_and_task_terminal_together(durable_task_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "pending", "task_id": task_id},
            )
        )

    await TaskRepository().create(
        task_id,
        {**_task_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )

    cancelled = await Tasker().cancel_task(task_id)

    assert cancelled.status == "cancelled"
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.build_metadata["status"] == "failed"
    assert dataset.build_metadata["message"] == "任务已取消"


async def test_cancel_request_wins_race_with_successful_handler(durable_task_schema) -> None:
    task_id = uuid.uuid4().hex
    repo = TaskRepository()
    await repo.create(task_id, _task_data())
    _, claimed = await repo.claim(task_id, worker_id="owner-a", lease_seconds=30)
    assert claimed is True
    assert await repo.request_cancel(task_id) is not None

    assert (
        await repo.finish_owned(
            task_id,
            worker_id="owner-a",
            status="success",
            message="任务已完成",
            result={"ignored": True},
        )
        is True
    )

    persisted = await repo.get_by_id(task_id)
    assert persisted.status == "cancelled"
    assert persisted.message == "任务已取消"
    assert persisted.result is None

    failed_task_id = uuid.uuid4().hex
    await repo.create(failed_task_id, _task_data())
    _, claimed = await repo.claim(failed_task_id, worker_id="owner-b", lease_seconds=30)
    assert claimed is True
    assert await repo.request_cancel(failed_task_id) is not None
    assert (
        await repo.finish_owned(
            failed_task_id,
            worker_id="owner-b",
            status="failed",
            message="执行失败",
            error="boom",
        )
        is True
    )
    assert (await repo.get_by_id(failed_task_id)).status == "cancelled"


async def test_dataset_completion_and_task_success_share_owner_transaction(durable_task_schema) -> None:
    now = utc_now_naive()
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "running", "task_id": task_id},
            )
        )

    await TaskRepository().create(
        task_id,
        {**_task_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    await TaskRepository().claim(task_id, worker_id="owner-a", lease_seconds=30, now=now)
    result = {
        "dataset_id": dataset_id,
        "item_count": 3,
        "build_metadata": {"source": "generated", "status": "completed", "task_id": task_id},
    }

    async def finish_dataset(session, task_record):
        await finish_dataset_generation_task(session, task_record, result)

    assert (
        await TaskRepository().finish_owned(
            task_id,
            worker_id="owner-a",
            status="success",
            message="完成",
            result=result,
            before_finish=finish_dataset,
            now=now + timedelta(seconds=1),
        )
        is True
    )

    task = await TaskRepository().get_by_id(task_id)
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert task.status == "success"
    assert dataset.item_count == 3
    assert dataset.build_metadata["status"] == "completed"


async def test_expired_owner_cannot_commit_dataset_completion(durable_task_schema) -> None:
    now = utc_now_naive()
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "running", "task_id": task_id},
            )
        )

    await TaskRepository().create(
        task_id,
        {**_task_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    await TaskRepository().claim(task_id, worker_id="owner-a", lease_seconds=5, now=now)
    result = {
        "dataset_id": dataset_id,
        "item_count": 3,
        "build_metadata": {"source": "generated", "status": "completed", "task_id": task_id},
    }

    async def finish_dataset(session, task_record):
        await finish_dataset_generation_task(session, task_record, result)

    assert (
        await TaskRepository().finish_owned(
            task_id,
            worker_id="owner-a",
            status="success",
            message="完成",
            result=result,
            before_finish=finish_dataset,
            now=now + timedelta(seconds=6),
        )
        is False
    )

    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.item_count == 0
    assert dataset.build_metadata["status"] == "running"


async def test_old_dataset_attempt_cannot_write_after_resume_claim(durable_task_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    old_task_id = uuid.uuid4().hex
    new_task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "running", "task_id": old_task_id},
            )
        )

    await TaskRepository().create(
        old_task_id,
        {**_task_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    await TaskRepository().claim(old_task_id, worker_id="old-owner", lease_seconds=0.1)
    await asyncio.sleep(0.2)
    await TaskRepository().reconcile_expired_leases(before_fail=finalize_task_failure)

    await TaskRepository().create(
        new_task_id,
        {**_task_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    async with evaluation_repository_module.pg_manager.get_async_session_context() as session:
        await EvaluationRepository.attach_dataset_generation_task_in_session(session, dataset_id, new_task_id)
    await TaskRepository().claim(new_task_id, worker_id="new-owner", lease_seconds=30)
    old_context = TaskContext(old_task_id, "old-owner", {"dataset_id": dataset_id})

    async def stale_write(session, _task_record):
        await EvaluationRepository.update_dataset_in_session(session, dataset_id, {"item_count": 99})

    with pytest.raises(asyncio.CancelledError):
        await old_context.run_owned_transaction(stale_write)

    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.item_count == 0
    assert dataset.build_metadata["task_id"] == new_task_id
    assert dataset.build_metadata["status"] == "pending"


async def test_late_dataset_attachment_preserves_same_task_failure(durable_task_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={
                    "source": "generated",
                    "status": "failed",
                    "task_id": task_id,
                    "error_message": "handler failed",
                },
            )
        )

    async with evaluation_repository_module.pg_manager.get_async_session_context() as session:
        await EvaluationRepository.attach_dataset_generation_task_in_session(session, dataset_id, task_id)

    persisted = await EvaluationRepository().get_dataset(dataset_id)
    assert persisted.build_metadata["status"] == "failed"
    assert persisted.build_metadata["error_message"] == "handler failed"


async def test_expired_evaluation_task_converges_run_when_observed(durable_task_schema) -> None:
    now = utc_now_naive()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"status": "completed"},
            )
        )
        await session.flush()
        session.add(
            EvaluationRun(
                run_id=run_id,
                name="pytest",
                kb_id=kb_id,
                dataset_id=dataset_id,
                status="running",
                total_items=0,
                completed_items=0,
                started_at=now,
            )
        )

    task_id = uuid.uuid4().hex
    await TaskRepository().create(
        task_id,
        {
            **_task_data(),
            "type": "rag_evaluation",
            "payload": {"run_id": run_id},
        },
    )
    await TaskRepository().claim(task_id, worker_id="owner-a", lease_seconds=5, now=now)
    await TaskRepository().reconcile_expired_leases(
        before_fail=finalize_task_failure,
        now=now + timedelta(seconds=6),
    )

    persisted = await EvaluationRepository().get_run(run_id)
    assert persisted.status == "failed"
    runs = await EvaluationService().list_runs(kb_id)

    assert runs[0]["status"] == "failed"
    persisted = await EvaluationRepository().get_run(run_id)
    assert persisted.status == "failed"
    assert persisted.metrics == {"error": "worker_lease_expired: 执行 worker 的 lease 已过期，任务副作用结果未知"}


async def test_worker_shutdown_commits_evaluation_failure_with_task(durable_task_schema) -> None:
    now = utc_now_naive()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    async with durable_task_schema.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
        await session.flush()
        session.add(EvaluationDataset(dataset_id=dataset_id, kb_id=kb_id, name="pytest", item_count=0))
        await session.flush()
        session.add(
            EvaluationRun(
                run_id=run_id,
                name="pytest",
                kb_id=kb_id,
                dataset_id=dataset_id,
                status="running",
                started_at=now,
            )
        )

    await TaskRepository().create(
        task_id,
        {**_task_data(), "type": "rag_evaluation", "payload": {"run_id": run_id}},
    )
    await TaskRepository().claim(task_id, worker_id="owner-a", lease_seconds=30, now=now)
    error = "worker_shutdown: worker 停止时任务中断"

    async def finalize_failure(session, record):
        await finalize_task_failure(session, record, error)

    assert (
        await TaskRepository().release_interrupted_owner(
            task_id,
            worker_id="owner-a",
            error=error,
            before_fail=finalize_failure,
            now=now + timedelta(seconds=1),
        )
        == "failed"
    )

    assert (await TaskRepository().get_by_id(task_id)).status == "failed"
    run = await EvaluationRepository().get_run(run_id)
    assert run.status == "failed"
    assert run.metrics == {"error": error}


async def test_payload_lookup_finds_active_task_beyond_recent_history_window(durable_task_schema) -> None:
    target_id = uuid.uuid4().hex
    now = utc_now_naive()
    async with durable_task_schema.get_async_session_context() as session:
        session.add(
            TaskRecord(
                id=target_id,
                **_task_data(),
            )
        )
        session.add_all(
            TaskRecord(
                id=uuid.uuid4().hex,
                **{
                    **_task_data(),
                    "status": "failed",
                    "payload": {"dataset_id": f"history-{index}"},
                    "created_at": now + timedelta(seconds=index + 1),
                    "updated_at": now + timedelta(seconds=index + 1),
                },
            )
            for index in range(250)
        )

    found = await TaskRepository().find_latest_by_payload(
        task_type="knowledge_parse",
        payload_match={"kb_id": "pytest-kb"},
        statuses={"pending", "running"},
    )

    assert found is not None
    assert found.id == target_id

    listing = await Tasker().list_tasks(limit=100)
    assert any(task["id"] == target_id for task in listing["tasks"])
    assert listing["summary"]["total"] == 251
    assert listing["summary"]["status_counts"]["pending"] == 1


async def test_concurrent_dedupe_creates_one_active_task(durable_task_schema) -> None:
    task_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    dedupe_key = uuid.uuid4().hex
    repo = TaskRepository()

    results = await asyncio.gather(
        repo.create(task_ids[0], _task_data(dedupe_key=dedupe_key)),
        repo.create(task_ids[1], _task_data(dedupe_key=dedupe_key)),
    )

    created = [(record.id, is_created) for record, is_created in results if is_created]
    reused = [(record.id, is_created) for record, is_created in results if not is_created]
    assert len(created) == 1
    assert reused == [(created[0][0], False)]
