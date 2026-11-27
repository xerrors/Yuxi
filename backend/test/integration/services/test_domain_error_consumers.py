"""非 HTTP 调用方的领域拒绝与可重试故障分类。"""

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.services.agent_input_fixtures import SCOPE, create_input_schema, drop_input_schema, start_input_run

from test.integration.support.module_audit import isolated_audit_store
from yuxi.infrastructure.redis import create_arq_redis_pool
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.identity.models import User
from yuxi.modules.schedules import service
from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.paths import user_workspace_dir
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("transient", [False, True])
async def test_scheduled_dispatch_distinguishes_domain_rejection_from_transient_failure(monkeypatch, transient):
    """领域拒绝收敛为失败，临时故障保留可恢复意图且不产生 Input。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            db.add(User(uid="scheduled-domain-user", username="scheduled-domain-user", password_hash="fixture", role="user"))
            await db.flush()
            db.add(
                Project(
                    id="scheduled-domain-project",
                    uid="scheduled-domain-user",
                    selection_status="selectable",
                    directory_mode="managed",
                    workdir_path="projects/test",
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentJob(
                    id="domain-job",
                    uid="scheduled-domain-user",
                    creation_request_id="domain-create-job",
                    creation_intent_hash="0" * 64,
                    project_id="scheduled-domain-project",
                    agent_slug="missing-agent",
                    name="领域错误测试",
                    prompt="hello",
                    cron_expression="* * * * *",
                    timezone="UTC",
                    next_run_at=utc_now(),
                )
            )
            await db.flush()
            db.add(
                ScheduledAgentRun(
                    id="domain-run",
                    job_id="domain-job",
                    input_id="uncreated-input",
                    thread_id="uncreated-thread",
                    occurrence_key="domain-occurrence",
                    scheduled_for=utc_now(),
                    project_id="scheduled-domain-project",
                    agent_slug="missing-agent",
                    session_title="领域错误测试",
                    prompt="hello",
                    tool_approval_mode="default",
                    status="dispatching",
                )
            )
            await db.commit()

        if transient:
            monkeypatch.setattr(service, "_validate_agent", AsyncMock(side_effect=ConnectionError("temporary database failure")))
            with pytest.raises(ConnectionError, match="temporary database failure"):
                await service.dispatch_scheduled_run(scheduled_run_id="domain-run")
        else:
            result = await service.dispatch_scheduled_run(scheduled_run_id="domain-run")
            assert result["status"] == "failed"
            assert result["error_message"] == "智能体不存在或不可访问"

        async with sessions() as observer:
            run = await observer.get(ScheduledAgentRun, "domain-run")
            assert run.status == ("dispatching" if transient else "failed")
            assert run.error_message == (None if transient else "智能体不存在或不可访问")
            assert await observer.scalar(select(func.count()).select_from(AgentInput)) == 0


@pytest.mark.parametrize(
    ("deleted", "message"),
    [(True, "对话线程不存在"), (False, "对话的工作目录已不存在或不可访问")],
    ids=["session-deleted", "workdir-deleted"],
)
async def test_arq_worker_persists_domain_rejection_without_http_prefix(monkeypatch, tmp_path, deleted, message):
    """真实 ARQ 子进程拒绝失去执行条件的 pending Run，落库失败与消费归属。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    workdir = user_workspace_dir(SCOPE.uid) / "projects/input-project"
    workdir.mkdir(parents=True)
    schema, admin, engine = await create_input_schema()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = f"pytest_domain_worker_{uuid.uuid4().hex}"
    redis = await create_arq_redis_pool()
    process = None
    try:
        run = await start_input_run(sessions, running=False)
        if deleted:
            async with sessions() as db:
                agent_session = await db.scalar(select(Session).where(Session.thread_id == "input-thread"))
                agent_session.status = "deleted"
                await db.commit()
        else:
            workdir.rmdir()

        job = await redis.enqueue_job("process_agent_run", run.id, _queue_name=queue, _job_id=f"{queue}:run")
        assert job is not None
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _WORKER_SCRIPT,
            env={**os.environ, "YUXI_TEST_SCHEMA": schema, "YUXI_TEST_QUEUE": queue},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, output.decode()

        async with sessions() as observer:
            persisted = await observer.get(AgentRun, run.id)
            turn = await observer.get(AgentTurn, run.turn_id)
            original_input = await observer.get(AgentInput, "input-0")
            assert persisted.status == "failed"
            assert persisted.error_type == "invalid_runtime_scope"
            assert persisted.error_message == message
            assert persisted.finished_at is not None
            assert turn.status == "failed"
            assert turn.current_run_id == persisted.id
            assert turn.result_run_id is None
            assert persisted.output_message_id is None
            assert original_input.status == "consumed"
            assert original_input.consumed_run_id == persisted.id
        assert workdir.exists() is deleted
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        keys = [key async for key in redis.scan_iter(match=f"*{queue}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await drop_input_schema(schema, admin, engine)


_WORKER_SCRIPT = """
import asyncio
import os
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.workers.arq import YuxiWorker
from yuxi.workers.settings import WorkerSettings

async def startup(ctx):
    "将数据库连接限定到用例自有 Schema。"
    pg_manager.async_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        connect_args={"server_settings": {"search_path": os.environ["YUXI_TEST_SCHEMA"]}},
    )
    pg_manager.AsyncSession = async_sessionmaker(pg_manager.async_engine, expire_on_commit=False)
    pg_manager._initialized = True

async def shutdown(ctx):
    "退出时释放测试进程连接。"
    await pg_manager.close()

async def main():
    "通过正式 ARQ 任务与 worker 执行失败路径。"
    worker = YuxiWorker(
        functions=[next(function for function in WorkerSettings.functions if function.name == "process_agent_run")],
        redis_settings=WorkerSettings.redis_settings,
        queue_name=os.environ["YUXI_TEST_QUEUE"],
        health_check_key=os.environ["YUXI_TEST_QUEUE"] + ":health",
        burst=True,
        max_jobs=1,
        poll_delay=0.01,
        on_startup=startup,
        on_shutdown=shutdown,
    )
    try:
        await worker.async_run()
    finally:
        await worker.close()

asyncio.run(main())
"""
