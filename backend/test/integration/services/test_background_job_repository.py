"""后台作业 的真实 PostgreSQL claim、lease、去重与失联收敛测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.modules.knowledge.evaluation.service import EvaluationService, finish_dataset_generation_job
import yuxi.modules.knowledge.repositories.evaluation as evaluation_repository_module
import yuxi.modules.knowledge.repositories.files as knowledge_file_repository_module
import yuxi.modules.background_jobs.repository as job_repository_module
from yuxi.modules.knowledge.repositories.evaluation import EvaluationRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.workers.background_jobs import finalize_job_failure, publish_pending_jobs
from yuxi.modules.background_jobs.service import JobTracker
from yuxi.workers.background_job_context import BackgroundJobContext
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.models import EvaluationDataset, EvaluationRun, KnowledgeBase, KnowledgeFile
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def background_job_schema(monkeypatch):
    """在独立 PostgreSQL Schema 中验证 BackgroundJob lease，不修改运行实例业务表。"""
    schema = f"pytest_background_job_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with scoped_engine.begin() as connection:
        await connection.run_sync(BackgroundJobRecord.__table__.create)
        await connection.run_sync(KnowledgeBase.__table__.create)
        await connection.run_sync(KnowledgeFile.__table__.create)
        await connection.run_sync(EvaluationDataset.__table__.create)
        await connection.run_sync(EvaluationRun.__table__.create)

    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = scoped_engine
    manager.AsyncSession = async_sessionmaker(scoped_engine, class_=AsyncSession, expire_on_commit=False)
    manager._initialized = True
    monkeypatch.setattr(job_repository_module, "pg_manager", manager)
    monkeypatch.setattr(evaluation_repository_module, "pg_manager", manager)
    monkeypatch.setattr(knowledge_file_repository_module, "pg_manager", manager)

    try:
        yield manager
    finally:
        await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def _job_data(*, dedupe_key: str | None = None) -> dict:
    now = utc_now()
    return {
        "name": "pytest durable job",
        "type": "knowledge_parse",
        "status": "pending",
        "progress": 0.0,
        "message": "等待执行",
        "payload": {"kb_id": "pytest-kb"},
        "result": None,
        "error": None,
        "cancel_requested": 0,
        "handler_version": 1,
        "dedupe_key": dedupe_key,
        "attempt_count": 0,
        "timeout_seconds": 60.0,
        "created_at": now,
        "updated_at": now,
    }


async def _seed_knowledge_base(session, kb_id: str) -> None:
    """建立后台知识作业共享的 KB 前置事实。"""
    session.add(KnowledgeBase(kb_id=kb_id, name="pytest", kb_type="milvus"))
    await session.flush()


async def test_concurrent_claim_has_single_owner_and_rejects_late_writer(background_job_schema) -> None:
    job_id = uuid.uuid4().hex
    repo = BackgroundJobRepository()
    await repo.create(job_id, _job_data())
    now = utc_now()

    claims = await asyncio.gather(
        repo.claim(job_id, worker_id="owner-a", lease_seconds=30, now=now),
        repo.claim(job_id, worker_id="owner-b", lease_seconds=30, now=now),
    )
    winners = [(record.worker_id, claimed) for record, claimed in claims if claimed]
    assert winners in [
        [("owner-a", True)],
        [("owner-b", True)],
    ]
    winner = winners[0][0]

    reconciled = await repo.reconcile_expired_leases(now=now + timedelta(seconds=31))
    assert reconciled == [(job_id, "failed", 1)]
    assert (
        await repo.update_owned(
            job_id,
            worker_id=winner,
            data={"message": "迟到结果"},
            now=now + timedelta(seconds=31),
        )
        is False
    )

    persisted = await repo.get_by_id(job_id)
    assert persisted.status == "failed"
    assert persisted.error.startswith("worker_lease_expired")
    assert persisted.worker_id is None


async def test_unknown_expired_job_does_not_rollback_other_reconciliation(background_job_schema) -> None:
    repo = BackgroundJobRepository()
    now = utc_now()
    unknown_id = uuid.uuid4().hex
    known_id = uuid.uuid4().hex
    await repo.create(unknown_id, {**_job_data(), "type": "removed_job_type"})
    await repo.create(known_id, {**_job_data(), "type": "knowledge_graph_index"})
    await repo.claim(unknown_id, worker_id="owner-a", lease_seconds=5, now=now)
    await repo.claim(known_id, worker_id="owner-b", lease_seconds=5, now=now)

    reconciled = await repo.reconcile_expired_leases(
        before_fail=finalize_job_failure,
        now=now + timedelta(seconds=6),
    )

    assert {item[:2] for item in reconciled} == {
        (unknown_id, "failed"),
        (known_id, "failed"),
    }
    assert (await repo.get_by_id(unknown_id)).status == "failed"
    assert (await repo.get_by_id(known_id)).status == "failed"


async def test_lock_wait_cannot_reuse_time_from_before_lease_expiry(background_job_schema) -> None:
    job_id = uuid.uuid4().hex
    repo = BackgroundJobRepository()
    await repo.create(job_id, _job_data())
    _, claimed = await repo.claim(job_id, worker_id="owner-a", lease_seconds=0.1)
    assert claimed is True

    async with background_job_schema.get_async_session_context() as blocker:
        await blocker.execute(text("SELECT id FROM background_jobs WHERE id = :job_id FOR UPDATE"), {"job_id": job_id})
        late_update = asyncio.create_task(repo.update_owned(job_id, worker_id="owner-a", data={"message": "迟到更新"}))
        await asyncio.sleep(0.2)

    assert await late_update is False
    assert (
        await repo.finish_owned(
            job_id,
            worker_id="owner-a",
            status="success",
            message="迟到完成",
        )
        is False
    )
    persisted = await repo.get_by_id(job_id)
    assert persisted.status == "running"


@pytest.mark.parametrize("owner_path", ["domain_callback", "terminal_hook"])
async def test_callback_lock_wait_rolls_back_after_lease_expiry(background_job_schema, owner_path) -> None:
    """两种 owning transaction callback 都必须在 lease 过期后回滚。"""
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(EvaluationDataset(dataset_id=dataset_id, kb_id=kb_id, name="pytest", item_count=0))

    repo = BackgroundJobRepository()
    await repo.create(job_id, _job_data())
    _record, claimed = await repo.claim(job_id, worker_id="owner-a", lease_seconds=0.1)
    assert claimed is True

    async def update_domain(session, _job_record) -> None:
        await EvaluationRepository.update_dataset_in_session(session, dataset_id, {"item_count": 99})

    async with background_job_schema.get_async_session_context() as blocker:
        await blocker.execute(
            text("SELECT dataset_id FROM evaluation_datasets WHERE dataset_id = :dataset_id FOR UPDATE"),
            {"dataset_id": dataset_id},
        )
        if owner_path == "domain_callback":
            terminal_operation = repo.run_owned_transaction(job_id, worker_id="owner-a", operation=update_domain)
        else:
            terminal_operation = repo.finish_owned(
                job_id,
                worker_id="owner-a",
                status="success",
                message="完成",
                before_finish=update_domain,
            )
        terminal = asyncio.create_task(terminal_operation)
        await asyncio.sleep(0.2)

    assert await terminal is False
    assert (await EvaluationRepository().get_dataset(dataset_id)).item_count == 0
    assert (await repo.get_by_id(job_id)).status == "running"


async def test_durable_capacity_reserves_worker_slots_for_agent_runs(background_job_schema) -> None:
    repo = BackgroundJobRepository()
    job_ids = [uuid.uuid4().hex for _ in range(5)]
    for job_id in job_ids:
        await repo.create(job_id, _job_data())

    for index, job_id in enumerate(job_ids[:4]):
        _record, claimed = await repo.claim(
            job_id,
            worker_id=f"owner-{index}",
            lease_seconds=30,
            max_running=4,
        )
        assert claimed is True

    _record, claimed = await repo.claim(
        job_ids[4],
        worker_id="owner-4",
        lease_seconds=30,
        max_running=4,
    )
    assert claimed is False
    assert (await repo.get_by_id(job_ids[4])).status == "pending"


async def test_knowledge_job_failure_fences_file_intermediate_state(background_job_schema) -> None:
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    file_id = f"file_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    owner = "owner-a"
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            KnowledgeFile(
                kb_id=kb_id,
                file_id=file_id,
                filename="pytest.txt",
                status="uploaded",
                is_folder=False,
            )
        )

    repo = BackgroundJobRepository()
    await repo.create(
        job_id,
        {
            **_job_data(),
            "type": "knowledge_parse",
            "payload": {"kb_id": kb_id, "scope": "files", "file_ids": [file_id]},
        },
    )
    _record, claimed = await repo.claim(job_id, worker_id=owner, lease_seconds=0.5)
    assert claimed is True
    claimed_file = await KnowledgeFileRepository().update_fields_if_status(
        kb_id=kb_id,
        file_id=file_id,
        allowed_statuses={"uploaded"},
        data={
            "status": "parsing",
            "processing_job_id": job_id,
            "processing_owner": owner,
        },
    )
    assert claimed_file is not None

    async with background_job_schema.get_async_session_context() as blocker:
        await blocker.execute(
            text("SELECT id FROM knowledge_files WHERE file_id = :file_id FOR UPDATE"),
            {"file_id": file_id},
        )
        late_write = asyncio.create_task(
            KnowledgeFileRepository().update_fields_if_status(
                kb_id=kb_id,
                file_id=file_id,
                allowed_statuses={"parsing"},
                data={"status": "parsed", "processing_job_id": None, "processing_owner": None},
                processing_job_id=job_id,
                processing_owner=owner,
            )
        )
        await asyncio.sleep(0.6)

    assert await late_write is None
    assert (await KnowledgeFileRepository().get_by_file_id(file_id)).status == "parsing"

    reconciled = await repo.reconcile_expired_leases(
        now=utc_now(),
        before_fail=finalize_job_failure,
    )
    assert reconciled == [(job_id, "failed", 1)]
    file_record = await KnowledgeFileRepository().get_by_file_id(file_id)
    assert file_record.status == "error_parsing"
    assert file_record.processing_job_id is None
    assert file_record.processing_owner is None


async def test_pending_dataset_cancel_commits_domain_and_job_terminal_together(background_job_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "pending", "job_id": job_id},
            )
        )

    await BackgroundJobRepository().create(
        job_id,
        {**_job_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )

    cancelled = await JobTracker().cancel_job(job_id)

    assert cancelled.status == "pending"
    assert cancelled.cancel_requested == 1
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.build_metadata["status"] == "pending"
    await publish_pending_jobs()
    assert (await BackgroundJobRepository().get_by_id(job_id)).status == "cancelled"
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.build_metadata["status"] == "failed"
    assert dataset.build_metadata["message"] == "任务已取消"


@pytest.mark.parametrize("replaced", [False, True])
@pytest.mark.parametrize("outcome", ["invalid", "cancelled", "expired"])
async def test_unknown_dataset_job_version_fails_only_its_own_build(background_job_schema, replaced, outcome) -> None:
    """无法重建的作业必须收敛所属数据集，不能覆盖新的生成任务。"""
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    current_job = "new-job" if replaced else job_id
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "pending", "job_id": current_job},
            )
        )
    await BackgroundJobRepository().create(
        job_id, {**_job_data(), "type": "dataset_generation", "handler_version": 999, "payload": {"dataset_id": dataset_id}}
    )

    repo = BackgroundJobRepository()
    if outcome == "cancelled":
        await repo.request_cancel(job_id)
    if outcome == "expired":
        _, claimed = await repo.claim(job_id, worker_id="old", lease_seconds=1, now=utc_now() - timedelta(minutes=1))
        assert claimed
        await repo.reconcile_expired_leases(before_fail=finalize_job_failure)
    else:
        await publish_pending_jobs()

    assert (await repo.get_by_id(job_id)).status == ("cancelled" if outcome == "cancelled" else "failed")
    metadata = (await EvaluationRepository().get_dataset(dataset_id)).build_metadata
    assert metadata["job_id"] == current_job
    assert metadata["status"] == ("pending" if replaced else "failed")


async def test_cancel_request_wins_race_with_successful_handler(background_job_schema) -> None:
    job_id = uuid.uuid4().hex
    repo = BackgroundJobRepository()
    await repo.create(job_id, _job_data())
    _, claimed = await repo.claim(job_id, worker_id="owner-a", lease_seconds=30)
    assert claimed is True
    assert await repo.request_cancel(job_id) is not None

    assert (
        await repo.finish_owned(
            job_id,
            worker_id="owner-a",
            status="success",
            message="任务已完成",
            result={"ignored": True},
        )
        is True
    )

    persisted = await repo.get_by_id(job_id)
    assert persisted.status == "cancelled"
    assert persisted.message == "任务已取消"
    assert persisted.result is None

    failed_job_id = uuid.uuid4().hex
    await repo.create(failed_job_id, _job_data())
    _, claimed = await repo.claim(failed_job_id, worker_id="owner-b", lease_seconds=30)
    assert claimed is True
    assert await repo.request_cancel(failed_job_id) is not None
    assert (
        await repo.finish_owned(
            failed_job_id,
            worker_id="owner-b",
            status="failed",
            message="执行失败",
            error="boom",
        )
        is True
    )
    assert (await repo.get_by_id(failed_job_id)).status == "cancelled"


@pytest.mark.parametrize("expired", [False, True], ids=["active_owner", "expired_owner"])
async def test_dataset_completion_requires_an_active_job_owner(background_job_schema, expired) -> None:
    """域数据只随有效 owner 的 Job 终态一起提交。"""
    now = utc_now()
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "running", "job_id": job_id},
            )
        )

    await BackgroundJobRepository().create(
        job_id,
        {**_job_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    await BackgroundJobRepository().claim(job_id, worker_id="owner-a", lease_seconds=5, now=now)
    result = {
        "dataset_id": dataset_id,
        "item_count": 3,
        "build_metadata": {"source": "generated", "status": "completed", "job_id": job_id},
    }

    async def finish_dataset(session, job_record):
        await finish_dataset_generation_job(session, job_record, result)

    finished = await BackgroundJobRepository().finish_owned(
        job_id,
        worker_id="owner-a",
        status="success",
        message="完成",
        result=result,
        before_finish=finish_dataset,
        now=now + timedelta(seconds=6 if expired else 1),
    )

    job = await BackgroundJobRepository().get_by_id(job_id)
    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert finished is not expired
    assert job.status == ("running" if expired else "success")
    assert dataset.item_count == (0 if expired else 3)
    assert dataset.build_metadata["status"] == ("running" if expired else "completed")


async def test_old_dataset_attempt_cannot_write_after_resume_claim(background_job_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    old_job_id = uuid.uuid4().hex
    new_job_id = uuid.uuid4().hex
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={"source": "generated", "status": "running", "job_id": old_job_id},
            )
        )

    await BackgroundJobRepository().create(
        old_job_id,
        {**_job_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    await BackgroundJobRepository().claim(old_job_id, worker_id="old-owner", lease_seconds=0.1)
    await asyncio.sleep(0.2)
    await BackgroundJobRepository().reconcile_expired_leases(before_fail=finalize_job_failure)

    await BackgroundJobRepository().create(
        new_job_id,
        {**_job_data(), "type": "dataset_generation", "payload": {"dataset_id": dataset_id}},
    )
    async with evaluation_repository_module.pg_manager.get_async_session_context() as session:
        await EvaluationRepository.attach_dataset_generation_job_in_session(session, dataset_id, new_job_id)
    await BackgroundJobRepository().claim(new_job_id, worker_id="new-owner", lease_seconds=30)
    old_context = BackgroundJobContext(old_job_id, "old-owner", {"dataset_id": dataset_id})

    async def stale_write(session, _job_record):
        await EvaluationRepository.update_dataset_in_session(session, dataset_id, {"item_count": 99})

    with pytest.raises(asyncio.CancelledError):
        await old_context.run_owned_transaction(stale_write)

    dataset = await EvaluationRepository().get_dataset(dataset_id)
    assert dataset.item_count == 0
    assert dataset.build_metadata["job_id"] == new_job_id
    assert dataset.build_metadata["status"] == "pending"


async def test_late_dataset_attachment_preserves_same_job_failure(background_job_schema) -> None:
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
        session.add(
            EvaluationDataset(
                dataset_id=dataset_id,
                kb_id=kb_id,
                name="pytest",
                item_count=0,
                build_metadata={
                    "source": "generated",
                    "status": "failed",
                    "job_id": job_id,
                    "error_message": "handler failed",
                },
            )
        )

    async with evaluation_repository_module.pg_manager.get_async_session_context() as session:
        await EvaluationRepository.attach_dataset_generation_job_in_session(session, dataset_id, job_id)

    persisted = await EvaluationRepository().get_dataset(dataset_id)
    assert persisted.build_metadata["status"] == "failed"
    assert persisted.build_metadata["error_message"] == "handler failed"


@pytest.mark.parametrize("failure_path", ["lease_expiry", "worker_shutdown"])
async def test_evaluation_run_failure_is_persisted_with_job(background_job_schema, failure_path) -> None:
    """lease 回收与 worker shutdown 都把 Run 和 Job 原子收敛为失败。"""
    now = utc_now()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    dataset_id = f"dataset_{uuid.uuid4().hex[:8]}"
    kb_id = f"kb_{uuid.uuid4().hex[:8]}"
    async with background_job_schema.get_async_session_context() as session:
        await _seed_knowledge_base(session, kb_id)
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

    job_id = uuid.uuid4().hex
    await BackgroundJobRepository().create(
        job_id,
        {
            **_job_data(),
            "type": "rag_evaluation",
            "payload": {"run_id": run_id},
        },
    )
    await BackgroundJobRepository().claim(
        job_id,
        worker_id="owner-a",
        lease_seconds=5 if failure_path == "lease_expiry" else 30,
        now=now,
    )
    if failure_path == "lease_expiry":
        expected_error = "worker_lease_expired: 执行 worker 的 lease 已过期，任务副作用结果未知"
        await BackgroundJobRepository().reconcile_expired_leases(
            before_fail=finalize_job_failure,
            now=now + timedelta(seconds=6),
        )
        runs = await EvaluationService().list_runs(kb_id)
        assert runs[0]["status"] == "failed"
    else:
        expected_error = "worker_shutdown: worker 停止时任务中断"

        async def finalize_failure(session, record):
            await finalize_job_failure(session, record, expected_error)

        assert (
            await BackgroundJobRepository().release_interrupted_owner(
                job_id,
                worker_id="owner-a",
                error=expected_error,
                before_fail=finalize_failure,
                now=now + timedelta(seconds=1),
            )
            == "failed"
        )

    job = await BackgroundJobRepository().get_by_id(job_id)
    run = await EvaluationRepository().get_run(run_id)
    assert job.status == "failed"
    assert run.status == "failed"
    assert run.metrics == {"error": expected_error}


async def test_payload_lookup_finds_active_job_beyond_recent_history_window(background_job_schema) -> None:
    target_id = uuid.uuid4().hex
    now = utc_now()
    async with background_job_schema.get_async_session_context() as session:
        session.add(
            BackgroundJobRecord(
                id=target_id,
                **_job_data(),
            )
        )
        session.add_all(
            BackgroundJobRecord(
                id=uuid.uuid4().hex,
                **{
                    **_job_data(),
                    "status": "failed",
                    "payload": {"dataset_id": f"history-{index}"},
                    "created_at": now + timedelta(seconds=index + 1),
                    "updated_at": now + timedelta(seconds=index + 1),
                },
            )
            for index in range(250)
        )

    found = await BackgroundJobRepository().find_latest_by_payload(
        job_type="knowledge_parse",
        payload_match={"kb_id": "pytest-kb"},
        statuses={"pending", "running"},
    )

    assert found is not None
    assert found.id == target_id

    listing = await JobTracker().list_jobs(limit=100)
    assert any(job["id"] == target_id for job in listing["jobs"])
    assert listing["summary"]["total"] == 251
    assert listing["summary"]["status_counts"]["pending"] == 1


async def test_concurrent_dedupe_creates_one_active_job(background_job_schema) -> None:
    job_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    dedupe_key = uuid.uuid4().hex
    repo = BackgroundJobRepository()

    results = await asyncio.gather(
        repo.create(job_ids[0], _job_data(dedupe_key=dedupe_key)),
        repo.create(job_ids[1], _job_data(dedupe_key=dedupe_key)),
    )

    created = [(record.id, is_created) for record, is_created in results if is_created]
    reused = [(record.id, is_created) for record, is_created in results if not is_created]
    assert len(created) == 1
    assert reused == [(created[0][0], False)]


async def test_registered_transaction_rollback_leaves_no_executable_job(background_job_schema):
    """登记与投递分离，回滚的登记对另一个数据库连接不可见。"""
    from yuxi.modules.background_jobs.dispatch import register_job_in_session

    observer = JobTracker()
    async with background_job_schema.get_async_session_context() as session:
        job, created = await register_job_in_session(
            session, name="pytest rollback", job_type="knowledge_parse", payload={"kb_id": "pytest-kb"}
        )
        assert created
        assert await BackgroundJobRepository().get_by_id(job.id) is None
        await session.rollback()
    assert await observer.get_job(job.id) is None
    assert await BackgroundJobRepository().list_pending() == []


@pytest.mark.parametrize("status", ["success", "failed"])
async def test_observer_preserves_executor_outcome_and_rejects_late_progress(background_job_schema, status):
    """相同失败计数不决定终态，进度达到 100 也不决定成功。"""
    observer = JobTracker()
    job, _ = await observer.register(name="pytest outcome", job_type="knowledge_parse")
    repo = BackgroundJobRepository()
    _, claimed = await repo.claim(job.id, worker_id="owner", lease_seconds=30)
    assert claimed
    assert await observer.report(job.id, worker_id="owner", progress=100, result={"failed": 2})
    assert (await observer.get_job(job.id))["status"] == "running"
    assert await repo.finish_owned(job.id, worker_id="owner", status=status, message="执行方结论")
    assert not await observer.report(job.id, worker_id="owner", progress=20, result={"failed": 0})
    observed = await observer.get_job(job.id)
    assert observed["status"] == status
    assert observed["result"] == {"failed": 2}


@pytest.mark.parametrize("action", ["parse", "index"])
@pytest.mark.parametrize("scope", ["files", "pending"])
async def test_document_partial_failure_persists_failed_job_and_details(background_job_schema, monkeypatch, action, scope) -> None:
    """真实 worker 入口将部分失败保存为失败终态，保留已提交的文件明细。"""
    import yuxi.modules.knowledge.services.background_jobs as knowledge_jobs
    from yuxi.workers.background_jobs import process_background_job

    async def process(_kb_id, file_id, **_kwargs):
        if file_id == "bad":
            raise RuntimeError("确定性的文件处理失败")
        return {"file_id": file_id, "status": "parsed" if action == "parse" else "indexed"}

    async def pending(_kb_id, *, after_file_id, **_kwargs):
        return [] if after_file_id else ["ok", "bad"]

    monkeypatch.setattr(knowledge_jobs.document_actions, f"{action}_file", process)
    monkeypatch.setattr(knowledge_jobs.knowledge_documents, "list_document_file_ids_by_statuses", pending)
    job_id = uuid.uuid4().hex
    repo = BackgroundJobRepository()
    await repo.create(
        job_id,
        {
            **_job_data(),
            "type": f"knowledge_{action}",
            "payload": {
                "kb_id": "pytest-kb",
                "operator_id": "pytest",
                "scope": scope,
                "file_ids": ["ok", "bad"],
                "statuses": ["uploaded"],
                "count": 2,
            },
        },
    )

    await process_background_job({"worker_id": "pytest"}, job_id)

    record = await repo.get_by_id(job_id)
    assert record.status == "failed"
    assert "失败 1 个" in record.error
    assert record.worker_id is None
    assert record.result["processed"] == 2
    assert record.result["failed"] == 1
    assert record.result["succeeded"] == 1
    assert [item["file_id"] for item in record.result["items"]] == ["ok", "bad"]
