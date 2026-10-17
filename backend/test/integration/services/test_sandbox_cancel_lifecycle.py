"""真实 HTTP、PostgreSQL、Redis、worker 执行链与 GEM 的取消证据。"""

import asyncio
import importlib.util
import json
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import anyio
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.agent_router import agent_router
from server.utils.auth_middleware import get_db, get_required_user
from test.http_helpers import live_http_server
from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, get_sandbox_provider
from yuxi.agents.backends.sandbox.provider import sandbox_provisioner_token
from yuxi.agents.context import BaseContext
from yuxi.services.skills.projection import get_user_skills_root_dir
from yuxi.config import get_skill_projection_dir
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services import run_worker
from yuxi.services.agent_run_manifest_service import PreparedRunExecution
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, Message, Project, User
from yuxi.storage.redis import close_async_redis_client, get_async_redis_client
from yuxi.workspace.paths import ensure_bound_user_workdir, global_user_data_dir, workspace_uid_dirname

from agent_run_test_helpers import create_agent_run

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def cancellation_db():
    """隔离业务表，避免测试 Run 被部署中的共享 worker 恢复扫描捕获。"""
    url = os.environ["POSTGRES_URL"]
    schema = f"test_sandbox_cancel_{uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield db
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def test_http_stop_kills_gem_before_worker_releases_fifo(cancellation_db, monkeypatch):
    """模型输出替换为固定脚本，取消/lease/事件/清理和文件均使用真实边界。"""
    db = cancellation_db
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    run_id, thread_id, message_id = await create_agent_run(
        factory, prefix="sandbox-cancel", message_content="cancel command", input_payload={}
    )
    async with factory() as fresh:
        run = await fresh.get(AgentRun, run_id)
        user = await fresh.scalar(select(User).where(User.uid == run.uid))
        conversation = await fresh.get(Conversation, run.conversation_id)
        project = await fresh.get(Project, conversation.project_id)
        message = await fresh.get(Message, message_id)
        message.extra_metadata = {}
        await fresh.commit()
    ensure_bound_user_workdir(user.uid, project.workdir_path)
    get_user_skills_root_dir(user.uid)
    backend = ProvisionerSandboxBackend(thread_id=thread_id, uid=user.uid, workdir_path=project.workdir_path)
    cleanup_entered, allow_cleanup = asyncio.Event(), asyncio.Event()
    execution = None

    @asynccontextmanager
    async def worker_session():
        """worker 与 HTTP 都只访问测试 Schema。"""
        async with factory() as fresh:
            try:
                yield fresh
                await fresh.commit()
            except BaseException:
                await fresh.rollback()
                raise

    async def http_db():
        """保留真实取消路由的事务提交顺序。"""
        async with worker_session() as fresh:
            yield fresh

    async def prepare(*, run, user, worker_id, workdir_binding):
        """固定模型准备结果，不调用收费模型；后续 worker 流程保持真实。"""
        return PreparedRunExecution(
            manifest={},
            backend_id="ChatbotAgent",
            context=BaseContext(
                uid=user.uid,
                thread_id=thread_id,
                run_id=run_id,
                request_id=run.request_id,
                worker_id=worker_id,
                runtime_scope_id=thread_id,
                workdir_relative_path=workdir_binding.workdir_path,
                workdir_path=f"/home/gem/user-data/{workdir_binding.workdir_path}",
            ),
        )

    async def command_stream(**_kwargs):
        """启动真实进程，延迟副作用必须被停止操作阻止。"""
        result = await backend.aexecute(
            'python3 -c "import os,time; from pathlib import Path; '
            "Path('/tmp/cancel-pid').write_text(str(os.getpid())); time.sleep(8); "
            "Path('/tmp/cancel-late').write_text('unexpected')\"",
            timeout=3600,
        )
        assert result.exit_code == 0, result.output
        yield json.dumps({"status": "finished", "thread_id": thread_id}).encode()

    release_runtime = run_worker._release_runtime_if_idle

    async def pause_before_release(run):
        """在真实清理前设置屏障，回读进程和 durable fence。"""
        cleanup_entered.set()
        await allow_cleanup.wait()
        return await release_runtime(run)

    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", worker_session)
    monkeypatch.setattr(run_worker, "prepare_and_record_run_execution", prepare)
    monkeypatch.setattr(run_worker, "stream_agent_chat", command_stream)
    monkeypatch.setattr(run_worker, "_read_run_token_usage_from_state", AsyncMock(return_value=None))
    monkeypatch.setattr(run_worker, "_release_runtime_if_idle", pause_before_release)
    app = FastAPI()
    app.include_router(agent_router, prefix="/api")
    app.dependency_overrides[get_db] = http_db
    app.dependency_overrides[get_required_user] = lambda: user
    provider = get_sandbox_provider()
    redis = await get_async_redis_client()
    try:
        # 预创建仅属于测试的 runtime，把镜像启动时间排除在停止时延之外。
        assert (await backend.aexecute("printf ready")).output.strip() == "ready"
        execution = asyncio.create_task(run_worker.process_agent_run({}, run_id))
        async with asyncio.timeout(15):
            while True:
                if execution.done():
                    await execution
                    async with factory() as fresh:
                        failed = await fresh.get(AgentRun, run_id)
                        pytest.fail(f"worker 未启动目标命令: {failed.error_type}: {failed.error_message}")
                result = await asyncio.to_thread(backend.execute, "test -f /tmp/cancel-pid")
                if result.exit_code == 0:
                    break
                await asyncio.sleep(0.1)
        async with live_http_server(app) as url, httpx.AsyncClient(base_url=url, timeout=15) as http:
            response = await http.post(f"/api/agent/runs/{run_id}/cancel")
            assert response.status_code == 200, response.text
            await asyncio.wait_for(cleanup_entered.wait(), 15)
            result = await asyncio.to_thread(
                backend.execute, "! kill -0 $(cat /tmp/cancel-pid) 2>/dev/null && test ! -e /tmp/cancel-late"
            )
            assert result.exit_code == 0, "取消已记录，但实际 GEM 进程仍存活"
            state = (await http.get(f"/api/agent/runs/{run_id}")).json()["run"]
            assert state["status"] == "cancelled" and state["runtime_cleanup_pending"] is True
            active_state = (await http.get(f"/api/agent/thread/{thread_id}/active_run")).json()["run"]
            assert active_state is not None, "刷新或新标签页必须看见尚未清理的 Run"
            assert active_state["id"] == run_id and active_state["runtime_cleanup_pending"] is True
            async with factory() as fresh:
                active = await AgentRunRepository(fresh).get_active_run_by_thread_for_user(
                    uid=user.uid, agent_slug="main", conversation_thread_id=thread_id
                )
                assert active.id == run_id, "清理前不得放行同一线程"
            events = await redis.xrange(f"run:events:{run_id}")
            assert not any(
                fields.get("event_type") == "end" or fields.get(b"event_type") == b"end" for _, fields in events
            )
            allow_cleanup.set()
            await asyncio.wait_for(execution, 15)
            assert (await http.get(f"/api/agent/thread/{thread_id}/active_run")).json()["run"] is None
            async with factory() as fresh:
                persisted = await fresh.get(AgentRun, run_id)
                assert persisted.status == "cancelled" and persisted.runtime_cleanup_pending is False
                assert persisted.worker_id is None and persisted.lease_expires_at is None
                assert (
                    await AgentRunRepository(fresh).get_active_run_by_thread_for_user(
                        uid=user.uid, agent_slug="main", conversation_thread_id=thread_id
                    )
                    is None
                )
            assert await asyncio.to_thread(provider._client.discover, backend.id) is None
            events = await redis.xrange(f"run:events:{run_id}")
            assert any(fields.get("event_type") == "end" or fields.get(b"event_type") == b"end" for _, fields in events)
            assert (await backend.aexecute("printf next-message")).output.strip() == "next-message"
    finally:
        allow_cleanup.set()
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        await asyncio.to_thread(provider.release, thread_id, uid=user.uid, workdir_path=project.workdir_path)
        await redis.delete(f"run:events:{run_id}", f"run:cancel:{run_id}")
        await close_async_redis_client()
        shutil.rmtree(global_user_data_dir(user.uid), ignore_errors=True)
        shutil.rmtree(get_skill_projection_dir() / workspace_uid_dirname(user.uid), ignore_errors=True)


