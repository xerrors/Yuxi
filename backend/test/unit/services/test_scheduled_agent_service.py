from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import yuxi.modules.schedules.service as service
from yuxi.modules.schedules.service import next_run_at, validate_schedule
from yuxi.shared.errors import DomainError, InvalidInputError, ResourceNotFoundError, StateConflictError


def test_next_run_at_uses_timezone_and_returns_utc_aware_datetime():
    result = next_run_at("0 9 * * *", "Asia/Shanghai", datetime(2026, 8, 26, 0, 0, tzinfo=UTC))

    assert result == datetime(2026, 8, 26, 1, 0, tzinfo=UTC)
    assert result.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    ("expression", "timezone"),
    [("every day", "Asia/Shanghai"), ("0 9 * * * *", "Asia/Shanghai"), ("0 9 * * *", "Mars/Olympus")],
)
def test_validate_schedule_rejects_invalid_expression_or_timezone(expression, timezone):
    with pytest.raises(DomainError) as exc_info:
        validate_schedule(expression, timezone)

    assert isinstance(exc_info.value, InvalidInputError)


def test_next_run_at_preserves_input_offset_and_crosses_daylight_saving():
    """输入偏移和夏令时不会改变触发瞬间。"""
    from datetime import timedelta, timezone

    after = datetime(2026, 8, 26, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    assert next_run_at("0 9 * * *", "Asia/Shanghai", after) == datetime(2026, 8, 26, 1, 0, tzinfo=UTC)
    before_dst = datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    assert next_run_at("0 9 * * *", "America/New_York", before_dst) == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)


def test_validate_schedule_accepts_five_field_cron_and_iana_timezone():
    assert validate_schedule("*/15 * * * *", "UTC") == ("*/15 * * * *", "UTC")


def test_validate_schedule_rejects_expression_without_future_occurrence():
    with pytest.raises(DomainError) as exc_info:
        validate_schedule("0 0 31 2 *", "UTC")

    assert isinstance(exc_info.value, InvalidInputError)


@pytest.mark.asyncio
async def test_validate_project_rejects_project_owned_by_another_user(monkeypatch):
    class ProjectRepository:
        def __init__(self, db):
            del db

        async def lock_active_for_user(self, project_id, uid):
            assert (project_id, uid) == ("project-1", "user-2")
            return None

    monkeypatch.setattr(service, "ProjectRepository", ProjectRepository)

    with pytest.raises(DomainError) as exc_info:
        await service._validate_project("project-1", SimpleNamespace(uid="user-2"), object())

    assert isinstance(exc_info.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_validate_agent_rejects_agent_outside_user_visibility(monkeypatch):
    class AgentRepository:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, *, slug, user):
            assert (slug, user.uid) == ("private-agent", "user-2")
            return None

    monkeypatch.setattr(service, "AgentRepository", AgentRepository)

    with pytest.raises(DomainError) as exc_info:
        await service._validate_agent("private-agent", SimpleNamespace(uid="user-2"), object())

    assert isinstance(exc_info.value, ResourceNotFoundError)


def test_scheduled_run_model_owns_execution_configuration_snapshot():
    from yuxi.modules.schedules.models import ScheduledAgentRun

    assert {"project_id", "agent_slug", "session_title", "prompt", "tool_approval_mode", "model_spec"}.issubset(
        ScheduledAgentRun.__table__.c.keys()
    )


@pytest.mark.asyncio
async def test_validate_agent_keeps_ordinary_agents_available(monkeypatch):
    """个人助理限制不影响已有普通 Agent 定时任务。"""
    agent = SimpleNamespace(backend_id="ChatbotAgent")
    repo = SimpleNamespace(get_visible_by_slug=AsyncMock(return_value=agent))
    monkeypatch.setattr(service, "AgentRepository", lambda _db: repo)
    monkeypatch.setattr(service, "get_agent_backend", lambda _backend: object())

    assert await service._validate_agent("ordinary-agent", SimpleNamespace(uid="user-1"), object()) is agent


