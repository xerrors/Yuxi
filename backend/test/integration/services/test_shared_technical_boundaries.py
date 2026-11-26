"""共享技术边界的真实 PostgreSQL、Redis 与 HTTP 结果。"""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import select, text
from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.dependencies.auth import get_db, get_required_user, get_superadmin_user
from yuxi.api.routers.system import system
from yuxi.bootstrap.options import ensure_options_in_db
from yuxi.modules.documents.options import mineru_official_api_opts
from yuxi.modules.models.chat import LangChainChatAdapter
from yuxi.modules.models.embed import OtherEmbedding
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.options import system_options
from yuxi.modules.system.repositories.options import OPTION_SYNC_LOCK_KEY
from yuxi.modules.system.services import options

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_config_http_commit_cache_failure_and_recovery(monkeypatch):
    """HTTP 提交由独立 PG 回读证明，Redis 拒绝后仍回源，恢复后拒绝旧版本回填。"""
    namespace = f"pytest:step9:{uuid4().hex}:"
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    dead = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(options, "OPTION_CACHE_PREFIX", namespace + "value:")
    monkeypatch.setattr(options, "OPTION_CACHE_VERSION_PREFIX", namespace + "version:")
    monkeypatch.setattr(options, "get_async_redis_client", AsyncMock(return_value=redis))
    try:
        async with isolated_audit_store(monkeypatch) as (_, sessions):
            async with sessions.begin() as db:
                await ensure_options_in_db(db)
            app = FastAPI()
            app.include_router(system, prefix="/api")

            async def database():
                async with sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database
            app.dependency_overrides[get_superadmin_user] = lambda: SimpleNamespace(username="fixture")
            app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(username="fixture")
            async with serve_http(app) as client:
                config = (await client.get("/api/system/config")).json()
                assert config["max_cooperation_depth"] == 1
                assert config["max_cooperation_concurrency"] == 12
                invalid = await client.post("/api/system/config", json={"key": "max_cooperation_depth", "value": -1})
                assert invalid.status_code == 400
                saved = await client.post("/api/system/config", json={"key": "default_model", "value": "fixture:current"})
                assert saved.status_code == 200
                secret = await client.put(
                    "/api/system/config/options/mineru_official_api_opts", json={"value": {"api_key": "fixture-secret"}}
                )
                assert secret.status_code == 200
                assert secret.json()["option"]["value"]["api_key"] == ""
                async with sessions() as db:
                    rows = {row.key: row for row in (await db.scalars(select(ConfigOption))).all()}
                    assert rows["system_options"].value == {"default_model": "fixture:current"}
                    assert rows["mineru_official_api_opts"].value == {"api_key": "fixture-secret"}
                monkeypatch.setattr(options, "get_async_redis_client", AsyncMock(return_value=dead))
                assert (await system_options.get())["default_model"] == "fixture:current"
                monkeypatch.setattr(options, "get_async_redis_client", AsyncMock(return_value=redis))
                assert (await system_options.get())["default_model"] == "fixture:current"
                assert json.loads(await redis.get(namespace + "value:system_options")) == {"default_model": "fixture:current"}
                assert (await mineru_official_api_opts.get())["api_key"] == "fixture-secret"
                assert await redis.get(namespace + "value:mineru_official_api_opts") is None
                old_version = await options._load_cache_version("system_options")
                saved = await client.post("/api/system/config", json={"key": "default_model", "value": "fixture:new"})
                assert saved.status_code == 200
                await options._save_cached_value("system_options", {"default_model": "stale:model"}, old_version)
                assert await redis.get(namespace + "value:system_options") is None
                assert (await system_options.get())["default_model"] == "fixture:new"

                saved = await client.post("/api/system/config/update", json={"max_cooperation_concurrency": 20})
                assert saved.json()["max_cooperation_concurrency"] == 20
                invalid = await client.post("/api/system/config/update", json={"max_cooperation_concurrency": 21})
                assert invalid.status_code == 400
                assert invalid.json()["detail"] == "max_cooperation_concurrency 不得大于 20"
                async with sessions.begin() as db:
                    record = await db.scalar(select(ConfigOption).where(ConfigOption.key == "system_options"))
                    assert record.value["max_cooperation_concurrency"] == 20
                    record.value = {**record.value, "max_cooperation_concurrency": 21}
                assert (await client.get("/api/system/config")).json()["max_cooperation_concurrency"] == 20
                async with sessions() as db:
                    record = await db.scalar(select(ConfigOption).where(ConfigOption.key == "system_options"))
                    assert record.value["max_cooperation_concurrency"] == 21
    finally:
        keys = [key async for key in redis.scan_iter(match=namespace + "*")]
        if keys:
            await redis.delete(*keys)
        await dead.aclose()
        await redis.aclose()


