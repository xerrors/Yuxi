"""真实 HTTP 验证受限 Key 的 API 面与来源边界。"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_agents_key_cannot_use_product_routes_or_spoof_source(test_client, admin_headers):
    """受限 Key 可读公开目录，但旧产品路由和来源头不能绕过。"""
    payload = {
        "request_id": str(uuid.uuid4()),
        "name": "Public API boundary test",
        "access_level": "agents",
        "app_id": "integration-app",
    }
    created = await test_client.post("/api/user/apikey/", json=payload, headers=admin_headers)
    assert created.status_code == 200, created.text
    key_id = created.json()["api_key"]["id"]
    headers = {
        "Authorization": f"Bearer {created.json()['secret']}",
        "X-App-Id": "forged-source",
    }
    product_thread_id = None
    public_session_id = None
    try:
        directory = await test_client.get("/api/v1/agents", headers=headers)
        assert directory.status_code == 200, directory.text
        assert directory.headers["X-App-Id"] == "integration-app"
        assert isinstance(directory.json()["data"], list)

        for path in ("/api/agent", "/api/user/apikey/", "/api/chat/threads"):
            blocked = await test_client.get(path, headers=headers)
            assert blocked.status_code == 403, (path, blocked.text)
            assert blocked.headers["X-App-Id"] == "integration-app"

        agents = await test_client.get("/api/agent", headers=admin_headers)
        assert agents.status_code == 200, agents.text
        agent = agents.json()["agents"][0]
        agent_slug = agent.get("agent_id") or agent["slug"]
        forged_thread = await test_client.post(
            "/api/chat/thread",
            json={"agent_id": agent_slug, "metadata": {"app_id": "integration-app", "source": "public_api"}},
            headers=admin_headers,
        )
        assert forged_thread.status_code == 400, forged_thread.text

        product_thread = await test_client.post(
            "/api/chat/thread",
            json={"agent_id": agent_slug, "metadata": {"source": "public_api"}},
            headers=admin_headers,
        )
        assert product_thread.status_code == 200, product_thread.text
        product_thread_id = product_thread.json().get("thread_id") or product_thread.json()["id"]
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
        try:
            stored = await conn.fetchrow(
                "UPDATE conversations SET extra_metadata = "
                "jsonb_set(extra_metadata::jsonb, '{app_id}', to_jsonb($1::text))::json "
                "WHERE thread_id = $2 RETURNING app_id, extra_metadata",
                "integration-app",
                product_thread_id,
            )
            assert stored is not None and stored["app_id"] is None
        finally:
            await conn.close()
        isolated = await test_client.get(f"/api/v1/agents/threads/{product_thread_id}", headers=headers)
        assert isolated.status_code == 404, isolated.text

        public_session = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert public_session.status_code == 200, public_session.text
        public_session_id = public_session.json()["id"]
        own_session = await test_client.get(f"/api/v1/agents/sessions/{public_session_id}", headers=headers)
        assert own_session.status_code == 200, own_session.text
        jwt_isolated = await test_client.get(
            f"/api/v1/agents/sessions/{public_session_id}", headers=admin_headers
        )
        assert jwt_isolated.status_code == 404, jwt_isolated.text

        replay = await test_client.post("/api/user/apikey/", json=payload, headers=admin_headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["api_key"]["id"] == key_id

        for changed in ({"app_id": "other-app"}, {"access_level": "full"}):
            conflict = await test_client.post("/api/user/apikey/", json={**payload, **changed}, headers=admin_headers)
            assert conflict.status_code == 409, conflict.text

        widened = await test_client.put(
            f"/api/user/apikey/{key_id}", json={"access_level": "full"}, headers=admin_headers
        )
        assert widened.status_code == 200, widened.text
        restored = await test_client.get("/api/agent", headers=headers)
        assert restored.status_code == 200, restored.text
    finally:
        if public_session_id is not None:
            await test_client.delete(f"/api/chat/thread/{public_session_id}", headers=admin_headers)
        if product_thread_id is not None:
            await test_client.delete(f"/api/chat/thread/{product_thread_id}", headers=admin_headers)
        await test_client.delete(f"/api/user/apikey/{key_id}", headers=admin_headers)


async def test_public_api_accepts_product_jwt_without_end_user_impersonation(test_client, admin_headers):
    """产品 JWT 可使用 Public，但不能声明 APP 的终端用户。"""
    missing = await test_client.post(
        "/api/user/apikey/",
        json={"request_id": str(uuid.uuid4()), "name": "No app", "access_level": "agents"},
        headers=admin_headers,
    )
    assert missing.status_code == 422, missing.text

    jwt = await test_client.get("/api/v1/agents", headers=admin_headers)
    assert jwt.status_code == 200, jwt.text
    assert "X-App-Id" not in jwt.headers

    spoofed = await test_client.get(
        "/api/v1/agents", headers={**admin_headers, "X-End-User-Id": "another-user"}
    )
    assert spoofed.status_code == 403, spoofed.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents/threads",
        "/api/v1/agents/threads/thread-id/requests",
        "/api/v1/agents/sessions",
        "/api/v1/agents/sessions/session-id/events",
    ],
)
async def test_public_message_preflight_allows_idempotency_key(test_client, path):
    """明确允许跨源浏览器提交 Public API 必需的幂等请求头。"""
    origin = os.getenv("YUXI_CORS_ORIGINS", "http://localhost:5173").split(",")[0]
    response = await test_client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key,x-end-user-id",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
    assert "x-end-user-id" in response.headers["access-control-allow-headers"].lower()
