"""Public 创建入口在真实 HTTP 与 PostgreSQL 中保存会话连接器选择。"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from test.integration.api.helpers import delete_created_threads
from test.support.public_api import delete_agent
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.sessions import Session

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("configured", [["base"], "all"])
async def test_public_creation_merges_mcp_selection_without_changing_agent_or_other_session(
    test_client, admin_headers, standard_user, postgres_sessions, api_key_factory, configured
):
    """个人 Key 创建与 JWT 回读同一会话；空列表不覆盖原选择。"""
    slug = "pytest-session-mcps-" + uuid4().hex[:10]
    headers = standard_user["headers"]
    base_slug = slug + "-base"
    connector = await test_client.post(
        "/api/system/mcp-servers",
        headers=admin_headers,
        json={
            "slug": base_slug,
            "name": "配置内连接器",
            "url": "https://example.com/mcp",
            "transport": "streamable_http",
            "auth_mode": "none",
        },
    )
    assert connector.status_code == 200, connector.text
    configured = "all" if configured == "all" else [base_slug]
    key = await api_key_factory({"request_id": str(uuid4()), "name": "Session connectors", "access_level": "agents"}, owner_headers=headers)
    created = await test_client.post(
        "/api/agent",
        headers=headers,
        json={
            "slug": slug,
            "name": "会话连接器验收",
            "backend_id": "ChatbotAgent",
            "config_json": {"context": {"mcps": configured}},
        },
    )
    assert created.status_code == 200, created.text
    session_ids = []
    try:
        request_id = str(uuid4())
        body = {"agent_id": slug, "yuxi": {"mcps": ["extra", base_slug, "extra"]}}
        create_headers = {**key.headers, "Idempotency-Key": request_id}
        response = await test_client.post("/api/v1/agents/sessions", headers=create_headers, json=body)
        assert response.status_code == 201, response.text
        identity = response.json()["id"]
        session_ids.append(identity)
        assert (await test_client.get(f"/api/v1/agents/sessions/{identity}", headers=headers)).status_code == 200
        replay = await test_client.post("/api/v1/agents/sessions", headers=create_headers, json=body)
        assert replay.status_code == 201 and replay.json()["id"] == identity
        body["yuxi"]["mcps"] = ["other"]
        assert (await test_client.post("/api/v1/agents/sessions", headers=create_headers, json=body)).status_code == 409
        other = await test_client.post(
            "/api/v1/agents/sessions",
            headers={**key.headers, "Idempotency-Key": str(uuid4())},
            json={"agent_id": slug, "yuxi": {"mcps": []}},
        )
        assert other.status_code == 201, other.text
        session_ids.append(other.json()["id"])
        async with postgres_sessions() as db:
            session = await db.scalar(select(Session).where(Session.thread_id == identity))
            assert session.config_snapshot["mcps"] == ("all" if configured == "all" else [base_slug, "extra"])
            other_session = await db.scalar(select(Session).where(Session.thread_id == session_ids[1]))
            assert other_session.config_snapshot["mcps"] == configured
            agent = await db.scalar(select(Agent).where(Agent.slug == slug))
            assert agent.config_json["context"]["mcps"] == configured
    finally:
        await delete_created_threads(*session_ids)
        await delete_agent(test_client, headers, slug)
        assert (await test_client.delete("/api/system/mcp-servers/" + base_slug, headers=admin_headers)).status_code in {200, 404}
