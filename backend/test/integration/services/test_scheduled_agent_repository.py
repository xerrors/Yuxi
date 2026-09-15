"""用户定时 Agent 的真实 PostgreSQL 并发与历史语义。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from yuxi.repositories.scheduled_agent_repository import ScheduledAgentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services import scheduled_agent_service as service
from yuxi.services.scheduled_agent_service import _claim_due_run, _create_run_record
from yuxi.storage.postgres.models_business import (
    AgentRun,
    AgentRunRequest,
    Conversation,
    Message,
    Project,
    ScheduledAgentJob,
    ScheduledAgentRun,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_claim_concurrency_coalesce_and_soft_delete_history():
    """并发只领取一次，misfire 合并且软删除保留执行记录。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-user-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = utc_now_naive()

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
            runs = list(
                (await db.execute(select(ScheduledAgentRun).where(ScheduledAgentRun.job_id == job_id))).scalars()
            )
            assert len(runs) == 1
            scheduled_run = runs[0]
            assert scheduled_run.thread_id
            assert job.next_run_at > now

            conversation = Conversation(
                thread_id=scheduled_run.thread_id,
                uid=uid,
                agent_id="chatbot",
                project_id=project_id,
            )
            db.add(conversation)
            await db.flush()
            message = Message(
                conversation_id=conversation.id,
                request_id=scheduled_run.request_id,
                role="user",
                content="hello",
                delivery_status="dispatched",
            )
            db.add(message)
            await db.flush()
            agent_run_id = f"run-{uuid.uuid4()}"
            agent_run = AgentRun(
                id=agent_run_id,
                conversation_thread_id=scheduled_run.thread_id,
                runtime_scope_id=scheduled_run.thread_id,
                agent_slug="chatbot",
                uid=uid,
                status="completed",
                request_id=scheduled_run.request_id,
                source="scheduled_agent",
                channel="worker",
                conversation_id=conversation.id,
                run_type="chat",
                input_payload={},
                finished_at=now,
            )
            db.add(agent_run)
            await db.flush()
            db.add(
                AgentRunRequest(
                    request_id=scheduled_run.request_id,
                    uid=uid,
                    agent_slug="chatbot",
                    conversation_thread_id=scheduled_run.thread_id,
                    source="scheduled_agent",
                    channel="worker",
                    queue_policy="enqueue",
                    status="dispatched",
                    input_message_id=message.id,
                    dispatched_run_id=agent_run_id,
                    input_payload={},
                )
            )
            scheduled_run.status = "submitted"
            await db.flush()
            assert await repo.has_active_run(job_id) is False

            agent_run.status = "running"
            agent_run.finished_at = None
            await db.flush()
            assert await repo.has_active_run(job_id) is True

            manual_now = utc_now_naive()
            manual = await _create_run_record(
                repo=repo,
                job=job,
                trigger="manual",
                occurrence_key=f"manual:{uuid.uuid4()}",
                scheduled_for=manual_now,
            )
            assert manual.status == "skipped"

            agent_run.status = "completed"
            agent_run.finished_at = now
            await db.flush()
            assert await repo.has_active_run(job_id) is False

            await repo.delete_job(job)
            await db.commit()
            manual_id = manual.id

        async with session_factory() as db:
            repo = ScheduledAgentRepository(db)
            assert await repo.get_job(job_id, uid) is None
            assert await repo.get_job(job_id, uid, include_deleted=True) is not None
            runs = await repo.list_recent_runs([job_id], uid, 20)
            assert manual_id in {run.id for run, _request, _agent_run in runs}
    finally:
        async with session_factory() as db:
            await db.execute(delete(ScheduledAgentRun).where(ScheduledAgentRun.job_id == job_id))
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.uid == uid))
            await db.execute(delete(AgentRun).where(AgentRun.uid == uid))
            await db.execute(
                delete(Message).where(
                    Message.conversation_id.in_(select(Conversation.id).where(Conversation.uid == uid))
                )
            )
            await db.execute(delete(Conversation).where(Conversation.uid == uid))
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
                    next_run_at=utc_now_naive() - timedelta(minutes=1),
                )
            )
            await db.commit()

        async with session_factory() as db:
            assert await ScheduledAgentRepository(db).claim_due_job(now=utc_now_naive()) is None
    finally:
        async with session_factory() as db:
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_dispatch_failure_is_recovered_exactly_once(monkeypatch):
    """Request 写入前的瞬时失败必须保留意图，并由恢复轮次幂等提交。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-recovery-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    scheduled_run_id = f"scheduled-run-{uuid.uuid4()}"
    request_id = f"request-{uuid.uuid4()}"
    thread_id = f"thread-{uuid.uuid4()}"
    calls = 0

    async def accept_project(project_id_arg, user, db):
        del db
        assert (project_id_arg, user.uid) == (project_id, uid)

    async def accept_agent(agent_slug, user, db):
        del db
        assert (agent_slug, user.uid) == ("chatbot", uid)

    async def fail_once_then_persist(*, request_input, current_user, db):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database interruption")
        conversation = Conversation(
            thread_id=request_input.thread_id,
            creation_request_id=request_input.request_id,
            uid=str(current_user.uid),
            agent_id=request_input.agent_slug,
            title=request_input.conversation_title,
            project_id=request_input.conversation_project_id,
        )
        db.add(conversation)
        await db.flush()
        message = Message(
            conversation_id=conversation.id,
            request_id=request_input.request_id,
            role="user",
            content="hello",
            delivery_status="queued",
        )
        db.add(message)
        await db.flush()
        db.add(
            AgentRunRequest(
                request_id=request_input.request_id,
                uid=str(current_user.uid),
                agent_slug=request_input.agent_slug,
                conversation_thread_id=request_input.thread_id,
                source="scheduled_agent",
                channel="worker",
                external_id=scheduled_run_id,
                origin_metadata={},
                queue_policy="enqueue",
                status="queued",
                input_message_id=message.id,
                input_payload={},
            )
        )
        await db.flush()
        return {"request_id": request_input.request_id, "status": "queued"}

    monkeypatch.setattr(service, "_validate_project", accept_project)
    monkeypatch.setattr(service, "_validate_agent", accept_agent)
    monkeypatch.setattr(service, "submit_agent_request", fail_once_then_persist)

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
                    next_run_at=utc_now_naive() + timedelta(days=1),
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentRun(
                    id=scheduled_run_id,
                    job_id=job_id,
                    request_id=request_id,
                    thread_id=thread_id,
                    trigger="scheduled",
                    occurrence_key="scheduled:recovery",
                    scheduled_for=utc_now_naive() - timedelta(minutes=2),
                    project_id=project_id,
                    agent_slug="chatbot",
                    conversation_title="Recovery Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    status="dispatching",
                    created_at=utc_now_naive() - timedelta(minutes=2),
                )
            )
            await db.commit()

        with pytest.raises(RuntimeError, match="temporary database interruption"):
            await service.dispatch_scheduled_run(scheduled_run_id=scheduled_run_id)

        async with session_factory() as db:
            scheduled_run = await db.get(ScheduledAgentRun, scheduled_run_id)
            request_count = len(
                list(
                    (
                        await db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
                    ).scalars()
                )
            )
            assert scheduled_run is not None and scheduled_run.status == "dispatching"
            assert request_count == 0

        async def list_only_test_run(repository, *, before, limit=100):
            del before, limit
            scheduled_run = await repository.db.get(ScheduledAgentRun, scheduled_run_id)
            return [scheduled_run] if scheduled_run is not None else []

        monkeypatch.setattr(ScheduledAgentRepository, "list_dispatching_runs", list_only_test_run)
        assert await service.recover_scheduled_dispatches(limit=10) == 1

        async with session_factory() as db:
            scheduled_run = await db.get(ScheduledAgentRun, scheduled_run_id)
            requests = list(
                (await db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))).scalars()
            )
            assert scheduled_run is not None and scheduled_run.status == "submitted"
            assert len(requests) == 1
            assert calls == 2
    finally:
        async with session_factory() as db:
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
            await db.execute(delete(Message).where(Message.request_id == request_id))
            await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await db.execute(delete(ScheduledAgentRun).where(ScheduledAgentRun.id == scheduled_run_id))
            await db.execute(delete(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_soft_deletion_removes_scheduled_job_history():
    """真实账号软删除必须同步移除任务定义与调度历史。"""
    database_url = os.environ["POSTGRES_URL"]
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"scheduled-cascade-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    run_id = f"scheduled-run-{uuid.uuid4()}"

    try:
        async with session_factory() as db:
            db.add(User(username=uid, uid=uid, password_hash="test", role="user"))
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
                    next_run_at=utc_now_naive(),
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentRun(
                    id=run_id,
                    job_id=job_id,
                    request_id=f"request-{uuid.uuid4()}",
                    thread_id=f"thread-{uuid.uuid4()}",
                    trigger="manual",
                    occurrence_key=f"manual:{uuid.uuid4()}",
                    scheduled_for=utc_now_naive(),
                    project_id=project_id,
                    agent_slug="chatbot",
                    conversation_title="Cascade Job",
                    prompt="hello",
                    tool_approval_mode="default",
                    status="failed",
                )
            )
            await db.commit()

        async with session_factory() as db:
            user = await db.scalar(select(User).where(User.uid == uid).with_for_update())
            assert user is not None
            await UserRepository(db).delete_for_admin(user)
            await db.commit()

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
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()
