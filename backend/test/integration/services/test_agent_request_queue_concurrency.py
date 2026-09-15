"""PostgreSQL concurrency coverage for Agent request intake."""

from __future__ import annotations

from yuxi.services.agent_request_service import AgentRequestInput, RunOrigin
from yuxi.services import agent_request_service
from types import SimpleNamespace

import asyncio
import os
import uuid
from yuxi.agents.context import BaseContext
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services import agent_request_queue_service
from yuxi.services import context_compression_service
from yuxi.services import run_worker
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.models_business import (
    AgentRun,
    AgentRunRequest,
    Conversation,
    Message,
    Project,
    SubagentThread,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _queue_test_conversation(
    db,
    *,
    thread_id: str,
    uid: str,
    agent_id: str = "main",
    project_id: str | None = None,
) -> Conversation:
    """构造带真实 User 与 Project Owner 的队列测试会话。"""

    if await db.scalar(select(User.uid).where(User.uid == uid)) is None:
        db.add(User(username=uid, uid=uid, password_hash="test"))
        await db.flush()
    if project_id is None:
        project_id = str(uuid.uuid4())
        db.add(
            Project(
                id=project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )
        )
        await db.flush()
    return Conversation(
        thread_id=thread_id,
        uid=uid,
        project_id=project_id,
        agent_id=agent_id,
        status="active",
    )


async def _cleanup_queue_test_thread(session_factory, engine, thread_id: str) -> None:
    async with session_factory() as db:
        row = (
            await db.execute(
                select(Conversation.id, Conversation.project_id, Conversation.uid).where(
                    Conversation.thread_id == thread_id
                )
            )
        ).one_or_none()
        conversation_id = row.id if row else None
        await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id == thread_id))
        if conversation_id is not None:
            await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id == thread_id))
        await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
        if row:
            await db.execute(delete(Project).where(Project.id == row.project_id))
            await db.execute(delete(User).where(User.uid == row.uid))
        await db.commit()
    await engine.dispose()