def test_scheduled_run_model_uses_unique_thread_per_execution_and_preserves_history():
    from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun

    project_fk = next(
        constraint
        for constraint in ScheduledAgentJob.__table__.foreign_key_constraints
        if constraint.name == "fk_scheduled_agent_jobs_project_uid"
    )
    assert project_fk.ondelete == "CASCADE"
    foreign_key = next(iter(ScheduledAgentRun.__table__.c.job_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in ScheduledAgentRun.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("thread_id",) in unique_columns


def test_execution_projection_reads_terminal_status_from_agent_run():
    scheduled_run = SimpleNamespace(
        status="submitted",
        to_dict=lambda: {
            "status": "submitted",
            "run_id": None,
            "error_message": None,
            "completed_at": None,
        },
    )
    input_item = SimpleNamespace(status="consumed", turn_id="turn-1")
    run = SimpleNamespace(
        id="run-1",
        status="failed",
        error_message="模型不可用",
        finished_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
    )

    result = service._execution_to_dict(scheduled_run, input_item, run)

    assert result == {
        "status": "failed",
        "run_id": "run-1",
        "turn_id": "turn-1",
        "error_message": "模型不可用",
        "completed_at": "2026-08-27T10:00:00Z",
        "session_available": True,
    }


def test_execution_projection_does_not_offer_session_before_input_exists():
    scheduled_run = SimpleNamespace(
        status="failed",
        to_dict=lambda: {"status": "failed", "thread_id": "reserved-thread"},
    )

    result = service._execution_to_dict(scheduled_run, None, None)

    assert result["session_available"] is False


@pytest.mark.asyncio
async def test_enabling_paused_job_schedules_from_current_time(monkeypatch):
    now = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    next_time = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
    job = SimpleNamespace(
        cron_expression="0 9 * * *",
        timezone="Asia/Shanghai",
        enabled=False,
        next_run_at=datetime(2026, 8, 20, 1, 0, tzinfo=UTC),
        updated_at=None,
        to_dict=lambda: {"enabled": job.enabled, "next_run_at": job.next_run_at},
    )

    class Repository:
        async def get_job(self, job_id, uid, *, lock):
            assert (job_id, uid, lock) == ("job-1", "user-1", True)
            return job

    class Db:
        async def commit(self):
            return None

    monkeypatch.setattr(service, "ScheduledAgentRepository", lambda _db: Repository())
    monkeypatch.setattr(service, "utc_now", lambda: now)
    monkeypatch.setattr(service, "next_run_at", lambda expression, timezone, after: next_time)

    result = await service.update_scheduled_job(
        job_id="job-1",
        user=SimpleNamespace(uid="user-1"),
        db=Db(),
        data={"enabled": True},
    )

    assert result == {"enabled": True, "next_run_at": next_time}


@pytest.mark.asyncio
async def test_run_now_rejects_request_id_reused_for_another_job(monkeypatch):
    job = SimpleNamespace(id="job-2")
    existing_run = SimpleNamespace(job_id="job-1")

    class Repository:
        async def get_job(self, job_id, uid, *, lock):
            assert (job_id, uid, lock) == ("job-2", "user-1", True)
            return job

        async def get_run(self, run_id):
            assert run_id == service.build_stable_id("scheduled-run", "user-1:manual:manual-request-1")
            return existing_run

    monkeypatch.setattr(service, "ScheduledAgentRepository", lambda _db: Repository())

    with pytest.raises(DomainError) as exc_info:
        await service.run_scheduled_job_now(
            job_id="job-2",
            request_id="manual-request-1",
            user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert isinstance(exc_info.value, StateConflictError)


@pytest.mark.asyncio
async def test_recovery_continues_after_one_dispatch_fails(monkeypatch):
    records = [SimpleNamespace(id="run-failing"), SimpleNamespace(id="run-success")]
    dispatched = []

    class Repository:
        def __init__(self, db):
            del db

        async def list_dispatching_runs(self, *, before, limit):
            del before
            assert limit == 10
            return records

    @asynccontextmanager
    async def session_context():
        yield object()

    async def dispatch(*, scheduled_run_id):
        dispatched.append(scheduled_run_id)
        if scheduled_run_id == "run-failing":
            raise RuntimeError("持续失败")
        return {"status": "submitted"}

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(service, "ScheduledAgentRepository", Repository)
    monkeypatch.setattr(service, "dispatch_scheduled_run", dispatch)

    assert await service.recover_scheduled_dispatches(limit=10) == 1
    assert dispatched == ["run-failing", "run-success"]


@pytest.mark.asyncio
async def test_due_job_claiming_continues_after_one_dispatch_fails(monkeypatch):
    runs = iter(
        [
            SimpleNamespace(id="run-failing", status="dispatching"),
            SimpleNamespace(id="run-success", status="dispatching"),
            None,
        ]
    )
    dispatched = []

    @asynccontextmanager
    async def session_context():
        yield object()

    async def claim(*, db, now):
        del db, now
        return next(runs)

    async def dispatch(*, scheduled_run_id):
        dispatched.append(scheduled_run_id)
        if scheduled_run_id == "run-failing":
            raise RuntimeError("持续失败")
        return {"status": "submitted"}

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(service, "_claim_due_run", claim)
    monkeypatch.setattr(service, "dispatch_scheduled_run", dispatch)

    assert await service.claim_and_dispatch_due_jobs(limit=3) == 2
    assert dispatched == ["run-failing", "run-success"]


@pytest.mark.asyncio
async def test_claim_disables_invalid_schedule_and_continues_to_next_job(monkeypatch):
    invalid = SimpleNamespace(
        id="job-invalid",
        cron_expression="0 9 * * *",
        timezone="Mars/Olympus",
        next_run_at=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
        enabled=True,
        updated_at=None,
    )
    valid = SimpleNamespace(
        id="job-valid",
        cron_expression="0 9 * * *",
        timezone="UTC",
        next_run_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
        enabled=True,
        updated_at=None,
    )
    jobs = iter([invalid, valid])
    commits = 0

    class Repository:
        async def claim_due_job(self, *, now):
            assert now == datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
            return next(jobs)

    class Db:
        async def commit(self):
            nonlocal commits
            commits += 1

    run = SimpleNamespace(id="run-valid")

    async def create_run_record(**kwargs):
        assert kwargs["job"] is valid
        return run

    monkeypatch.setattr(service, "ScheduledAgentRepository", lambda _db: Repository())
    monkeypatch.setattr(service, "_create_run_record", create_run_record)

    result = await service._claim_due_run(db=Db(), now=datetime(2026, 8, 27, 10, 0, tzinfo=UTC))

    assert result is run
    assert invalid.enabled is False
    assert valid.next_run_at == datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
    assert commits == 2
