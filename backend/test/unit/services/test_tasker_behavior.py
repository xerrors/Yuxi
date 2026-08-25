"""Durable Task 行为单元测试：持久提交、Handler 重建、lease 与终态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from yuxi.services import task_queue_service, task_service
from yuxi.services.task_service import TaskContext, Tasker, process_task
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


class FakeRecord(SimpleNamespace):
    def to_dict(self) -> dict[str, Any]:
        data = vars(self).copy()
        for key in ("created_at", "updated_at", "started_at", "completed_at", "heartbeat_at", "lease_expires_at"):
            data[key] = format_utc_datetime(data.get(key))
        return data


def make_record(**overrides) -> FakeRecord:
    now = utc_now_naive()
    data = {
        "id": "task-1",
        "name": "demo",
        "type": "demo",
        "status": "pending",
        "progress": 0.0,
        "message": "等待执行",
        "payload": {},
        "result": None,
        "error": None,
        "cancel_requested": 0,
        "recovery_strategy": "fail",
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
    """实现 Task Service 单元测试所需的持久边界。"""

    def __init__(self, record: FakeRecord | None = None):
        self.record = record
        self.events: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self.finish_calls: list[dict[str, Any]] = []
        self.claim_allowed = True
        self.live_owner = True
        self.renew_error: Exception | None = None
        self.release_calls: list[str] = []

    async def create(self, task_id: str, data: dict[str, Any]):
        self.events.append("persist")
        if self.record is not None and self.record.dedupe_key == data.get("dedupe_key"):
            return self.record, False
        self.record = make_record(id=task_id, **data)
        return self.record, True

    async def get_by_id(self, task_id: str):
        return self.record if self.record and self.record.id == task_id else None

    async def list(self, status=None, limit=100):
        if self.record is None or (status and self.record.status != status):
            return []
        return [self.record]

    async def claim(self, task_id: str, *, worker_id: str, lease_seconds: float, max_running: int | None = None):
        if not self.claim_allowed or self.record is None or self.record.status != "pending":
            return self.record, False
        now = utc_now_naive()
        self.record.status = "running"
        self.record.worker_id = worker_id
        self.record.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.record.heartbeat_at = now
        self.record.attempt_count += 1
        return self.record, True

    async def check_control(self, task_id: str, *, worker_id: str):
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def renew_lease(self, task_id: str, *, worker_id: str, lease_seconds: float):
        if self.renew_error is not None:
            raise self.renew_error
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def update_owned(self, task_id: str, *, worker_id: str, data: dict[str, Any]):
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return False
        self.updates.append(data)
        for key, value in data.items():
            setattr(self.record, key, value)
        return True

    async def finish_owned(self, task_id: str, *, worker_id: str, **data):
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

    async def release_interrupted_owner(self, task_id: str, *, worker_id: str, error: str, before_fail=None):
        self.release_calls.append(error)
        self.record.status = "pending" if self.record.recovery_strategy == "restart" else "failed"
        self.record.error = error
        return self.record.status

    async def request_cancel(self, task_id: str, *, before_cancel=None):
        if self.record is None or self.record.status in {"success", "failed", "cancelled"}:
            return None
        self.record.cancel_requested = 1
        if self.record.status == "pending":
            self.record.status = "cancelled"
        return self.record

    async def delete_terminal(self, task_id: str):
        if self.record and self.record.status in {"success", "failed", "cancelled"}:
            self.record = None
            return True
        return False


@dataclass
class FakeDefinition:
    handler: Any
    recovery_strategy: str = "fail"
    version: int = 1
    requires_knowledge: bool = False

    def load_handler(self):
        return self.handler

    def load_success_handler(self):
        return None

    def load_failure_handler(self):
        return None


def test_shipping_task_callers_do_not_pass_process_local_coroutines():
    """知识库与评估入口不能重新把进程内 closure 交给 Task Service。"""
    backend_root = Path(__file__).resolve().parents[3]
    owners = [
        backend_root / "server/routers/knowledge_router.py",
        backend_root / "package/yuxi/knowledge/eval/service.py",
    ]

    for owner in owners:
        assert "coroutine=" not in owner.read_text(), owner


async def test_arq_publication_uses_fresh_messages_instead_of_stale_job_lock(monkeypatch):
    calls = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))

    async def pool():
        return Pool()

    monkeypatch.setattr(task_queue_service, "get_arq_pool", pool)

    await task_queue_service.publish_task("task-1")

    assert calls == [(("process_task", "task-1"), {})]


async def test_submit_persists_before_arq_publication(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo

    async def publish(task_id: str):
        assert repo.record is not None
        repo.events.append("publish")
        return True

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", publish)

    task = await tasker.enqueue(name="demo", task_type="demo", payload={"value": 1})

    assert repo.events == ["persist", "publish"]
    assert task.payload == {"value": 1}
    assert task.status == "pending"


async def test_publication_failure_keeps_persisted_pending_intent(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo

    async def fail_publication(*_args):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", fail_publication)

    task = await tasker.enqueue(name="demo", task_type="demo", payload={"value": 1})

    assert task.status == "pending"
    assert repo.record is not None
    assert repo.record.payload == {"value": 1}


async def test_unique_submit_uses_database_dedupe_and_does_not_republish(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo
    published: list[str] = []

    async def publish(task_id: str):
        published.append(task_id)
        return True

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", publish)

    first, first_created = await tasker.enqueue_unique_by_payload(
        name="demo",
        task_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )
    second, second_created = await tasker.enqueue_unique_by_payload(
        name="demo",
        task_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert published == [first.id]


async def test_task_context_throttles_progress_and_rejects_lost_lease(monkeypatch):
    record = make_record(status="running", worker_id="owner")
    record.lease_expires_at = utc_now_naive() + timedelta(seconds=30)
    repo = FakeRepo(record)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    context = TaskContext(record.id, "owner", {"value": 1})

    await context.set_progress(10)
    await context.set_progress(11)
    await context.set_progress(12)

    assert context.payload == {"value": 1}
    assert [update["progress"] for update in repo.updates] == [10, 12]

    repo.live_owner = False
    with pytest.raises(asyncio.CancelledError, match="lease was lost"):
        await context.set_message("迟到更新")


async def test_process_task_rebuilds_handler_and_persists_success(monkeypatch):
    record = make_record(payload={"value": 7})
    repo = FakeRepo(record)
    seen: list[int] = []

    async def handler(context: TaskContext):
        seen.append(context.payload["value"])
        await context.set_progress(50, "执行中")
        return {"ok": True}

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert seen == [7]
    assert repo.record.status == "success"
    assert repo.finish_calls[-1]["result"] == {"ok": True}
    assert repo.record.attempt_count == 1


async def test_lite_worker_does_not_claim_knowledge_task(monkeypatch):
    record = make_record(type="knowledge_parse")
    repo = FakeRepo(record)

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "lite_mode_enabled", lambda: True)
    monkeypatch.setattr(
        task_service,
        "get_task_definition",
        lambda *_args: FakeDefinition(None, requires_knowledge=True),
    )

    await process_task({"worker_id": "worker-lite"}, record.id)

    assert record.status == "pending"
    assert record.attempt_count == 0


async def test_lite_api_does_not_cancel_knowledge_task(monkeypatch):
    record = make_record(type="knowledge_parse")
    repo = FakeRepo(record)
    tasker = Tasker()
    tasker._repo = repo

    monkeypatch.setattr(task_service, "lite_mode_enabled", lambda: True)
    monkeypatch.setattr(
        task_service,
        "get_task_definition",
        lambda *_args: FakeDefinition(None, requires_knowledge=True),
    )

    assert await tasker.cancel_task(record.id) is None
    assert record.status == "pending"
    assert record.cancel_requested == 0

    record.status = "failed"
    assert await tasker.delete_task(record.id) is False
    assert repo.record is record


async def test_duplicate_delivery_cannot_execute_without_claim(monkeypatch):
    record = make_record(status="running", worker_id="other-owner")
    repo = FakeRepo(record)
    repo.claim_allowed = False
    called = False

    async def handler(context: TaskContext):
        nonlocal called
        called = True

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert called is False
    assert repo.finish_calls == []


async def test_heartbeat_error_cancels_handler_as_lost_lease(monkeypatch):
    record = make_record(timeout_seconds=1)
    repo = FakeRepo(record)
    repo.renew_error = ConnectionError("database unavailable")
    observed_reason: list[str | None] = []

    async def handler(context: TaskContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(task_service, "TASK_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert observed_reason == ["lease_lost"]
    assert repo.finish_calls == []


async def test_parent_job_cancellation_waits_for_handler_exit(monkeypatch):
    record = make_record(recovery_strategy="fail")
    repo = FakeRepo(record)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(context: TaskContext):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    job = asyncio.create_task(process_task({"worker_id": "worker-1"}, record.id))
    await started.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert stopped.is_set()
    assert repo.release_calls == ["worker_shutdown: worker 停止时任务中断"]


async def test_process_task_timeout_persists_failed_terminal(monkeypatch):
    record = make_record(timeout_seconds=0.01)
    repo = FakeRepo(record)

    observed_reason: list[str | None] = []

    async def handler(context: TaskContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "failed"
    assert observed_reason == ["timeout"]
    assert repo.finish_calls[-1]["message"] == "任务执行超时"
    assert "0.01-second" in repo.finish_calls[-1]["error"]


async def test_pending_cancel_becomes_terminal_without_worker(monkeypatch):
    repo = FakeRepo(make_record())
    tasker = Tasker()
    tasker._repo = repo

    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(None))
    task = await tasker.cancel_task("task-1")

    assert task is not None
    assert task.status == "cancelled"
    assert task.cancel_requested is True
    assert await tasker.delete_task("task-1") is True