async def test_concurrent_reject_requests_never_enter_queue(monkeypatch: pytest.MonkeyPatch):
    thread_id = f"pytest-reject-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    request_ids = [f"reject-{uuid.uuid4()}" for _ in range(2)]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_service,
        "resolve_agent_run_config",
        AsyncMock(return_value=("model", "default")),
    )

    async with session_factory() as db:
        conversation = await _queue_test_conversation(db, thread_id=thread_id, uid=uid)
        db.add(conversation)
        await db.commit()

    async def submit(request_id: str):
        async with session_factory() as db:
            result, _ = await agent_request_service._persist_request(
                db=db,
                agent_item=MagicMock(),
                agent_backend=MagicMock(),
                request_input=AgentRequestInput(
                    request_id=request_id,
                    agent_slug="main",
                    thread_id=thread_id,
                    input_message=build_chat_input_message(request_id),
                    queue_policy="reject",
                    origin=RunOrigin(source="chat", channel="web"),
                ),
                current_user=SimpleNamespace(uid=uid),
            )
            await db.commit()
            return result

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(submit(request_id) for request_id in request_ids)),
            timeout=10,
        )

        assert sorted(result.status for result in results) == ["dispatched", "rejected"]

        async with session_factory() as db:
            requests = (
                (await db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id.in_(request_ids))))
                .scalars()
                .all()
            )
            messages = (await db.execute(select(Message).where(Message.request_id.in_(request_ids)))).scalars().all()

        assert sorted(request.status for request in requests) == ["dispatched", "rejected"]
        assert sorted(message.delivery_status for message in messages) == ["dispatched", "rejected"]
    finally:
        async with session_factory() as db:
            now = utc_now_naive()
            await db.execute(
                update(AgentRun)
                .where(AgentRun.conversation_thread_id == thread_id)
                .values(status="cancelled", finished_at=now, updated_at=now)
            )
            await db.commit()
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_context_compression_holds_thread_lock_until_checkpoint_update(monkeypatch: pytest.MonkeyPatch):
    """主动压缩持有 Conversation 行锁时，普通 intake 不能并发派发。"""
    thread_id = f"pytest-compression-lock-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    request_id = f"queued-during-compression-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    compression_started = asyncio.Event()
    release_compression = asyncio.Event()

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return MagicMock(backend_id="ChatbotAgent", config_json={"context": {}})

    async def empty_config(*_args, **_kwargs):
        return {}

    async def model_spec(*_args, **_kwargs):
        return "provider:model"

    async def workdir(**_kwargs):
        return "projects/test"

    async def runtime(**_kwargs):
        return None

    async def compress(**_kwargs):
        compression_started.set()
        await asyncio.wait_for(release_compression.wait(), timeout=5)
        return {"status": "no_op", "before_tokens": 0, "after_tokens": 0}

    monkeypatch.setattr(context_compression_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(
        context_compression_service.agent_manager,
        "get_agent",
        lambda _backend_id: MagicMock(capabilities=["context_compression"], context_schema=BaseContext),
    )
    monkeypatch.setattr(context_compression_service, "resolve_agent_run_model_spec", model_spec)
    monkeypatch.setattr(context_compression_service, "ensure_conversation_workdir_available", workdir)
    monkeypatch.setattr(context_compression_service, "_ensure_runtime_available", runtime)
    monkeypatch.setattr(context_compression_service, "prepare_agent_runtime_context", AsyncMock())
    monkeypatch.setattr(context_compression_service, "_compress_agent_checkpoint", compress)
    monkeypatch.setattr(
        agent_request_service,
        "resolve_agent_run_config",
        AsyncMock(return_value=("provider:model", "default")),
    )

    async with session_factory() as db:
        db.add(await _queue_test_conversation(db, thread_id=thread_id, uid=uid))
        await db.commit()

    async def run_compression():
        async with session_factory() as db:
            return await context_compression_service.compress_thread_context(
                thread_id=thread_id,
                current_user=MagicMock(uid=uid, role="user"),
                db=db,
            )

    async def submit_message():
        async with session_factory() as db:
            result, _ = await agent_request_service._persist_request(
                db=db,
                agent_item=MagicMock(),
                agent_backend=MagicMock(),
                request_input=AgentRequestInput(
                    request_id=request_id,
                    agent_slug="main",
                    thread_id=thread_id,
                    input_message=build_chat_input_message("hello"),
                    origin=RunOrigin(source="chat", channel="web"),
                ),
                current_user=SimpleNamespace(uid=uid),
            )
            await db.commit()
            return result

    try:
        compression_task = asyncio.create_task(run_compression())
        await asyncio.wait_for(compression_started.wait(), timeout=5)
        intake_task = asyncio.create_task(submit_message())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(intake_task), timeout=0.2)

        release_compression.set()
        compression_result, intake_result = await asyncio.wait_for(
            asyncio.gather(compression_task, intake_task),
            timeout=10,
        )
        assert compression_result["status"] == "no_op"
        assert intake_result.status == "dispatched"
    finally:
        release_compression.set()
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_concurrent_steer_requests_keep_one_pending(monkeypatch: pytest.MonkeyPatch):
    """Conversation 行锁保证同一线程只接受一个待处理 Steer。"""
    thread_id = f"pytest-steer-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    active_run_id = f"active-{uuid.uuid4()}"
    active_request_id = f"active-request-{uuid.uuid4()}"
    request_ids = [f"steer-{uuid.uuid4()}" for _ in range(2)]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_service,
        "resolve_agent_run_config",
        AsyncMock(return_value=("model", "default")),
    )

    async with session_factory() as db:
        conversation = await _queue_test_conversation(db, thread_id=thread_id, uid=uid)
        db.add(conversation)
        await db.flush()
        active_message = Message(
            conversation_id=conversation.id,
            request_id=active_request_id,
            role="user",
            content="active",
            delivery_status="dispatched",
        )
        db.add(active_message)
        await db.flush()
        db.add(
            AgentRunRequest(
                request_id=active_request_id,
                uid=uid,
                agent_slug="main",
                conversation_thread_id=thread_id,
                source="chat",
                queue_policy="enqueue",
                status="dispatched",
                input_message_id=active_message.id,
                input_payload={},
            )
        )
        db.add(
            AgentRun(
                id=active_run_id,
                conversation_thread_id=thread_id,
                runtime_scope_id=thread_id,
                agent_slug="main",
                uid=uid,
                status="running",
                request_id=active_request_id,
                conversation_id=conversation.id,
                run_type="chat",
                input_payload={},
            )
        )
        await db.commit()

    async def submit(request_id: str):
        async with session_factory() as db:
            try:
                result, _ = await agent_request_service._persist_request(
                    db=db,
                    agent_item=MagicMock(),
                    agent_backend=MagicMock(),
                    request_input=AgentRequestInput(
                        request_id=request_id,
                        agent_slug="main",
                        thread_id=thread_id,
                        input_message=build_chat_input_message(request_id),
                        queue_policy="steer",
                        origin=RunOrigin(source="chat", channel="web"),
                    ),
                    current_user=SimpleNamespace(uid=uid),
                )
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(submit(request_id) for request_id in request_ids), return_exceptions=True),
            timeout=10,
        )

        accepted = [result for result in results if not isinstance(result, Exception)]
        conflicts = [result for result in results if isinstance(result, HTTPException)]
        assert len(accepted) == 1
        assert accepted[0].status == "queued"
        assert accepted[0].queue_policy == "steer"
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert conflicts[0].detail["code"] == "steer_already_pending"

        async with session_factory() as db:
            requests = (
                await db.scalars(select(AgentRunRequest).where(AgentRunRequest.request_id.in_(request_ids)))
            ).all()
        assert len(requests) == 1
        assert requests[0].queue_policy == "steer"
        assert requests[0].status == "queued"
    finally:
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_concurrent_enqueue_dispatches_fifo_head(monkeypatch: pytest.MonkeyPatch):
    thread_id = f"pytest-enqueue-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    request_ids = [f"enqueue-first-{uuid.uuid4()}", f"enqueue-second-{uuid.uuid4()}"]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_service,
        "resolve_agent_run_config",
        AsyncMock(return_value=("model", "default")),
    )

    original_create = AgentRunRequestRepository.create
    first_request_created = asyncio.Event()
    release_first_request = asyncio.Event()
    second_request_finished = asyncio.Event()

    async def controlled_create(self, **kwargs):
        request = await original_create(self, **kwargs)
        if kwargs["request_id"] == request_ids[0]:
            first_request_created.set()
            await asyncio.wait_for(release_first_request.wait(), timeout=5)
        return request

    monkeypatch.setattr(AgentRunRequestRepository, "create", controlled_create)

    async with session_factory() as db:
        db.add(await _queue_test_conversation(db, thread_id=thread_id, uid=uid))
        await db.commit()

    async def submit(request_id: str):
        async with session_factory() as db:
            result, _ = await agent_request_service._persist_request(
                db=db,
                agent_item=MagicMock(),
                agent_backend=MagicMock(),
                request_input=AgentRequestInput(
                    request_id=request_id,
                    agent_slug="main",
                    thread_id=thread_id,
                    input_message=build_chat_input_message(request_id),
                    queue_policy="enqueue",
                    origin=RunOrigin(source="chat", channel="web"),
                ),
                current_user=SimpleNamespace(uid=uid),
            )
            await db.commit()
            if request_id == request_ids[1]:
                second_request_finished.set()
            return result

    try:
        first_task = asyncio.create_task(submit(request_ids[0]))
        await asyncio.wait_for(first_request_created.wait(), timeout=5)
        second_task = asyncio.create_task(submit(request_ids[1]))

        try:
            await asyncio.wait_for(second_request_finished.wait(), timeout=1)
        except TimeoutError:
            pass
        finally:
            release_first_request.set()

        results = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)

        async with session_factory() as db:
            requests = (
                (await db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id.in_(request_ids))))
                .scalars()
                .all()
            )
        requests_by_id = {request.request_id: request for request in requests}
        results_by_id = {result.request_id: result for result in results}

        assert requests_by_id[request_ids[0]].status == "dispatched"
        assert results_by_id[request_ids[0]].status == "dispatched"
        assert requests_by_id[request_ids[1]].status == "queued"
        assert results_by_id[request_ids[1]].status == "queued"
    finally:
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_dispatch_retry_reenqueues_existing_pending_run(monkeypatch: pytest.MonkeyPatch):
    thread_id = f"pytest-dispatch-retry-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    request_id = f"dispatch-retry-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    enqueue_calls: list[str] = []
    materialized_workdirs: list[tuple[str, str]] = []

    async def flaky_enqueue(run_id: str):
        enqueue_calls.append(run_id)
        if len(enqueue_calls) == 1:
            raise ConnectionError("simulated Redis outage after commit")

    def materialize_workdir(bound_uid: str, workdir_path: str):
        materialized_workdirs.append((bound_uid, workdir_path))

    @asynccontextmanager
    async def session_context():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", flaky_enqueue)
    monkeypatch.setattr(agent_request_queue_service, "ensure_bound_user_workdir", materialize_workdir)
    monkeypatch.setattr(agent_request_queue_service.pg_manager, "get_async_session_context", session_context)

    async with session_factory() as db:
        conversation = await _queue_test_conversation(db, thread_id=thread_id, uid=uid)
        db.add(conversation)
        await db.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="queued",
            request_id=request_id,
            delivery_status="queued",
        )
        db.add(message)
        await db.flush()
        await AgentRunRequestRepository(db).create(
            request_id=request_id,
            uid=uid,
            agent_slug="main",
            conversation_thread_id=thread_id,
            input_message_id=message.id,
            input_payload={"model_spec": "model", "tool_approval_mode": "default"},
        )
        await db.commit()

    try:
        with pytest.raises(ConnectionError, match="Redis outage"):
            await agent_request_queue_service.dispatch_next_request(
                uid=uid,
                agent_slug="main",
                thread_id=thread_id,
            )

        recovered_run_id = await agent_request_queue_service.dispatch_next_request(
            uid=uid,
            agent_slug="main",
            thread_id=thread_id,
        )

        async with session_factory() as db:
            request = await db.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
            run = await db.scalar(select(AgentRun).where(AgentRun.request_id == request_id))

        assert request.status == "dispatched"
        assert run.status == "pending"
        assert recovered_run_id == run.id
        assert enqueue_calls == [run.id, run.id]
        expected_workdir = f"projects/{conversation.project_id}"
        assert materialized_workdirs == [(uid, expected_workdir), (uid, expected_workdir)]
    finally:
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_startup_recovery_reenqueues_pending_runs_without_queue_requests(monkeypatch: pytest.MonkeyPatch):
    uid = f"pytest-user-{uuid.uuid4()}"
    resume_thread_id = f"pytest-resume-{uuid.uuid4()}"
    parent_thread_id = f"pytest-subagent-parent-{uuid.uuid4()}"
    child_thread_id = f"pytest-subagent-{uuid.uuid4()}"
    resume_creator_run_id = str(uuid.uuid4())
    resume_run_id = str(uuid.uuid4())
    parent_run_id = str(uuid.uuid4())
    child_run_id = str(uuid.uuid4())
    pending_run_ids = [resume_run_id, child_run_id]
    thread_ids = [resume_thread_id, parent_thread_id, child_thread_id]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    enqueue_calls: list[str] = []
    materialized_workdirs: list[tuple[str, str]] = []

    @asynccontextmanager
    async def session_context():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def fake_enqueue(run_id: str):
        enqueue_calls.append(run_id)

    def materialize_workdir(bound_uid: str, workdir_path: str):
        materialized_workdirs.append((bound_uid, workdir_path))

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", fake_enqueue)
    monkeypatch.setattr(agent_request_queue_service, "ensure_bound_user_workdir", materialize_workdir)
    monkeypatch.setattr(agent_request_queue_service.pg_manager, "get_async_session_context", session_context)

    async with session_factory() as db:
        resume_conversation = await _queue_test_conversation(db, thread_id=resume_thread_id, uid=uid)
        parent_conversation = await _queue_test_conversation(db, thread_id=parent_thread_id, uid=uid)
        child_conversation = await _queue_test_conversation(
            db,
            thread_id=child_thread_id,
            uid=uid,
            agent_id="worker",
            project_id=parent_conversation.project_id,
        )
        child_conversation.status = "subagent"
        db.add_all([resume_conversation, parent_conversation, child_conversation])
        await db.flush()
        resume_creator = AgentRun(
            id=resume_creator_run_id,
            conversation_thread_id=resume_thread_id,
            runtime_scope_id=resume_thread_id,
            agent_slug="main",
            uid=uid,
            request_id=f"startup-resume-creator-{uuid.uuid4()}",
            conversation_id=resume_conversation.id,
            input_payload={"model_spec": "model"},
            status="interrupted",
            run_type="chat",
        )
        parent_run = AgentRun(
            id=parent_run_id,
            conversation_thread_id=parent_thread_id,
            runtime_scope_id=parent_thread_id,
            agent_slug="main",
            uid=uid,
            request_id=f"startup-parent-{uuid.uuid4()}",
            conversation_id=parent_conversation.id,
            input_payload={"model_spec": "model"},
            status="running",
            run_type="chat",
            worker_id=f"worker-parent:{uuid.uuid4()}",
            heartbeat_at=utc_now_naive(),
            lease_expires_at=utc_now_naive() + timedelta(minutes=5),
        )
        db.add_all([resume_creator, parent_run])
        await db.flush()
        relation = SubagentThread(
            uid=uid,
            parent_conversation_id=parent_conversation.id,
            child_conversation_id=child_conversation.id,
            child_thread_id=child_thread_id,
            subagent_slug="worker",
            created_by_run_id=parent_run_id,
        )
        db.add(relation)
        await db.flush()
        db.add_all(
            [
                AgentRun(
                    id=resume_run_id,
                    conversation_thread_id=resume_thread_id,
                    runtime_scope_id=resume_thread_id,
                    agent_slug="main",
                    uid=uid,
                    request_id=f"startup-resume-{uuid.uuid4()}",
                    conversation_id=resume_conversation.id,
                    input_payload={"model_spec": "model"},
                    status="pending",
                    run_type="resume",
                    created_by_run_id=resume_creator_run_id,
                ),
                AgentRun(
                    id=child_run_id,
                    conversation_thread_id=child_thread_id,
                    runtime_scope_id=parent_thread_id,
                    agent_slug="worker",
                    uid=uid,
                    request_id=f"startup-subagent-{uuid.uuid4()}",
                    conversation_id=child_conversation.id,
                    created_by_run_id=parent_run_id,
                    subagent_thread_relation_id=relation.id,
                    input_payload={"model_spec": "model"},
                    status="pending",
                    run_type="subagent",
                ),
            ]
        )
        await db.commit()

    try:
        await agent_request_queue_service.recover_pending_dispatches()

        async with session_factory() as db:
            request_count = len(
                (
                    await db.scalars(
                        select(AgentRunRequest).where(
                            AgentRunRequest.conversation_thread_id.in_([resume_thread_id, child_thread_id])
                        )
                    )
                ).all()
            )

        assert sorted(enqueue_calls) == sorted(pending_run_ids)
        assert sorted(materialized_workdirs) == sorted(
            [
                (uid, f"projects/{resume_conversation.project_id}"),
                (uid, f"projects/{parent_conversation.project_id}"),
            ]
        )
        assert request_count == 0
    finally:
        async with session_factory() as db:
            await db.execute(
                delete(AgentRun).where(
                    AgentRun.id.in_([resume_creator_run_id, resume_run_id, child_run_id, parent_run_id])
                )
            )
            await db.execute(delete(SubagentThread).where(SubagentThread.child_thread_id == child_thread_id))
            await db.execute(delete(Conversation).where(Conversation.thread_id.in_(thread_ids)))
            await db.execute(
                delete(Project).where(Project.id.in_([resume_conversation.project_id, parent_conversation.project_id]))
            )
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


