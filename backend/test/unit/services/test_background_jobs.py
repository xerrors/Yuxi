"""后台作业 行为单元测试：持久提交、Handler 重建、lease 与终态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import yuxi.modules.background_jobs.dispatch as job_queue_service
import yuxi.modules.background_jobs.service as job_service
import yuxi.modules.knowledge.services.background_jobs as knowledge_job_service
import yuxi.workers.background_job_context as job_context
import yuxi.workers.background_jobs as job_worker
from yuxi.modules.background_jobs.registry import get_job_definition
from yuxi.modules.background_jobs.service import JobTracker
from yuxi.shared.datetime import utc_now
from yuxi.workers.background_job_context import BackgroundJobContext
from yuxi.workers.background_jobs import process_background_job


@pytest.fixture(autouse=True)
def disable_pending_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认隔离任务完成后的下一批发布，专项测试再覆盖它。"""

    async def no_pending_jobs(*, limit: int = 200) -> list[str]:
        return []

    monkeypatch.setattr(job_worker, "publish_pending_jobs", no_pending_jobs)
    monkeypatch.setattr(job_worker, "BackgroundJobRepository", lambda: job_service.BackgroundJobRepository())
    monkeypatch.setattr(job_context, "BackgroundJobRepository", lambda: job_service.BackgroundJobRepository())


class FakeRecord(SimpleNamespace):
    """模拟仓储实体，保持内部时间与状态原值。"""


def make_record(**overrides) -> FakeRecord:
    now = utc_now()
    data = {
        "id": "job-1",
        "name": "demo",
        "type": "demo",
        "status": "pending",
        "progress": 0.0,
        "message": "等待执行",
        "payload": {},
        "result": None,
        "error": None,
        "cancel_requested": 0,
        "handler_version": 1,
        "dedupe_key": None,
        "attempt_count": 0,
        "worker_id": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "timeout_seconds": 60.0,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    data.update(overrides)
    return FakeRecord(**data)


class FakeRepo:
    """实现 BackgroundJob Service 单元测试所需的持久边界。"""

    def __init__(self, record: FakeRecord | None = None):
        self.record = record
        self.events: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self.finish_calls: list[dict[str, Any]] = []
        self.claim_allowed = True
        self.live_owner = True
        self.renew_error: Exception | None = None
        self.release_calls: list[str] = []

    async def create(self, job_id: str, data: dict[str, Any]):
        self.events.append("persist")
        if self.record is not None and self.record.dedupe_key == data.get("dedupe_key"):
            return self.record, False
        self.record = make_record(id=job_id, **data)
        return self.record, True

    async def create_or_get_in_session(self, session, job_id, data):
        """登记接口复用同一持久测试替身。"""
        return await self.create(job_id, data)

    async def get_by_id(self, job_id: str):
        return self.record if self.record and self.record.id == job_id else None

    async def list(self, status=None, limit=100):
        if self.record is None or (status and self.record.status != status):
            return []
        return [self.record]

    async def claim(self, job_id: str, *, worker_id: str, lease_seconds: float, max_running: int | None = None):
        if not self.claim_allowed or self.record is None or self.record.status != "pending":
            return self.record, False
        now = utc_now()
        self.record.status = "running"
        self.record.worker_id = worker_id
        self.record.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.record.heartbeat_at = now
        self.record.attempt_count += 1
        return self.record, True

    async def check_control(self, job_id: str, *, worker_id: str):
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def renew_lease(self, job_id: str, *, worker_id: str, lease_seconds: float):
        if self.renew_error is not None:
            raise self.renew_error
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def update_owned(self, job_id: str, *, worker_id: str, data: dict[str, Any]):
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return False
        self.updates.append(data)
        for key, value in data.items():
            setattr(self.record, key, value)
        return True

    async def finish_owned(self, job_id: str, *, worker_id: str, **data):
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return False
        self.finish_calls.append(data)
        self.record.status = data["status"]
        self.record.message = data["message"]
        if "result" in data:
            self.record.result = data["result"]
        self.record.error = data.get("error")
        self.record.worker_id = None
        return True

    async def release_interrupted_owner(self, job_id: str, *, worker_id: str, error: str, before_fail=None):
        self.release_calls.append(error)
        self.record.status = "failed"
        self.record.error = error
        return self.record.status

    async def request_cancel(self, job_id: str):
        if self.record is None or self.record.status in {"success", "failed", "cancelled"}:
            return None
        self.record.cancel_requested = 1
        return self.record

    async def delete_terminal(self, job_id: str):
        if self.record and self.record.status in {"success", "failed", "cancelled"}:
            self.record = None
            return True
        return False


@dataclass
class FakeDefinition:
    handler: Any
    version: int = 1

    def load_handler(self):
        return self.handler

    def load_success_handler(self):
        return None

    def load_failure_handler(self):
        return None


async def test_arq_publication_uses_fresh_messages_instead_of_stale_job_lock(monkeypatch):
    calls = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))

    async def pool():
        return Pool()

    monkeypatch.setattr(job_queue_service, "get_arq_pool", pool)

    await job_queue_service.publish_job("job-1")

    assert calls == [(("process_background_job", "job-1"), {})]


