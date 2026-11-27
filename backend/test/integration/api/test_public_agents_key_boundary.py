"""真实 HTTP 验证受限 Key 的 API 面与来源边界。"""

from __future__ import annotations

import os
import uuid
from contextlib import suppress

import asyncpg
import pytest
from test.integration.api.helpers import delete_created_threads
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.runtime_paths import runtime_workspace_path

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize("access_level", ["full", "agents"])
async def test_unbound_key_uses_owners_product_thread_scope(test_client, admin_headers, api_key_factory, access_level):
    """本人 Key 与 JWT 共享会话、Project 和 Workspace，不能伪造终端身份。"""
    created_key = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "Personal product scope", "access_level": access_level})
    assert created_key.response.json()["api_key"]["app_id"] is None
    key_headers = created_key.headers
    thread_id = None
    try:
        created = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": "default-chatbot"},
            headers={**key_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        from_jwt = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=admin_headers)
        assert from_jwt.status_code == 200, from_jwt.text
        assert from_jwt.json()["id"] == thread_id

        me = await test_client.get("/api/auth/me", headers=admin_headers)
        assert me.status_code == 200, me.text
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
        try:
            stored = await conn.fetchrow(
                "SELECT s.uid, s.app_id, p.uid AS project_uid, p.workdir_path, u.user_kind "
                "FROM sessions s JOIN projects p ON p.id = s.project_id "
                "JOIN users u ON u.uid = s.uid WHERE s.thread_id = $1",
                thread_id,
            )
        finally:
            await conn.close()
        assert stored["uid"] == stored["project_uid"] == me.json()["uid"]
        assert stored["app_id"] is None and stored["user_kind"] == "human"
        assert stored["workdir_path"]
        workspace = Workspace(stored["uid"])
        file_path = f"/{stored['workdir_path'].strip('/')}/personal-key.txt"
        workspace.replace_authorized_file(file_path, b"owner workspace content")
        try:
            runtime_path = runtime_workspace_path(file_path)
            for headers in (key_headers, admin_headers):
                artifact = await test_client.get(
                    f"/api/v1/agents/sessions/{thread_id}/artifacts/{runtime_path.lstrip('/')}", headers=headers
                )
                assert artifact.status_code == 200 and artifact.content == b"owner workspace content"
        finally:
            workspace.delete_authorized_path(file_path, root="/")

        updated = await test_client.put(
            f"/api/user/apikey/{created_key.key_id}", json={"access_level": "agents"}, headers=admin_headers
        )
        assert updated.status_code == 200 and updated.json()["api_key"]["app_id"] is None
        accessible = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=key_headers)
        assert accessible.status_code == 200 and accessible.json()["id"] == thread_id

        for identity in ("other-user", me.json()["uid"], ""):
            forged = await test_client.get(
                f"/api/v1/agents/sessions/{thread_id}",
                headers={**key_headers, "X-End-User-Id": identity},
            )
            assert forged.status_code == 403, forged.text
            assert forged.json()["detail"] == "本人身份的 Key 不接受 X-End-User-Id"

        another_key = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "Same owner", "access_level": "agents"})
        shared = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=another_key.headers)
        assert shared.status_code == 200 and shared.json()["id"] == thread_id
        creation_id = str(uuid.uuid4())
        jwt_created = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": "default-chatbot"},
            headers={**admin_headers, "Idempotency-Key": creation_id},
        )
        assert jwt_created.status_code == 201, jwt_created.text
        jwt_thread_id = jwt_created.json()["id"]
        try:
            replay = await test_client.post(
                "/api/v1/agents/sessions",
                json={"agent_id": "default-chatbot"},
                headers={**key_headers, "Idempotency-Key": creation_id},
            )
            assert replay.status_code == 201 and replay.json()["id"] == jwt_thread_id
        finally:
            await delete_created_threads(jwt_thread_id)
    finally:
        await delete_created_threads(thread_id)


