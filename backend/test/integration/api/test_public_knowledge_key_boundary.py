"""Knowledge API Key 在真实 HTTP 边界仅能访问版本化 external 查询。"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_knowledge_key_is_limited_to_public_knowledge_api(test_client, admin_headers):
    """knowledge Key 可查询 external 接口，不能越界到管理或其他 API。"""
    created = await test_client.post(
        "/api/user/apikey/",
        json={
            "request_id": str(uuid.uuid4()),
            "name": "Knowledge API boundary test",
            "access_level": "knowledge",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["api_key"]["id"]
    headers = {"Authorization": f"Bearer {created.json()['secret']}"}
    try:
        external = await test_client.get("/api/v1/knowledge/databases/external", headers=headers)
        assert external.status_code == 200, external.text
        assert "databases" in external.json()

        for path in (
            "/api/knowledge/databases",
            "/api/knowledge/databases/external",
            "/api/v1/agents",
            "/api/user/apikey/",
            "/api/graph/list",
            "/api/evaluation/databases/unused/datasets",
        ):
            blocked = await test_client.get(path, headers=headers)
            assert blocked.status_code == 403, (path, blocked.text)
    finally:
        await test_client.delete(f"/api/user/apikey/{key_id}", headers=admin_headers)


async def test_agents_key_cannot_access_public_knowledge_api(test_client, admin_headers):
    """Agents 与 Knowledge 两种受限 Key 的 API 面互不包含。"""
    created = await test_client.post(
        "/api/user/apikey/",
        json={
            "request_id": str(uuid.uuid4()),
            "name": "Agents API boundary test",
            "access_level": "agents",
            "app_id": "knowledge-boundary-test",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["api_key"]["id"]
    try:
        response = await test_client.get(
            "/api/v1/knowledge/databases/external",
            headers={"Authorization": f"Bearer {created.json()['secret']}"},
        )
        assert response.status_code == 403, response.text
        tool_response = await test_client.get(
            "/api/v1/knowledge/tools/list_kbs",
            headers={"Authorization": f"Bearer {created.json()['secret']}"},
        )
        assert tool_response.status_code == 403, tool_response.text
    finally:
        await test_client.delete(f"/api/user/apikey/{key_id}", headers=admin_headers)


async def test_public_knowledge_does_not_expose_management_routes(test_client, admin_headers):
    """版本化知识域仅注册 external 查询，不迁入管理路由。"""
    response = await test_client.get("/api/v1/knowledge/databases", headers=admin_headers)
    assert response.status_code == 404, response.text


async def test_legacy_external_list_matches_public_v1(test_client, admin_headers):
    """迁移期旧 external 路由仍可访问并返回相同业务结果。"""
    public = await test_client.get("/api/v1/knowledge/databases/external", headers=admin_headers)
    legacy = await test_client.get("/api/knowledge/databases/external", headers=admin_headers)
    assert public.status_code == legacy.status_code == 200
    assert public.json() == legacy.json()


async def test_knowledge_key_reaches_all_external_operations(test_client, admin_headers, knowledge_database):
    """knowledge Key 能调用五个 external 操作，资源内错误保留原语义。"""
    created = await test_client.post(
        "/api/user/apikey/",
        json={
            "request_id": str(uuid.uuid4()),
            "name": "Knowledge external operations test",
            "access_level": "knowledge",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["api_key"]["id"]
    key_headers = {"Authorization": f"Bearer {created.json()['secret']}"}
    kb_id = knowledge_database["kb_id"]
    try:
        files = await test_client.get(f"/api/v1/knowledge/databases/external/{kb_id}/files", headers=key_headers)
        assert files.status_code == 200, files.text

        retrieved = await test_client.post(
            f"/api/v1/knowledge/databases/external/{kb_id}/retrieve",
            json={"query": "hello"},
            headers=key_headers,
        )
        assert retrieved.status_code == 200, retrieved.text
        assert retrieved.json()["kb_id"] == kb_id

        opened = await test_client.get(
            f"/api/v1/knowledge/databases/external/{kb_id}/files/missing/open",
            headers=key_headers,
        )
        assert opened.status_code == 400, opened.text

        found = await test_client.post(
            f"/api/v1/knowledge/databases/external/{kb_id}/files/missing/find",
            json={"patterns": []},
            headers=key_headers,
        )
        assert found.status_code == 400, found.text
    finally:
        await test_client.delete(f"/api/user/apikey/{key_id}", headers=admin_headers)
