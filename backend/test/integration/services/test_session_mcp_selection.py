"""真实 PostgreSQL 与 HTTP 证明会话连接器的接收、冻结与合批。"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from test.integration.services.agent_input_fixtures import SCOPE, start_input_run
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.extensions.mcp import mcp
from yuxi.api.routers.public_v1.agents.auth import require_public_context
from yuxi.api.routers.public_v1.agents.events import router
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.run_lifecycle import settle_run
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.identity.models import User
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest.fixture
def app(sessions):
    """使用真实路由与数据库，只注入测试的可信调用身份。"""
    application = FastAPI()
    application.add_exception_handler(DomainError, domain_error_handler)
    application.include_router(router, prefix="/api/v1/agents")

    async def database():
        """每次 HTTP 请求独占事务，失败时回滚。"""
        async with sessions() as db:
            yield db

    application.dependency_overrides[get_db] = database
    application.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
    return application


def message_body(*, mcps=None, mode="follow_up"):
    """生成提交级选择，不把资源标识塞入模型消息。"""
    options = {"mode": mode}
    if mcps is not None:
        options["mcps"] = mcps
    return {
        "events": [
            {
                "type": "agent.session.input.message",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                "yuxi": options,
            }
        ]
    }


async def test_connector_directory_projects_runtime_availability_with_knowledge_disabled(sessions, app, monkeypatch):
    """管理目录保留内置身份；提及投影按当前部署、开关与本人连接关闭。"""
    app.include_router(mcp, prefix="/api")

    async def current_user():
        """读取当前真实用户，复用目录的认证边界。"""
        async with sessions() as db:
            return await db.scalar(select(User).where(User.uid == SCOPE.uid))

    app.dependency_overrides[get_required_user] = current_user
    async with sessions() as db:
        for slug, mode, enabled in (("knowledge", "delegation", 1), ("ready", "none", 1), ("off", "none", 0), ("personal", "bearer", 1)):
            db.add(
                MCPServer(
                    slug=slug,
                    name=slug,
                    transport="streamable_http",
                    url="https://example.com/mcp",
                    auth_mode=mode,
                    enabled=enabled,
                    headers={},
                    created_by=SCOPE.uid,
                    updated_by=SCOPE.uid,
                )
            )
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for role in ("user", "superadmin"):
            async with sessions() as db:
                user = await db.scalar(select(User).where(User.uid == SCOPE.uid))
                user.role = role
                await db.commit()
            for enabled in (False, True):
                monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", str(enabled).lower())
                response = await client.get("/api/system/mcp-servers")
                assert response.status_code == 200, response.text
                catalog = {item["slug"]: item for item in response.json()["data"]}
                assert catalog["knowledge"]["enabled"] is True
                assert catalog["knowledge"]["available"] is enabled
                assert catalog["ready"]["available"] is True
                assert catalog["personal"]["available"] is False
                assert not catalog.get("off", {}).get("available", False)


async def test_http_mcp_selection_is_atomic_idempotent_and_thread_scoped(sessions, app):
    """追加与重试保存一次；已有 Input、Agent 和其他会话不变。"""
    async with sessions() as db:
        session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        session.config_snapshot = {**session.config_snapshot, "mcps": ["base"]}
        await db.commit()
    url = "/api/v1/agents/sessions/input-thread/events"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        original = await client.post(url, headers={"Idempotency-Key": "original"}, json=message_body())
        added = await client.post(url, headers={"Idempotency-Key": "added"}, json=message_body(mcps=["extra", "base", "extra"]))
        assert original.status_code == added.status_code == 202
        retry = await client.post(url, headers={"Idempotency-Key": "added"}, json=message_body(mcps=["extra", "base"]))
        assert retry.status_code == 202 and retry.json() == added.json()
        conflict = await client.post(url, headers={"Idempotency-Key": "added"}, json=message_body(mcps=["forged"]))
        assert conflict.status_code == 409
        app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=ActorScope(uid=SCOPE.uid, app_id="other-app"))
        assert (await client.post(url, headers={"Idempotency-Key": "cross-app"}, json=message_body(mcps=["forged"]))).status_code == 404
        app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=ActorScope(uid="other-user", app_id=None))
        assert (await client.post(url, headers={"Idempotency-Key": "cross-user"}, json=message_body(mcps=["forged"]))).status_code == 404
        app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
        cancelled = await client.post(
            url,
            headers={"Idempotency-Key": "cancel-added"},
            json={
                "events": [
                    {
                        "type": "yuxi.session.input.cancel_input",
                        "input_id": added.json()["input_id"],
                    }
                ]
            },
        )
        assert cancelled.status_code == 202
        following = await client.post(url, headers={"Idempotency-Key": "following"}, json=message_body(mcps=[]))
        assert following.status_code == 202
    async with sessions() as db:
        assert (await SessionRepository(db).get_session_by_thread_id("input-thread")).config_snapshot["mcps"] == ["base", "extra"]
        first = await db.get(AgentInput, original.json()["input_id"])
        assert first.input_payload["context_snapshot"]["mcps"] == ["base"]
        for response in (added, following):
            item = await db.get(AgentInput, response.json()["input_id"])
            assert item.input_payload["context_snapshot"]["mcps"] == ["base", "extra"]
        assert (await db.get(AgentInput, added.json()["input_id"])).status == "cancelled"
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 3
        assert (await db.scalar(select(Agent).where(Agent.slug == "main"))).config_json == {"context": {"model": "test:chat"}}


async def test_steer_batch_merges_only_consumed_selections_and_keeps_run_config(sessions, app):
    """运行中连续追加按安全边界合入，较晚 follow-up 不污染本批。"""
    current = await start_input_run(sessions)
    url = "/api/v1/agents/sessions/input-thread/events"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        accepted = []
        for slug, mode in (("first", "steer"), ("second", "steer"), ("later", "follow_up")):
            response = await client.post(url, headers={"Idempotency-Key": slug}, json=message_body(mcps=[slug], mode=mode))
            assert response.status_code == 202, response.text
            accepted.append(response.json()["input_id"])
        rejected_body = message_body(mcps=["rejected"], mode="steer")
        rejected_body["events"][0]["yuxi"]["model"] = "test:chat"
        assert (await client.post(url, headers={"Idempotency-Key": "rejected"}, json=rejected_body)).status_code == 422
    async with sessions() as db:
        await SessionRepository(db).lock_session_by_thread_id("input-thread")
        run = await db.get(AgentRun, current.id)
        settled = await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
        assert settled.status == "yielded"
        await db.commit()
    async with sessions() as db:
        successor = await db.get(AgentRun, settled.next_dispatch.run_id)
        context = successor.input_payload["context_snapshot"]
        assert context["mcps"] == ["first", "second"]
        assert context["model"] == current.input_payload["context_snapshot"]["model"]
        assert context["tool_approval_mode"] == current.input_payload["context_snapshot"]["tool_approval_mode"]
        assert "mcps" not in (await db.get(AgentRun, current.id)).input_payload["context_snapshot"]
        for identity in accepted[:2]:
            assert (await db.get(AgentInput, identity)).consumed_run_id == successor.id
        assert (await db.get(AgentInput, accepted[2])).status == "pending"
        assert (await db.scalar(select(Session))).config_snapshot["mcps"] == ["first", "second", "later"]