async def test_agents_key_cannot_use_product_routes_or_spoof_source(test_client, admin_headers, api_key_factory):
    """受限 Key 可读公开目录，但不能跨 APP 或使用管理接口。"""
    payload = {
        "request_id": str(uuid.uuid4()),
        "name": "Public API boundary test",
        "access_level": "agents",
        "app_id": "integration-app",
    }
    created = await api_key_factory(payload)
    key_id = created.key_id
    headers = {**created.headers, "X-App-Id": "forged-source"}
    product_thread_id = None
    public_thread_id = None
    try:
        directory = await test_client.get("/api/v1/agents", headers=headers)
        assert directory.status_code == 200, directory.text
        assert directory.headers["X-App-Id"] == "integration-app"
        assert isinstance(directory.json()["data"], list)

        for path in ("/api/agent", "/api/user/apikey/"):
            blocked = await test_client.get(path, headers=headers)
            assert blocked.status_code == 403, (path, blocked.text)
            assert blocked.headers["X-App-Id"] == "integration-app"
        old_chat = await test_client.get("/api/chat/threads", headers=headers)
        assert old_chat.status_code == 404, old_chat.text

        agents = await test_client.get("/api/agent", headers=admin_headers)
        assert agents.status_code == 200, agents.text
        agent = agents.json()["agents"][0]
        agent_slug = agent.get("agent_id") or agent["slug"]
        web_create = await test_client.post(
            "/api/agent/threads",
            json={"agent_id": agent_slug, "workdir_naming_text": "Web draft"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert web_create.status_code == 403, web_create.text
        named_public_create = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug, "yuxi": {"workdir_naming_text": "Web draft"}},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert named_public_create.status_code == 422, named_public_create.text
        forged_thread = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug, "app_id": "integration-app"},
            headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert forged_thread.status_code == 422, forged_thread.text

        product_thread = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug},
            headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert product_thread.status_code == 201, product_thread.text
        product_thread_id = product_thread.json()["id"]
        conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
        try:
            stored = await conn.fetchrow(
                "UPDATE sessions SET extra_metadata = jsonb_set(extra_metadata::jsonb, '{app_id}', to_jsonb($1::text))::json "
                "WHERE thread_id = $2 RETURNING app_id, extra_metadata",
                "integration-app",
                product_thread_id,
            )
            assert stored is not None and stored["app_id"] is None
        finally:
            await conn.close()
        isolated = await test_client.get(f"/api/v1/agents/sessions/{product_thread_id}", headers=headers)
        assert isolated.status_code == 404, isolated.text
        isolated_items = await test_client.get(f"/api/v1/agents/sessions/{product_thread_id}/items", headers=headers)
        assert isolated_items.status_code == 404, isolated_items.text

        public_thread = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert public_thread.status_code == 201, public_thread.text
        public_thread_id = public_thread.json()["id"]
        own_thread = await test_client.get(f"/api/v1/agents/sessions/{public_thread_id}", headers=headers)
        assert own_thread.status_code == 200, own_thread.text
        jwt_isolated = await test_client.get(f"/api/v1/agents/sessions/{public_thread_id}", headers=admin_headers)
        assert jwt_isolated.status_code == 404, jwt_isolated.text
        jwt_items = await test_client.get(f"/api/v1/agents/sessions/{public_thread_id}/items", headers=admin_headers)
        assert jwt_items.status_code == 404, jwt_items.text

        replay = await test_client.post("/api/user/apikey/", json=payload, headers=admin_headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["api_key"]["id"] == key_id

        for changed in ({"app_id": "other-app"}, {"access_level": "full"}):
            conflict = await test_client.post("/api/user/apikey/", json={**payload, **changed}, headers=admin_headers)
            assert conflict.status_code == 409, conflict.text

        widened = await test_client.put(f"/api/user/apikey/{key_id}", json={"access_level": "full"}, headers=admin_headers)
        assert widened.status_code == 200, widened.text
        restored = await test_client.get("/api/agent", headers=headers)
        assert restored.status_code == 200, restored.text
    finally:
        if public_thread_id is not None:
            await test_client.post(f"/api/v1/agents/sessions/{public_thread_id}/archive", headers=headers)
        if product_thread_id is not None:
            await test_client.post(f"/api/v1/agents/sessions/{product_thread_id}/archive", headers=admin_headers)
        await delete_created_threads(public_thread_id, product_thread_id)


async def test_public_api_accepts_product_jwt_without_end_user_impersonation(test_client, admin_headers):
    """产品 JWT 可使用 Public，但不能声明 APP 的终端用户。"""
    jwt = await test_client.get("/api/v1/agents", headers=admin_headers)
    assert jwt.status_code == 200, jwt.text
    assert "X-App-Id" not in jwt.headers

    spoofed = await test_client.get("/api/v1/agents", headers={**admin_headers, "X-End-User-Id": "another-user"})
    assert spoofed.status_code == 403, spoofed.text


async def test_key_without_end_user_id_cannot_reach_product_project_files(test_client, admin_headers, api_key_factory):
    """无终端用户 Header 的 APP Key 仍使用独立 UID 与 Workspace。"""
    created_key = await api_key_factory(
        {
            "request_id": str(uuid.uuid4()),
            "name": "Default APP user isolation",
            "access_level": "agents",
            "app_id": f"boundary-{uuid.uuid4()}",
        }
    )
    key_headers = created_key.headers
    product_thread_id = None
    app_thread_id = None
    product_workspace = None
    app_workspace = None
    product_file = None
    app_file = None
    product_saved_file = None
    try:
        agents = await test_client.get("/api/v1/agents", headers=admin_headers)
        assert agents.status_code == 200, agents.text
        agent_slug = agents.json()["data"][0]["id"]
        product_thread = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug},
            headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert product_thread.status_code == 201, product_thread.text
        product_thread_id = product_thread.json()["id"]
        app_thread = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug},
            headers={**key_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert app_thread.status_code == 201, app_thread.text
        app_thread_id = app_thread.json()["id"]

        conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
        try:
            rows = await conn.fetch(
                "SELECT c.thread_id, c.uid, c.project_id, c.app_id, p.workdir_path, "
                "u.user_kind, u.end_user_id FROM sessions c "
                "JOIN projects p ON p.id = c.project_id "
                "JOIN users u ON u.uid = c.uid WHERE c.thread_id = ANY($1::text[])",
                [product_thread_id, app_thread_id],
            )
        finally:
            await conn.close()
        bindings = {row["thread_id"]: row for row in rows}
        product = bindings[product_thread_id]
        app = bindings[app_thread_id]
        assert product["uid"] != app["uid"]
        assert product["app_id"] is None
        assert app["app_id"] == created_key.response.json()["api_key"]["app_id"]
        assert app["user_kind"] == "end_user"
        assert app["end_user_id"] == "__default__"

        product_workspace = Workspace(product["uid"])
        app_workspace = Workspace(app["uid"])
        product_file = f"/{product['workdir_path'].strip('/')}/product-only.txt"
        app_file = f"/{app['workdir_path'].strip('/')}/app-source.txt"
        product_saved_file = f"/{product['workdir_path'].strip('/')}/app-source.txt"
        product_workspace.replace_authorized_file(product_file, b"product private file")
        app_workspace.replace_authorized_file(app_file, b"app file")

        foreign_project = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": agent_slug, "project_id": product["project_id"]},
            headers={**key_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert foreign_project.status_code == 404, foreign_project.text
        product_runtime_path = runtime_workspace_path(product_file)
        foreign_read = await test_client.get(
            f"/api/v1/agents/sessions/{app_thread_id}/artifacts/{product_runtime_path.lstrip('/')}",
            headers=key_headers,
        )
        assert foreign_read.status_code in {403, 404}, foreign_read.text
        foreign_write = await test_client.post(
            f"/api/v1/agents/sessions/{app_thread_id}/artifacts/save",
            json={
                "path": runtime_workspace_path(app_file),
                "destination_path": f"/{product['workdir_path'].strip('/')}",
            },
            headers=key_headers,
        )
        assert foreign_write.status_code in {403, 404}, foreign_write.text
        with pytest.raises(FileNotFoundError):
            product_workspace.stat_authorized_path(product_saved_file, root="/")
        assert product_workspace.read_authorized_file(product_file, 100) == b"product private file"
    finally:
        if product_workspace is not None:
            for path in (product_file, product_saved_file):
                if path:
                    with suppress(FileNotFoundError):
                        product_workspace.delete_authorized_path(path, root="/")
        if app_workspace is not None and app_file:
            with suppress(FileNotFoundError):
                app_workspace.delete_authorized_path(app_file, root="/")
        if app_thread_id is not None:
            await test_client.post(f"/api/v1/agents/sessions/{app_thread_id}/archive", headers=key_headers)
        if product_thread_id is not None:
            await test_client.post(f"/api/v1/agents/sessions/{product_thread_id}/archive", headers=admin_headers)
        await delete_created_threads(app_thread_id, product_thread_id)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents/sessions",
        "/api/v1/agents/sessions/thread-id/events",
    ],
)
async def test_public_message_preflight_allows_idempotency_key(test_client, path):
    """明确允许跨源浏览器提交 Public API 必需的幂等请求头。"""
    origin = (os.getenv("YUXI_CORS_ORIGINS") or "http://localhost:5173").split(",")[0].strip()
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