async def test_registration_does_not_publish_before_the_caller_commits(monkeypatch):
    repo = FakeRepo()
    job_tracker = JobTracker()
    job_tracker._repo = repo

    async def publish(job_id: str):
        assert repo.record is not None
        repo.events.append("publish")
        return True

    monkeypatch.setattr(job_queue_service, "get_job_definition", lambda _job_type: FakeDefinition(None))
    monkeypatch.setattr(job_queue_service, "job_tracker", job_tracker)
    monkeypatch.setattr(job_queue_service, "publish_job", publish)

    job, _ = await job_queue_service.register_job_in_session(object(), name="demo", job_type="demo", payload={"value": 1})
    assert repo.events == ["persist"]
    await job_queue_service.dispatch_job(job.id)

    assert repo.events == ["persist", "publish"]
    assert job.payload == {"value": 1}
    assert job.status == "pending"


async def test_publication_failure_keeps_persisted_pending_intent(monkeypatch):
    repo = FakeRepo()
    job_tracker = JobTracker()
    job_tracker._repo = repo

    async def fail_publication(*_args):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(job_queue_service, "get_job_definition", lambda _job_type: FakeDefinition(None))
    monkeypatch.setattr(job_queue_service, "job_tracker", job_tracker)
    monkeypatch.setattr(job_queue_service, "publish_job", fail_publication)

    job, _ = await job_queue_service.register_job_in_session(object(), name="demo", job_type="demo", payload={"value": 1})
    assert repo.events == ["persist"]
    await job_queue_service.dispatch_job(job.id)

    assert job.status == "pending"
    assert repo.record is not None
    assert repo.record.payload == {"value": 1}


async def test_unique_registration_uses_database_dedupe_and_leaves_publication_to_caller(monkeypatch):
    repo = FakeRepo()
    job_tracker = JobTracker()
    job_tracker._repo = repo
    published: list[str] = []

    async def publish(job_id: str):
        published.append(job_id)
        return True

    monkeypatch.setattr(job_queue_service, "get_job_definition", lambda _job_type: FakeDefinition(None))
    monkeypatch.setattr(job_queue_service, "job_tracker", job_tracker)
    monkeypatch.setattr(job_queue_service, "publish_job", publish)

    first, first_created = await job_queue_service.register_job_in_session(
        object(),
        name="demo",
        job_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )
    second, second_created = await job_queue_service.register_job_in_session(
        object(),
        name="demo",
        job_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert published == []


async def test_job_context_throttles_progress_and_rejects_lost_lease(monkeypatch):
    record = make_record(status="running", worker_id="owner")
    record.lease_expires_at = utc_now() + timedelta(seconds=30)
    repo = FakeRepo(record)
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    context = BackgroundJobContext(record.id, "owner", {"value": 1})

    await context.set_progress(10)
    await context.set_progress(11, "第二步")
    await context.set_progress(12)

    assert context.payload == {"value": 1}
    assert [update["progress"] for update in repo.updates] == [10, 11]
    assert repo.updates[-1]["message"] == "第二步"

    repo.live_owner = False
    with pytest.raises(asyncio.CancelledError, match="lease was lost"):
        await context.set_message("迟到更新")


async def test_job_context_tracks_messages_written_outside_progress_updates(monkeypatch):
    record = make_record(status="running", worker_id="owner")
    repo = FakeRepo(record)
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    context = BackgroundJobContext(record.id, "owner")

    await context.set_progress(10, "A")
    await context.set_message("B")
    await context.set_progress(11, "A")

    assert repo.updates == [
        {"progress": 10.0, "message": "A"},
        {"message": "B"},
        {"progress": 11.0, "message": "A"},
    ]


async def test_process_background_job_rebuilds_handler_and_persists_success(monkeypatch):
    record = make_record(payload={"value": 7})
    repo = FakeRepo(record)
    seen: list[int] = []

    async def handler(context: BackgroundJobContext):
        seen.append(context.payload["value"])
        await context.set_progress(50, "执行中")
        return {"ok": True}

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert seen == [7]
    assert repo.record.status == "success"
    assert repo.finish_calls[-1]["result"] == {"ok": True}
    assert repo.record.attempt_count == 1


async def test_process_background_job_uses_failure_hook_when_success_hook_cannot_load(monkeypatch):
    record = make_record(type="dataset_generation")
    repo = FakeRepo(record)
    failure_calls: list[str] = []

    async def failure_hook(_session, _record, error: str):
        failure_calls.append(error)

    class BrokenSuccessDefinition(FakeDefinition):
        def load_success_handler(self):
            raise ImportError("success hook missing")

        def load_failure_handler(self):
            return failure_hook

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: BrokenSuccessDefinition(None))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "failed"
    assert repo.finish_calls[-1]["message"] == "任务 Handler 无法加载"
    before_finish = repo.finish_calls[-1]["before_finish"]
    assert before_finish is not None
    await before_finish(object(), record)
    assert failure_calls == ["success hook missing"]