async def test_configuration_sync_uses_existing_postgres_lock(monkeypatch):
    """独立连接持有旧部署字面锁值时，同步配置必须等待。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as holder, sessions() as contender:
            await holder.execute(text("SELECT pg_advisory_xact_lock(94721801)"))
            assert OPTION_SYNC_LOCK_KEY == 94721801
            waiting = asyncio.create_task(ensure_options_in_db(contender))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiting), timeout=0.15)
                await holder.rollback()
                await asyncio.wait_for(waiting, timeout=5)
                await contender.commit()
            finally:
                if not waiting.done():
                    waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            async with sessions() as reader:
                assert await reader.scalar(select(ConfigOption).where(ConfigOption.key == "system_options")) is not None


@pytest.mark.parametrize("synchronous", [True, False])
async def test_embedding_http_retry_failure_and_recovery(synchronous, monkeypatch):
    """两类客户端经真实 TCP 共用 payload、重试和响应解析，保留各自异常契约。"""
    monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
    app = FastAPI()
    statuses = [429, 200, 400, 200]
    observed = []

    @app.post("/embeddings")
    async def embeddings(request: Request):
        observed.append(await request.json())
        status = statuses.pop(0)
        return JSONResponse({"data": [{"embedding": [0.25, -0.5]}]}, status_code=status, headers={"Retry-After": "0"})

    async with serve_http(app) as client:
        model = OtherEmbedding(model="fixture", api_key="fixture", base_url=f"{client.base_url}/embeddings")

        async def encode():
            return await asyncio.to_thread(model.encode, ["fixed input"]) if synchronous else await model.aencode(["fixed input"])

        assert await encode() == [[0.25, -0.5]]
        with pytest.raises(ValueError if synchronous else httpx.HTTPStatusError) as failure:
            await encode()
        if synchronous:
            assert isinstance(failure.value.__context__, requests.HTTPError)
        assert await encode() == [[0.25, -0.5]]
    assert observed == [{"model": "fixture", "input": ["fixed input"]}] * 4


async def test_text_adapter_keeps_original_exception_identity():
    """恢复 raise Exception 包装后，此负例按类型与身份失败。"""
    failure = httpx.ConnectError("fixture unavailable")
    adapter = LangChainChatAdapter(SimpleNamespace(ainvoke=AsyncMock(side_effect=failure)), model_name="fixture")
    with pytest.raises(httpx.ConnectError) as raised:
        await adapter.call("fixed input")
    assert raised.value is failure


async def test_configuration_commit_failure_rolls_back(monkeypatch):
    """提交失败必须撤销已 flush 的管理员值，独立事务读取原值。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            await ensure_options_in_db(db)
        async with sessions() as db:
            monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
            with pytest.raises(RuntimeError, match="commit failed"):
                await options.save_configuration(db, "system_options", {"default_model": "uncommitted:model"}, "fixture")
            assert not db.in_transaction()
        async with sessions() as db:
            record = await db.scalar(select(ConfigOption).where(ConfigOption.key == "system_options"))
            assert record.value == {}


async def test_dashboard_http_reads_current_agent_and_session_facts(monkeypatch):
    """报表直接读取当前 Agent 与 Session，统计与明细共享真实 PG 事实。"""
    from yuxi.api.routers.dashboard import dashboard
    from yuxi.modules.agents.models.definitions import Agent
    from yuxi.modules.agents.models.sessions import Session
    from yuxi.modules.identity.models import User
    from yuxi.modules.workspace.models import Project

    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add(User(uid="report-user", username="report-user", password_hash="disabled", role="user"))
            await db.flush()
            db.add(Agent(slug="report-agent", backend_id="test", name="当前名称", share_config={}, created_by="report-user"))
            db.add(
                Project(
                    id="report-project",
                    uid="report-user",
                    selection_status="implicit",
                    directory_mode="managed",
                    workdir_path="projects/report",
                )
            )
            await db.flush()
            db.add(
                Session(
                    thread_id="report-thread", uid="report-user", agent_id="report-agent", project_id="report-project", title="固定会话"
                )
            )
        app = FastAPI()
        app.include_router(dashboard, prefix="/api")

        async def database():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_superadmin_user] = lambda: SimpleNamespace(username="fixture")
        async with serve_http(app) as client:
            listing = await client.get("/api/dashboard/sessions")
            assert listing.status_code == 200, listing.text
            assert listing.json()["items"][0]["agent_name"] == "当前名称"
            detail = await client.get("/api/dashboard/sessions/report-thread")
            assert detail.status_code == 200, detail.text
            assert detail.json()["title"] == "固定会话"
            assert detail.json()["messages"] == []
            analytics = await client.get("/api/dashboard/stats/threads")
            assert analytics.status_code == 200, analytics.text
            assert analytics.json()["summary"]["total_threads"] == 1
            assert analytics.json()["agent_distribution"][0]["agent_name"] == "当前名称"