async def test_terminal_status_loser_does_not_change_message_delivery_status(monkeypatch: pytest.MonkeyPatch):
    thread_id = f"pytest-terminal-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    request_id = f"terminal-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    worker_id = f"worker-terminal:{uuid.uuid4()}"
    lease_now = utc_now_naive()
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", session_context)

    async with session_factory() as db:
        conversation = await _queue_test_conversation(db, thread_id=thread_id, uid=uid)
        db.add(conversation)
        await db.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="input",
            request_id=request_id,
            delivery_status="dispatched",
        )
        db.add(message)
        await db.flush()
        db.add(
            AgentRun(
                id=run_id,
                conversation_thread_id=thread_id,
                runtime_scope_id=thread_id,
                agent_slug="main",
                uid=uid,
                request_id=request_id,
                conversation_id=conversation.id,
                input_message_id=message.id,
                input_payload={},
                status="running",
                run_type="chat",
                worker_id=worker_id,
                heartbeat_at=lease_now,
                lease_expires_at=lease_now + timedelta(minutes=5),
            )
        )
        await db.commit()

    try:
        async with session_factory() as db:
            run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id))
            output_message = Message(
                conversation_id=run.conversation_id,
                role="assistant",
                content="completed output",
                run_id=run.id,
                request_id=run.request_id,
            )
            db.add(output_message)
            await db.flush()
            await AgentRunRepository(db).set_output_message(
                run.id,
                output_message.id,
                worker_id=worker_id,
                now=lease_now + timedelta(seconds=1),
            )
            await db.commit()

        completed = await run_worker.mark_run_terminal(run_id, "completed", worker_id=worker_id)
        cancelled = await run_worker.mark_run_terminal(
            run_id,
            "cancelled",
            error_type="cancelled",
            error_message="late cancel",
            worker_id=worker_id,
        )

        async with session_factory() as db:
            run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id))
            message = await db.scalar(select(Message).where(Message.request_id == request_id, Message.role == "user"))

        assert completed.changed is True
        assert completed.status == "completed"
        assert cancelled.changed is False
        assert cancelled.status == "completed"
        assert run.status == "completed"
        assert message.delivery_status == "complete"
    finally:
        await _cleanup_queue_test_thread(session_factory, engine, thread_id)