async def test_busy_proxy_delete_is_bounded_and_other_runtime_stays_available():
    """真实长 HTTP 代理占用 pin 时删除返回 503，其他实例和健康检查仍可用。"""
    uid = f"pytest-busy-{uuid4().hex}"
    first = ProvisionerSandboxBackend(thread_id="busy", uid=uid, inherit_env=False)
    other = ProvisionerSandboxBackend(thread_id="other", uid=uid, inherit_env=False)
    provider = get_sandbox_provider()
    command = None
    client = None
    try:
        assert (await first.aexecute("printf ready")).exit_code == 0
        assert (await other.aexecute("printf ready")).exit_code == 0
        client = first._get_client()
        await asyncio.to_thread(client.shell.create_session, id="busy-proxy")
        command = asyncio.create_task(
            asyncio.to_thread(
                client.shell.exec_command,
                id="busy-proxy",
                command="touch /tmp/busy-started; sleep 12",
                timeout=20,
                hard_timeout=15,
            )
        )
        async with asyncio.timeout(10):
            while True:
                if (await asyncio.to_thread(first.execute, "test -f /tmp/busy-started")).exit_code == 0:
                    break
                await asyncio.sleep(0.1)
        url = os.environ["SANDBOX_PROVISIONER_URL"].rstrip("/")
        headers = {"Authorization": f"Bearer {sandbox_provisioner_token()}"}
        async with httpx.AsyncClient(base_url=url, headers=headers, timeout=10) as http:
            started_at = asyncio.get_running_loop().time()
            deletion = asyncio.create_task(http.delete(f"/api/sandboxes/{first.id}"))
            try:
                for _ in range(5):
                    health = await http.get("/health")
                    assert health.status_code == 200
                    assert (await other.aexecute("printf independent")).output.strip() == "independent"
                response = await deletion
                assert response.status_code == 503, response.text
                assert response.json()["detail"] == "sandbox busy"
                assert asyncio.get_running_loop().time() - started_at < 10
                assert (await http.get(f"/api/sandboxes/{first.id}")).status_code == 200
            finally:
                await asyncio.gather(deletion, return_exceptions=True)
    finally:
        try:
            if command is not None:
                await asyncio.wait_for(command, 25)
        finally:
            for backend in (first, other):
                await asyncio.to_thread(provider.release, backend._thread_id, uid=uid)


async def test_health_responds_when_cleanup_occupies_all_sync_threads(monkeypatch):
    """同步线程池被清理占满时，真实 HTTP 健康检查仍须及时响应。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "test-busy-health-token-at-least-32-characters")
    spec = importlib.util.spec_from_file_location(
        "busy_health_provisioner", Path(__file__).resolve().parents[4] / "docker/sandbox_provisioner/app.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    pins = module.sandbox_operation_pins
    pins.acquire("busy")
    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    deletion = None
    try:
        async with live_http_server(module.app) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
            deletion = asyncio.create_task(
                http.delete(
                    "/api/sandboxes/busy",
                    headers={"Authorization": "Bearer test-busy-health-token-at-least-32-characters"},
                )
            )
            async with asyncio.timeout(2):
                while "busy" not in pins._deleting:
                    await asyncio.sleep(0.01)
            async with asyncio.timeout(1):
                assert (await http.get("/health")).status_code == 200
            assert (await deletion).status_code == 503
    finally:
        pins.release("busy")
        limiter.total_tokens = original_tokens
        if deletion is not None:
            await asyncio.gather(deletion, return_exceptions=True)
        sys.modules.pop(spec.name, None)
