"""知识库委托配置在 API/worker 接流量前拒绝不一致部署。"""

import os
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from yuxi.bootstrap import api, worker
from yuxi.modules.identity.security import AuthUtils

pytestmark = pytest.mark.asyncio


class ReachedSchema(RuntimeError):
    """配置校验后到达数据库版本边界的受控终点。"""


@pytest.fixture(autouse=True)
def shared_config(monkeypatch):
    """提供显式部署配置，单个案例只移除目标前提。"""
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "startup-knowledge-jwt-" + "x" * 32)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "startup-api-key-" + "k" * 32)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "startup-knowledge-test")
    monkeypatch.setattr(api, "get_oidc_config", lambda: None)
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.check_sandbox_provider", AsyncMock())


@pytest.mark.parametrize("process", ["api", "worker"])
@pytest.mark.parametrize("missing", ["JWT_SECRET_KEY", "YUXI_INSTANCE_ID"])
async def test_enabled_process_rejects_missing_shared_config_before_random_secrets_or_database(monkeypatch, process, missing):
    """缺少共享配置不能被开发环境自动生成掩盖。"""
    monkeypatch.delenv(missing)
    expected_jwt = os.environ.get("JWT_SECRET_KEY")
    entry = api if process == "api" else worker

    def forbidden():
        """委托校验失败时不能访问后续初始化。"""
        pytest.fail("不得生成安全配置或访问数据库")

    monkeypatch.setattr(AuthUtils, "require_security_secrets", forbidden)
    monkeypatch.setattr(entry.pg_manager, "initialize", forbidden)
    if process == "api":
        app = FastAPI()
        with pytest.raises(api.RequiredStartupComponentError, match="component=knowledge_delegation_config"):
            await api.startup(app)
        assert app.state.startup_complete is False
        assert app.state.startup_components == {
            "knowledge_delegation_config": {"status": "error", "required": True, "code": "ValueError"},
        }
    else:
        with pytest.raises(ValueError, match=missing):
            await worker.startup({})
    assert os.environ.get("JWT_SECRET_KEY") == expected_jwt
    assert missing not in os.environ


@pytest.mark.parametrize("process", ["api", "worker"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_valid_enabled_config_and_disabled_without_instance_reach_schema(monkeypatch, process, enabled):
    """启用使用共享配置，关闭不新增实例标识要求。"""
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", str(enabled).lower())
    if not enabled:
        monkeypatch.delenv("YUXI_INSTANCE_ID")
    entry = api if process == "api" else worker
    initialized = []
    monkeypatch.setattr(entry.pg_manager, "initialize", lambda: initialized.append("database"))

    async def stop_at_schema(_manager):
        """不连接数据库，证明配置允许进入既有版本边界。"""
        raise ReachedSchema

    monkeypatch.setattr(entry, "require_current_schema", stop_at_schema)
    app = FastAPI()
    with pytest.raises(ReachedSchema):
        if process == "api":
            await api.startup(app)
        else:
            await worker.startup({})
    assert initialized == ["database"]
    if process == "api":
        assert ("knowledge_delegation_config" in app.state.startup_components) is enabled
        assert app.state.startup_components["security_secrets"] == {"status": "ok", "required": True}


@pytest.mark.parametrize("failure", ["milvus", "neo4j"])
async def test_worker_backend_unready_cannot_publish_or_claim_pending_jobs(monkeypatch, failure):
    """恢复关闭部署时，任何知识后端未就绪都不能先消费持久任务。"""
    monkeypatch.setattr(worker.pg_manager, "initialize", lambda: None)
    monkeypatch.setattr(worker, "require_current_schema", AsyncMock())
    monkeypatch.setattr("yuxi.modules.system.services.options.invalidate_option_cache", AsyncMock())
    calls = []

    async def initialize():
        """模拟 Milvus 存量知识库连接。"""
        calls.append("milvus")
        if failure == "milvus":
            raise ConnectionError("milvus not ready")

    def connect_graph():
        """模拟真实 Bolt 连接失败。"""
        calls.append("neo4j")
        raise ConnectionError("neo4j not ready")

    monkeypatch.setattr("yuxi.modules.knowledge.runtime.knowledge_base.initialize", initialize)
    monkeypatch.setattr("yuxi.infrastructure.neo4j.get_shared_neo4j_connection", connect_graph)
    recover = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(worker, "_reconcile_agent_runs_once", recover)
    monkeypatch.setattr(worker, "reconcile_and_publish_jobs", publish)
    ctx = {}
    with pytest.raises(ConnectionError, match=failure + " not ready"):
        await worker.startup(ctx)
    recover.assert_not_awaited()
    publish.assert_not_awaited()
    assert calls == (["milvus"] if failure == "milvus" else ["milvus", "neo4j"])
    assert not any(
        key in ctx
        for key in (
            worker._RECONCILIATION_TASK_KEY,
            worker._JOB_RECONCILIATION_TASK_KEY,
            worker._SANDBOX_CLEANUP_TASK_KEY,
        )
    )


@pytest.mark.parametrize("enabled", [True, False])
async def test_worker_publishes_only_after_enabled_backends_are_ready(monkeypatch, enabled):
    """默认启动先连接知识后端；关闭时完全跳过连接并保持普通派发。"""
    import asyncio

    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", str(enabled).lower())
    monkeypatch.setattr(worker.pg_manager, "initialize", lambda: None)
    monkeypatch.setattr(worker, "require_current_schema", AsyncMock())
    monkeypatch.setattr("yuxi.modules.system.services.options.invalidate_option_cache", AsyncMock())
    calls = []

    async def initialize():
        """记录后端初始化完成。"""
        calls.append("milvus")

    def connect_graph():
        """记录 Bolt 已可连接。"""
        calls.append("neo4j")

    async def publish():
        """后端就绪后才进入已有的持久任务补发。"""
        calls.append("publish")
        return []

    monkeypatch.setattr("yuxi.modules.knowledge.runtime.knowledge_base.initialize", initialize)
    monkeypatch.setattr("yuxi.infrastructure.neo4j.get_shared_neo4j_connection", connect_graph)
    monkeypatch.setattr(worker, "reconcile_and_publish_jobs", publish)
    for name in (
        "_reconcile_agent_runs_once",
        "_publish_job_reconciliation_health",
        "_reconcile_sandboxes_forever",
        "_reconcile_agent_run_leases_forever",
        "_reconcile_background_jobs_forever",
        "_sweep_checkpoint_garbage_forever",
    ):
        monkeypatch.setattr(worker, name, AsyncMock())
    ctx = {}
    await worker.startup(ctx)
    await asyncio.gather(*(value for value in ctx.values() if isinstance(value, asyncio.Task)))
    assert calls == (["milvus", "neo4j", "publish"] if enabled else ["publish"])
