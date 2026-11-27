"""用户定时 Agent 的真实 PostgreSQL 并发与历史语义。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from yuxi.modules.schedules.repository import ScheduledAgentRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
import yuxi.modules.schedules.service as service
from yuxi.modules.schedules.service import _claim_due_run, _create_run_record
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.workspace.models import Project
from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun
from yuxi.modules.identity.models import User
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.asyncio
async def test_claim_concurrency_coalesce_and_soft_delete_history():
    """并发只领取一次，misfire 合并且软删除保留执行记录。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-user-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = utc_now()

    try:
        async with session_factory() as db:
            db.add(User(username=uid, uid=uid, password_hash="test", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    name="Scheduled Project",
                    selection_status="selectable",
                    workdir_path=f"projects/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentJob(
                    id=job_id,
                    uid=uid,
                    creation_request_id=f"test-create-{job_id}",
                    creation_intent_hash="0" * 64,
                    project_id=project_id,
                    agent_slug="chatbot",
                    name="Scheduled Job",
                    prompt="hello",
                    tool_approval_mode="always_trust",
                    cron_expression="* * * * *",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=now - timedelta(minutes=10),
                )
            )
            await db.commit()

        async def claim():
            async with session_factory() as db:
                run = await _claim_due_run(db=db, now=now)
                return run.id if run else None

        claimed = await asyncio.gather(claim(), claim())
        assert sum(item is not None for item in claimed) == 1

        async with session_factory() as db:
            repo = ScheduledAgentRepository(db)
            job = await repo.get_job(job_id, uid, lock=True)
            runs = list((await db.execute(select(ScheduledAgentRun).where(ScheduledAgentRun.job_id == job_id))).scalars())
            assert len(runs) == 1
            scheduled_run = runs[0]
            assert scheduled_run.thread_id
            assert job.next_run_at > now

            agent_session = Session(
                thread_id=scheduled_run.thread_id,
                uid=uid,
                agent_id="chatbot",
                project_id=project_id,
            )
            db.add(agent_session)
            await db.flush()
            input_repo = AgentInputRepository(db)
            input_item = await input_repo.create(
                input_id=scheduled_run.input_id,
                thread_id=scheduled_run.thread_id,
                uid=uid,
                app_id=None,
                agent_slug="chatbot",
                kind="follow_up",
                messages=[serialize_input_message(build_chat_input_message("hello"))],
                source="scheduled_agent",
                channel="worker",
            )
            await AgentInputReceiptRepository(db).create(
                receipt_id=f"receipt-{uuid.uuid4()}",
                idempotency_key=scheduled_run.id,
                uid=uid,
                app_id=None,
                thread_id=scheduled_run.thread_id,
                event_type="message",
                intent_hash="scheduled",
                input_id=input_item.id,
            )
            scheduled_run.status = "submitted"
            await db.flush()
            assert await repo.has_active_run(job_id) is True

            manual_now = utc_now()
            manual = await _create_run_record(
                repo=repo,
                job=job,
                trigger="manual",
                occurrence_key=f"manual:{uuid.uuid4()}",
                scheduled_for=manual_now,
            )
            assert manual.status == "skipped"

            turn = await AgentTurnRepository(db).create(
                turn_id=f"turn-{uuid.uuid4()}", thread_id=scheduled_run.thread_id, uid=uid, app_id=None
            )
            agent_run = await AgentRunRepository(db).create_run(
                run_id=f"run-{uuid.uuid4()}",
                thread_id=scheduled_run.thread_id,
                agent_slug="chatbot",
                uid=uid,
                turn_id=turn.id,
                input_payload={},
                source="scheduled_agent",
                channel="worker",
                session_record_id=agent_session.id,
            )
            await AgentTurnRepository(db).set_current(turn, run_id=agent_run.id)
            [message] = await input_repo.consume(inputs=[input_item], turn_id=turn.id, run_id=agent_run.id)
            agent_run.input_message_id = message.id
            agent_run.status = "running"
            assert await repo.has_active_run(job_id) is True

            agent_run.status = "completed"
            agent_run.finished_at = now
            await db.flush()
            await AgentTurnRepository(db).set_terminal(turn, status="completed", result_run_id=agent_run.id)
            assert await repo.has_active_run(job_id) is False

            await repo.delete_job(job)
            await db.commit()
            manual_id = manual.id

        async with session_factory() as db:
            repo = ScheduledAgentRepository(db)
            assert await repo.get_job(job_id, uid) is None
            deleted_job = await db.get(ScheduledAgentJob, job_id)
            assert deleted_job is not None and deleted_job.deleted_at is not None
            runs = await repo.list_recent_runs([job_id], uid, 20)
            assert manual_id in {run.id for run, _input, _agent_run in runs}
            submitted = next(row for row in runs if row[0].id == scheduled_run.id)
            assert submitted[1].id == input_item.id and submitted[2].id == agent_run.id
    finally:
        async with session_factory() as db:
            await db.execute(delete(ScheduledAgentRun).where(ScheduledAgentRun.job_id == job_id))
            await db.execute(update(AgentTurn).where(AgentTurn.uid == uid).values(current_run_id=None, result_run_id=None))
            await db.execute(delete(AgentInputReceipt).where(AgentInputReceipt.uid == uid))
            await db.execute(delete(Message).where(Message.session_record_id.in_(select(Session.id).where(Session.uid == uid))))

            await db.execute(delete(AgentInput).where(AgentInput.uid == uid))
            await db.execute(delete(AgentRun).where(AgentRun.uid == uid))
            await db.execute(delete(AgentTurn).where(AgentTurn.uid == uid))
            await db.execute(delete(Session).where(Session.uid == uid))
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleted_user_job_is_not_claimed():
    """软删除用户的任务不能继续产生后台副作用。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-deleted-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    try:
        async with session_factory() as db:
            db.add(User(username=uid, uid=uid, password_hash="test", role="user", is_deleted=1))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    name="Deleted User Project",
                    selection_status="selectable",
                    workdir_path=f"projects/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentJob(
                    id=job_id,
                    uid=uid,
                    creation_request_id=f"test-create-{job_id}",
                    creation_intent_hash="0" * 64,
                    project_id=project_id,
                    agent_slug="chatbot",
                    name="Deleted User Job",
                    prompt="hello",
                    tool_approval_mode="always_trust",
                    cron_expression="* * * * *",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=utc_now() - timedelta(minutes=1),
                )
            )
            await db.commit()

        async with session_factory() as db:
            assert await ScheduledAgentRepository(db).claim_due_job(now=utc_now()) is None
    finally:
        async with session_factory() as db:
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_dispatch_failure_is_recovered_exactly_once(monkeypatch):
    """Input 接收前的瞬时失败保留定时意图，恢复后只接收一次。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-recovery-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    scheduled_run_id = f"scheduled-run-{uuid.uuid4()}"
    input_id = f"input-{uuid.uuid4()}"
    thread_id = f"thread-{uuid.uuid4()}"
    calls = 0

    async def accept_project(project_id_arg, user, db):
        del db
        assert (project_id_arg, user.uid) == (project_id, uid)

    async def accept_agent(agent_slug, user, db):
        del db
        assert (agent_slug, user.uid) == ("chatbot", uid)

    async def fail_once_then_persist(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database interruption")
        db = kwargs["db"]
        scope = kwargs["scope"]
        assert (scope.uid, kwargs["agent_slug"], kwargs["thread_id"]) == (uid, "chatbot", thread_id)
        assert kwargs["idempotency_key"] == scheduled_run_id
        agent_session = Session(
            thread_id=thread_id,
            uid=uid,
            agent_id="chatbot",
            title=kwargs["title"],
            project_id=project_id,
        )
        db.add(agent_session)
        await db.flush()
        input_item = await AgentInputRepository(db).create(
            input_id=input_id,
            thread_id=thread_id,
            uid=uid,
            app_id=None,
            agent_slug="chatbot",
            kind="follow_up",
            source="scheduled_agent",
            channel="worker",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        )
        await AgentInputReceiptRepository(db).create(
            receipt_id=f"receipt-{uuid.uuid4()}",
            idempotency_key=scheduled_run_id,
            uid=uid,
            app_id=None,
            thread_id=thread_id,
            event_type="yuxi.session.create",
            intent_hash="scheduled",
            input_id=input_item.id,
        )
        await db.commit()
        return {"input_id": input_id, "status": "queued"}

    monkeypatch.setattr(service, "_validate_project", accept_project)
    monkeypatch.setattr(service, "_validate_agent", accept_agent)
    monkeypatch.setattr(service, "create_thread", fail_once_then_persist)

    class ScopedManager:
        @asynccontextmanager
        async def get_async_session_context(self):
            async with session_factory() as db:
                yield db

    monkeypatch.setattr(service, "pg_manager", ScopedManager())

    try:
        async with session_factory() as db:
            db.add(User(username=uid, uid=uid, password_hash="test", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    name="Recovery Project",
                    selection_status="selectable",
                    workdir_path=f"projects/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentJob(
                    id=job_id,
                    uid=uid,
                    creation_request_id=f"test-create-{job_id}",
                    creation_intent_hash="0" * 64,
                    project_id=project_id,
                    agent_slug="chatbot",
                    name="Recovery Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    cron_expression="0 9 * * *",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=utc_now() + timedelta(days=1),
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentRun(
                    id=scheduled_run_id,
                    job_id=job_id,
                    input_id=input_id,
                    thread_id=thread_id,
                    trigger="scheduled",
                    occurrence_key="scheduled:recovery",
                    scheduled_for=utc_now() - timedelta(minutes=2),
                    project_id=project_id,
                    agent_slug="chatbot",
                    session_title="Recovery Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    status="dispatching",
                    created_at=utc_now() - timedelta(minutes=2),
                )
            )
            await db.commit()

        with pytest.raises(RuntimeError, match="temporary database interruption"):
            await service.dispatch_scheduled_run(scheduled_run_id=scheduled_run_id)

        async with session_factory() as db:
            scheduled_run = await db.get(ScheduledAgentRun, scheduled_run_id)
            input_count = len(list((await db.execute(select(AgentInput).where(AgentInput.id == input_id))).scalars()))
            assert scheduled_run is not None and scheduled_run.status == "dispatching"
            assert input_count == 0

        async def list_only_test_run(repository, *, before, limit=100):
            del before, limit
            scheduled_run = await repository.db.get(ScheduledAgentRun, scheduled_run_id)
            return [scheduled_run] if scheduled_run is not None else []

        monkeypatch.setattr(ScheduledAgentRepository, "list_dispatching_runs", list_only_test_run)
        assert await service.recover_scheduled_dispatches(limit=10) == 1

        async with session_factory() as db:
            scheduled_run = await db.get(ScheduledAgentRun, scheduled_run_id)
            inputs = list((await db.execute(select(AgentInput).where(AgentInput.id == input_id))).scalars())
            assert scheduled_run is not None and scheduled_run.status == "submitted"
            assert len(inputs) == 1
            assert calls == 2
    finally:
        async with session_factory() as db:
            await db.execute(delete(AgentInputReceipt).where(AgentInputReceipt.input_id == input_id))
            await db.execute(delete(AgentInput).where(AgentInput.id == input_id))
            await db.execute(delete(Message).where(Message.session_record_id.in_(select(Session.id).where(Session.thread_id == thread_id))))
            await db.execute(delete(Session).where(Session.thread_id == thread_id))
            await db.execute(delete(ScheduledAgentRun).where(ScheduledAgentRun.id == scheduled_run_id))
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.self_managed_resources
async def test_account_soft_deletion_removes_scheduled_job_history():
    """真实账号软删除必须同步移除任务定义与调度历史。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    actor_uid = f"scheduled-admin-{uuid.uuid4()}"
    uid = f"scheduled-cascade-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    run_id = f"scheduled-run-{uuid.uuid4()}"

    try:
        async with session_factory() as db:
            db.add(User(username=uid, uid=uid, password_hash="test", role="user"))
            db.add(User(username=actor_uid, uid=actor_uid, password_hash="test", role="admin"))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    name="Cascade Project",
                    selection_status="selectable",
                    workdir_path=f"projects/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentJob(
                    id=job_id,
                    uid=uid,
                    creation_request_id=f"test-create-{job_id}",
                    creation_intent_hash="0" * 64,
                    project_id=project_id,
                    agent_slug="chatbot",
                    name="Cascade Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    cron_expression="0 9 * * *",
                    timezone="UTC",
                    enabled=False,
                    next_run_at=utc_now(),
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentRun(
                    id=run_id,
                    job_id=job_id,
                    input_id=f"input-{uuid.uuid4()}",
                    thread_id=f"thread-{uuid.uuid4()}",
                    trigger="manual",
                    occurrence_key=f"manual:{uuid.uuid4()}",
                    scheduled_for=utc_now(),
                    project_id=project_id,
                    agent_slug="chatbot",
                    session_title="Cascade Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    status="failed",
                )
            )
            await db.commit()

        async with session_factory() as db:
            user = await db.scalar(select(User).where(User.uid == uid).with_for_update())
            assert user is not None
            from yuxi.modules.identity.services.users import delete_user

            actor = await db.scalar(select(User).where(User.uid == actor_uid))
            await delete_user(db, current_user=actor, user_id=user.id)

        async with session_factory() as db:
            user = await db.scalar(select(User).where(User.uid == uid))
            assert user is not None and user.is_deleted == 1
            assert await db.get(ScheduledAgentJob, job_id) is None
            assert await db.get(ScheduledAgentRun, run_id) is None
            assert await db.get(Project, project_id) is not None
    finally:
        async with session_factory() as db:
            await db.execute(delete(ScheduledAgentRun).where(ScheduledAgentRun.id == run_id))
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid.in_([uid, actor_uid])))
            await db.commit()
        await engine.dispose()