async def test_personal_agents_key_rejects_cross_user_management_and_audits(test_client, admin_headers, api_key_factory, postgres_sessions):
    """即使 Key 属于超管，也不能管理产品、读取审计或访问其他用户的会话。"""
    from sqlalchemy import select
    from yuxi.modules.identity.models import APIKey

    owner = await test_client.get("/api/auth/me", headers=admin_headers)
    assert owner.status_code == 200 and owner.json()["role"] == "superadmin"
    issued = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "Restricted admin", "access_level": "agents"})
    other_user = await test_client.post(
        "/api/auth/users",
        json={
            "username": f"key_scope_{uuid.uuid4().hex[:8]}",
            "password": "boundaryTest123!",
            "role": "user",
            "department_id": owner.json()["department_id"],
        },
        headers=admin_headers,
    )
    assert other_user.status_code == 200, other_user.text
    thread_id = None
    try:
        other_key = await api_key_factory(
            {"request_id": str(uuid.uuid4()), "name": "Other user", "access_level": "agents", "user_id": other_user.json()["id"]}
        )
        created = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": "default-chatbot"},
            headers={**issued.headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        for suffix in ("", "/items"):
            foreign = await test_client.get(f"/api/v1/agents/sessions/{thread_id}{suffix}", headers=other_key.headers)
            assert foreign.status_code == 404, foreign.text
        denied_write = await test_client.post(
            f"/api/v1/agents/sessions/{thread_id}", json={"yuxi": {"title": "Forged title"}}, headers=other_key.headers
        )
        assert denied_write.status_code == 404, denied_write.text
        for path in ("/api/agent", "/api/user/apikey/", "/api/system/config", "/api/system/logs"):
            denied = await test_client.get(path, headers=issued.headers)
            assert denied.status_code == 403, (path, denied.text)
            assert denied.json()["detail"] == "该 API Key 无权访问此 API 面"
        denied_update = await test_client.put(
            f"/api/user/apikey/{issued.key_id}", json={"name": "Forged name", "access_level": "full"}, headers=issued.headers
        )
        assert denied_update.status_code == 403, denied_update.text
        denied_create = await test_client.post(
            "/api/user/apikey/", json={"request_id": str(uuid.uuid4()), "name": "Forbidden creation"}, headers=issued.headers
        )
        assert denied_create.status_code == 403, denied_create.text
        audits = await test_client.get(f"/api/v1/agents/sessions/{thread_id}/audits", headers=issued.headers)
        assert audits.status_code == 403 and audits.json()["detail"] == "无权读取模型与工具审计"
        jwt_audits = await test_client.get(f"/api/v1/agents/sessions/{thread_id}/audits", headers=admin_headers)
        assert jwt_audits.status_code == 200 and jwt_audits.json()["audits"] == []
        async with postgres_sessions() as db:
            key = await db.get(APIKey, issued.key_id)
            assert key.name == "Restricted admin" and key.access_level == "agents"
            assert await db.scalar(select(APIKey.id).where(APIKey.name == "Forbidden creation")) is None
        unchanged = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=admin_headers)
        assert unchanged.status_code == 200 and unchanged.json()["yuxi"]["title"] != "Forged title"
    finally:
        await delete_created_threads(thread_id)
        await test_client.delete(f"/api/auth/users/{other_user.json()['id']}", headers=admin_headers)