async def test_concurrent_request_id_reuse_across_threads_returns_scope_conflict(monkeypatch: pytest.MonkeyPatch):
    thread_ids = [f"pytest-idem-a-{uuid.uuid4()}", f"pytest-idem-b-{uuid.uuid4()}"]
    uid = f"pytest-user-{uuid.uuid4()}"
    request_id = f"shared-request-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_service,
        "resolve_agent_run_config",
        AsyncMock(return_value=("model", "default")),
    )

    async with session_factory() as db:
        conversations = [await _queue_test_conversation(db, thread_id=thread_id, uid=uid) for thread_id in thread_ids]
        db.add_all(conversations)
        await db.commit()

    async def submit(thread_id: str):
        async with session_factory() as db:
            try:
                result, _ = await agent_request_service._persist_request(
                    db=db,
                    agent_item=MagicMock(),
                    agent_backend=MagicMock(),
                    request_input=AgentRequestInput(
                        request_id=request_id,
                        agent_slug="main",
                        thread_id=thread_id,
                        input_message=build_chat_input_message(thread_id),
                        queue_policy="enqueue",
                        origin=RunOrigin(source="chat", channel="web"),
                    ),
                    current_user=SimpleNamespace(uid=uid),
                )
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(submit(thread_id) for thread_id in thread_ids), return_exceptions=True),
            timeout=10,
        )

        successful = [result for result in results if not isinstance(result, Exception)]
        conflicts = [result for result in results if isinstance(result, HTTPException)]
        assert len(successful) == 1
        assert successful[0].status == "dispatched"
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert conflicts[0].detail["code"] == "request_id_conflict"

        async with session_factory() as db:
            requests = (await db.scalars(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))).all()
            messages = (await db.scalars(select(Message).where(Message.request_id == request_id))).all()
            runs = (await db.scalars(select(AgentRun).where(AgentRun.request_id == request_id))).all()
        assert len(requests) == 1
        assert len(messages) == 1
        assert len(runs) == 1
    finally:
        async with session_factory() as db:
            project_ids = list(
                (await db.scalars(select(Conversation.project_id).where(Conversation.thread_id.in_(thread_ids)))).all()
            )
            now = utc_now_naive()
            await db.execute(
                update(AgentRun)
                .where(AgentRun.conversation_thread_id.in_(thread_ids))
                .values(status="cancelled", finished_at=now, updated_at=now)
            )
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id.in_(thread_ids)))
            conversation_ids = list(
                (await db.scalars(select(Conversation.id).where(Conversation.thread_id.in_(thread_ids)))).all()
            )
            if conversation_ids:
                await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
            await db.commit()
        async with session_factory() as db:
            await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id.in_(thread_ids)))
            await db.execute(delete(Conversation).where(Conversation.thread_id.in_(thread_ids)))
            await db.execute(delete(Project).where(Project.id.in_(project_ids)))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()