async def test_process_background_job_republishes_pending_jobs_after_slot_release(monkeypatch):
    record = make_record()
    repo = FakeRepo(record)
    publication_limits: list[int] = []

    async def handler(_context: BackgroundJobContext):
        return {"ok": True}

    async def publish_pending(*, limit: int = 200) -> list[str]:
        publication_limits.append(limit)
        return []

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))
    monkeypatch.setattr(job_worker, "publish_pending_jobs", publish_pending)

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert publication_limits == [job_worker.BACKGROUND_JOB_MAX_RUNNING]


async def test_duplicate_delivery_cannot_execute_without_claim(monkeypatch):
    record = make_record(status="running", worker_id="other-owner")
    repo = FakeRepo(record)
    repo.claim_allowed = False
    called = False

    async def handler(context: BackgroundJobContext):
        nonlocal called
        called = True

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert called is False
    assert repo.finish_calls == []


async def test_heartbeat_error_cancels_handler_as_lost_lease(monkeypatch):
    record = make_record(timeout_seconds=1)
    repo = FakeRepo(record)
    repo.renew_error = ConnectionError("database unavailable")
    observed_reason: list[str | None] = []

    async def handler(context: BackgroundJobContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(job_worker, "JOB_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert observed_reason == ["lease_lost"]
    assert repo.finish_calls == []


async def test_parent_job_cancellation_waits_for_handler_exit(monkeypatch):
    record = make_record()
    repo = FakeRepo(record)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(context: BackgroundJobContext):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    job = asyncio.create_task(process_background_job({"worker_id": "worker-1"}, record.id))
    await started.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert stopped.is_set()
    assert repo.release_calls == ["worker_shutdown: worker 停止时任务中断"]


async def test_process_background_job_timeout_persists_failed_terminal(monkeypatch):
    record = make_record(timeout_seconds=0.01)
    repo = FakeRepo(record)

    observed_reason: list[str | None] = []

    async def handler(context: BackgroundJobContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "failed"
    assert observed_reason == ["timeout"]
    assert repo.finish_calls[-1]["message"] == "任务执行超时"
    assert "0.01-second" in repo.finish_calls[-1]["error"]


async def test_pending_cancel_is_only_an_intent_until_execution_acknowledges(monkeypatch):
    repo = FakeRepo(make_record())
    observer = JobTracker()
    observer._repo = repo

    job = await observer.cancel_job("job-1")

    assert job.status == "pending"
    assert job.cancel_requested == 1
    assert await observer.delete_job("job-1") is False


async def test_registration_and_cancel_do_not_require_a_worker_handler():
    observer = JobTracker()
    observer._repo = FakeRepo()
    job, created = await observer.register(name="业务动作", job_type="domain-owned", handler_version=99)

    assert created is True
    assert job.type == "domain-owned"
    assert job.handler_version == 99
    assert observer._repo.events == ["persist"]
    cancelled = await observer.cancel_job(job.id)
    assert cancelled.status == "pending"
    assert cancelled.cancel_requested == 1


async def test_report_progress_100_does_not_infer_success_or_failure():
    record = make_record(status="running", worker_id="owner")
    observer = JobTracker()
    observer._repo = FakeRepo(record)

    assert await observer.report(record.id, worker_id="owner", progress=100, result={"failed": 2}) is True
    assert record.progress == 100
    assert record.result == {"failed": 2}
    assert record.status == "running"


@pytest.mark.parametrize("progress", [float("nan"), float("inf"), -float("inf")])
async def test_report_rejects_non_finite_progress(progress):
    observer = JobTracker()
    observer._repo = FakeRepo(make_record(status="running", worker_id="owner"))
    with pytest.raises(ValueError, match="finite"):
        await observer.report("job-1", worker_id="owner", progress=progress)
    assert observer._repo.updates == []


def test_job_timeout_accepts_existing_values_above_24_hours():
    assert job_queue_service.resolve_job_timeout(None, default=172800.0) == 172800.0


def test_job_timeout_override_cannot_exceed_worker_default():
    assert job_queue_service.resolve_job_timeout(30.0, default=60.0) == 30.0
    with pytest.raises(ValueError, match="cannot exceed the worker default"):
        job_queue_service.resolve_job_timeout(61.0, default=60.0)


async def test_running_cancel_waits_for_handler_safe_boundary(monkeypatch):
    record = make_record()
    repo = FakeRepo(record)
    entered = asyncio.Event()
    boundary = asyncio.Event()
    interrupted = asyncio.Event()

    async def handler(context):
        entered.set()
        try:
            await boundary.wait()
            await context.raise_if_cancelled()
        except asyncio.CancelledError:
            interrupted.set()
            raise

    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repo)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))
    monkeypatch.setattr(job_worker, "JOB_HEARTBEAT_SECONDS", 0.001)
    job = asyncio.create_task(process_background_job({}, record.id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        record.cancel_requested = 1
        await asyncio.sleep(0.02)
        assert not interrupted.is_set(), "取消请求不能打断尚未到达安全边界的业务动作"
        boundary.set()
        await asyncio.wait_for(job, 1)
        assert interrupted.is_set()
        assert record.status == "cancelled"
    finally:
        boundary.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


def test_unknown_handler_version_is_rejected() -> None:
    assert get_job_definition("knowledge_parse", 1).version == 1

    with pytest.raises(ValueError, match="Unsupported handler version"):
        get_job_definition("knowledge_parse", 2)

    with pytest.raises(ValueError, match="Unknown job type"):
        get_job_definition("no_such_job")


def test_document_job_result_keeps_bounded_references_and_counts():
    """任务摘要不携带完整文件数据或异常秘密，计数仍覆盖全部结果。"""
    items = [{"file_id": f"file-{index}", "status": "failed", "error": "private-token", "markdown": "private-body"} for index in range(250)]
    result = knowledge_job_service._document_result(items, processed=250, failed=250)

    assert result["failed"] == 250 and result["succeeded"] == 0
    assert result["result_truncated"] is True
    assert len(result["items"]) == 200
    assert set(result["items"][0]) == {"file_id", "status", "error"}
    assert "private-token" not in str(result) and "private-body" not in str(result)


@pytest.mark.parametrize("scope", ["files", "pending", "ingest"])
async def test_missing_embedding_reason_survives_job_result_projection(monkeypatch, scope):
    """缺少模型的固定业务提示穿过任务执行与观察投影，其他异常仍不公开。"""
    from yuxi.modules.background_jobs.models import BackgroundJobRecord
    from yuxi.modules.knowledge.base import KBEmbeddingModelMissingError

    async def index(*_args, **_kwargs):
        raise KBEmbeddingModelMissingError()

    async def parse(_kb_id, file_id, **_kwargs):
        return {"file_id": file_id, "status": "parsed"}

    async def pending(_kb_id, *, after_file_id, **_kwargs):
        return [] if after_file_id else ["file-1"]

    monkeypatch.setattr(knowledge_job_service.knowledge_base, "index_file", index)
    monkeypatch.setattr(knowledge_job_service.knowledge_base, "parse_file", parse)
    monkeypatch.setattr(knowledge_job_service.knowledge_base, "list_document_file_ids_by_statuses", pending)
    record = make_record(
        type="knowledge_index",
        payload={
            "kb_id": "kb-1",
            "operator_id": "user-1",
            "file_ids": ["file-1"],
            "scope": scope,
            "statuses": ["parsed"],
            "count": 1,
            "params": {"auto_index": True},
        },
    )
    repository = FakeRepo(record)
    handler = knowledge_job_service.run_knowledge_ingest if scope == "ingest" else knowledge_job_service.run_knowledge_index
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repository)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    observation = BackgroundJobRecord(**vars(record)).to_observation_dict()
    assert observation["status"] == "failed"
    assert "未配置 Embedding 模型" in observation["error"]
    assert observation["message"] == observation["error"]
    assert "未配置 Embedding 模型" in observation["result"]["items"][0]["error"]
    assert observation["result"]["failed"] == 1


def test_raw_job_errors_stay_private():
    """没有刻意公开消息时，顶层和单文件错误都不能泄露原始异常。"""
    from yuxi.modules.background_jobs.models import BackgroundJobRecord

    record = BackgroundJobRecord(
        id="private-job",
        type="knowledge_index",
        name="test",
        status="failed",
        error="secret-token",
        message="secret-token",
        result={"items": [{"file_id": "f1", "status": "failed", "error": "secret-token"}]},
    )
    observation = record.to_observation_dict()
    assert "secret-token" not in str(observation)
    assert "任务执行失败" in observation["error"]


async def test_milvus_preserves_missing_embedding_error():
    """已有集合创建校验保留业务异常，避免被通用连接错误覆盖。"""
    from yuxi.modules.knowledge.base import KBEmbeddingModelMissingError
    from yuxi.modules.knowledge.implementations.milvus import MilvusKB

    executor = object.__new__(MilvusKB)
    executor.collections = {}
    with pytest.raises(KBEmbeddingModelMissingError, match="未配置 Embedding 模型"):
        await executor._get_or_create_milvus_collection("kb-missing", None)


@pytest.mark.parametrize("action", ["parse", "index"])
@pytest.mark.parametrize("scope", ["files", "pending"])
@pytest.mark.parametrize("failure", ["exception", "result", "params", None])
async def test_document_job_failure_keeps_details_and_rejects_success(monkeypatch, action, scope, failure):
    """解析和入库两种入口都保留明细，任一文件失败时拒绝成功收尾。"""
    payload = {
        "kb_id": "kb_1",
        "operator_id": "user_1",
        "file_ids": ["ok", "bad"],
        "statuses": ["uploaded"],
        "count": 2,
        "scope": scope,
    }
    if failure == "params":
        payload["params"] = {"chunk_size": 10}
    calls = []

    async def process(_kb_id, file_id, *, params, **_kwargs):
        """在动作拥有的参数领取或执行阶段注入失败。"""
        from yuxi.modules.knowledge.chunking.presets import ChunkConfigError

        assert params == payload.get("params", {})
        calls.append(file_id)
        if file_id == "bad" and failure == "params":
            raise ChunkConfigError("参数领取失败")
        if file_id == "bad" and failure == "exception":
            raise RuntimeError("处理失败")
        return {"file_id": file_id, "status": "failed" if file_id == "bad" and failure == "result" else "parsed"}

    async def pending(_kb_id, *, after_file_id, **_kwargs):
        return [] if after_file_id else ["ok", "bad"]

    monkeypatch.setattr(knowledge_job_service.knowledge_base, f"{action}_file", process)
    monkeypatch.setattr(knowledge_job_service.knowledge_base, "list_document_file_ids_by_statuses", pending)
    handler = knowledge_job_service.run_knowledge_parse if action == "parse" else knowledge_job_service.run_knowledge_index
    record = make_record(type=f"knowledge_{action}", payload=payload)
    repository = FakeRepo(record)
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repository)
    monkeypatch.setattr(job_worker, "get_job_definition", lambda *_args: FakeDefinition(handler))

    await process_background_job({"worker_id": "worker-1"}, record.id)

    assert record.status == ("failed" if failure else "success")
    assert calls == ["ok", "bad"]
    assert record.result["processed"] == 2
    assert record.result["failed"] == int(bool(failure))
    assert record.result["succeeded"] == 2 - int(bool(failure))
    assert [item["file_id"] for item in record.result["items"]] == ["ok", "bad"]
    assert record.result["items"][1]["status"] == ("failed" if failure else "parsed")
    if failure:
        assert "失败 1 个" in record.error


async def test_registration_returns_repository_record_without_state_copy(monkeypatch):
    """内部返回同一持久实体，时间和状态不经序列化重建。"""
    repository = FakeRepo()
    monkeypatch.setattr(job_service, "BackgroundJobRepository", lambda: repository)
    job, created = await JobTracker().register(name="direct-record", job_type="domain-owned")
    assert created and job is repository.record
    assert job.created_at == repository.record.created_at


def test_job_summary_does_not_construct_full_result():
    """列表直接生成摘要字段，不为随后丢弃的详情执行投影。"""
    from yuxi.modules.background_jobs.models import BackgroundJobRecord

    record = BackgroundJobRecord(
        id="test", name="test", type="test", status="pending", cancel_requested=0, handler_version=1, attempt_count=0, timeout_seconds=60
    )
    record._result_summary = lambda: pytest.fail("列表不能构建结果详情")
    summary = record.to_summary_dict()
    assert "result" not in summary and "payload" not in summary and "worker_id" not in summary
