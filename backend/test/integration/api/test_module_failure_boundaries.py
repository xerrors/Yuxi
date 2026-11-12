"""通过真实 HTTP、PostgreSQL 和本地 Dify 协议验证模块失败边界。"""

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.extensions.mcp.service import delete_mcp_server

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    "scope",
    [
        {"read_scope": {"access_level": []}},
        {"manage_scope": {"access_level": False}},
        {"read_scope": {"access_level": "department", "department_ids": "1"}},
        {"manage_scope": {"access_level": "user", "user_uids": "outsider"}},
    ],
)
async def test_corrupt_agent_share_config_is_hidden_and_rejected(test_client, admin_headers, standard_user, scope):
    """坏记录对普通用户隐藏，superadmin 仍可查看原值并修复配置。"""
    slug = "pytest-invalid-share-" + uuid4().hex[:12]
    path = f"/api/agent/{slug}"
    config = {"version": 2, **scope}
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    created = False
    try:
        payload = {"name": "Pytest invalid sharing", "slug": slug, "visibility": "shared", "share_config": config}
        response = await test_client.post("/api/agent", headers=admin_headers, json=payload)
        assert response.status_code == 422, response.text
        assert await conn.fetchval("SELECT id FROM agents WHERE slug = $1", slug) is None

        valid = {"version": 2, "read_scope": {"access_level": "global"}}
        response = await test_client.post("/api/agent", headers=admin_headers, json={**payload, "share_config": valid})
        assert response.status_code == 200, response.text
        created = True
        response = await test_client.get(path, headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["effective_permission"] == "read"

        await conn.execute("UPDATE agents SET share_config = $1::jsonb WHERE slug = $2", json.dumps(config), slug)
        assert json.loads(await conn.fetchval("SELECT share_config FROM agents WHERE slug = $1", slug)) == config
        response = await test_client.get("/api/agent", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert response.json()["agents"]
        assert slug not in {item["slug"] for item in response.json()["agents"]}
        response = await test_client.get(path, headers=standard_user["headers"])
        assert response.status_code == 404, response.text
        response = await test_client.put(path, headers=standard_user["headers"], json={"name": "Unauthorized"})
        assert response.status_code == 404, response.text
        assert await conn.fetchval("SELECT name FROM agents WHERE slug = $1", slug) == payload["name"]

        response = await test_client.get("/api/agent", headers=admin_headers)
        assert response.status_code == 200, response.text
        damaged = next(item for item in response.json()["agents"] if item["slug"] == slug)
        assert damaged["share_config"] == config
        assert damaged["share_config_invalid"] is True
        assert damaged["effective_permission"] == "manage"
        response = await test_client.get(path, headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["share_config"] == config
        assert response.json()["agent"]["share_config_invalid"] is True
        response = await test_client.put(path, headers=admin_headers, json={"share_config": valid})
        assert response.status_code == 200, response.text
        persisted = json.loads(await conn.fetchval("SELECT share_config FROM agents WHERE slug = $1", slug))
        assert persisted["read_scope"]["access_level"] == "global"
        assert response.json()["agent"]["share_config_invalid"] is False
        response = await test_client.get(path, headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["effective_permission"] == "read"
    finally:
        try:
            if created:
                response = await test_client.delete(path, headers=admin_headers)
                assert response.status_code == 200, response.text
                assert await conn.fetchval("SELECT id FROM agents WHERE slug = $1", slug) is None
        finally:
            await conn.close()


async def test_builtin_mcp_delete_preserves_postgres_row(test_client, admin_headers):
    """HTTP 和直接服务调用均拒绝删除内置 MCP，持久行保持不变。"""
    slug = "deepwiki-official"
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        before = await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug)
        assert before is not None
        response = await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        assert response.status_code == 403, response.text
        async with async_sessionmaker(engine)() as db:
            with pytest.raises(PermissionError, match="内置连接器"):
                await delete_mcp_server(db, slug)
        assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) == before
    finally:
        await conn.close()
        await engine.dispose()


@pytest.mark.parametrize("remote_fails", [True, False])
async def test_dify_http_failure_export_and_result_boundaries(test_client, admin_headers, remote_fails):
    """真实 Dify HTTP 故障显式失败，超限结果被截断，未实现导出返回 501。"""
    requests = []

    class DifyHandler(BaseHTTPRequestHandler):
        """提供确定性 Dify 检索响应，独立记录实际请求参数。"""

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            assert self.path == "/v1/datasets/fixture-dataset/retrieve"
            data = {"records": [{"segment": {"id": str(index), "content": f"chunk-{index}"}, "score": 0.8} for index in range(201)]}
            body = json.dumps({"error": "unavailable"} if remote_fails else data).encode()
            self.send_response(503 if remote_fails else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DifyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    kb_id = None
    try:
        response = await test_client.post(
            "/api/knowledge/knowledge-bases",
            headers=admin_headers,
            json={
                "name": "pytest_dify_boundary_" + uuid4().hex[:12],
                "description": "Pytest Dify failure boundaries",
                "kb_type": "dify",
                "additional_params": {
                    "dify_api_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "dify_token": "fixture-token",
                    "dify_dataset_id": "fixture-dataset",
                },
            },
        )
        assert response.status_code == 200, response.text
        kb_id = response.json()["kb_id"]
        path = f"/api/knowledge/knowledge-bases/{kb_id}"
        response = await test_client.get(path + "/export", headers=admin_headers)
        assert response.status_code == 501, response.text
        assert response.json()["detail"] == "知识库导出暂不支持"

        rejected = await test_client.post(
            path + "/query-test",
            headers=admin_headers,
            json={"query": "fixture query", "meta": {"final_top_k": 1000000}},
        )
        assert rejected.status_code == 422, rejected.text
        assert "超出允许范围" in rejected.json()["detail"]
        assert requests == []

        payload = {"query": "fixture query", "meta": {"final_top_k": 100}}
        response = await test_client.post(path + "/query-test", headers=admin_headers, json=payload)
        if remote_fails:
            assert response.status_code == 500, response.text
            assert response.json()["detail"] == "知识库检索失败"
            assert len(requests) == 2
            assert requests[-1] == {"query": "fixture query"}
            response = await test_client.post(path + "/query", headers=admin_headers, json=payload)
            assert response.status_code == 500, response.text
            assert response.json()["detail"] == "知识库查询失败，请稍后重试"
            assert "result" not in response.json()
        else:
            assert response.status_code == 200, response.text
            results = response.json()
            assert len(results) == 100
            assert [item["content"] for item in results] == [f"chunk-{index}" for index in range(100)]
        assert requests[0]["retrieval_model"]["top_k"] == 100
    finally:
        try:
            if kb_id:
                response = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
                assert response.status_code == 200, response.text
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.parametrize("damaged", [{"version": 2, "read_scope": {"access_level": False}}, None])
async def test_corrupt_skill_share_config_remains_repairable(test_client, admin_headers, standard_user, damaged):
    """Skill 卡片保留坏配置供管理者修复，普通用户始终按后端权限隔离。"""
    slug = "pytest-invalid-skill-share-" + uuid4().hex[:10]
    content = f"---\nname: {slug}\nslug: {slug}\ndescription: Permission repair fixture\n---\n# Fixture\n"
    response = await test_client.post(
        "/api/skills/import/prepare",
        headers=admin_headers,
        files={"file": ("SKILL.md", content.encode(), "text/markdown")},
    )
    assert response.status_code == 200, response.text
    draft_id = response.json()["data"]["draft_id"]
    response = await test_client.post(
        f"/api/skills/install-drafts/{draft_id}/confirm",
        headers=admin_headers,
        json={"slugs": [slug], "share_config": None},
    )
    assert response.status_code == 200, response.text
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        for default_scope in ({}, {"access_level": None}, {"access_level": ""}):
            valid_default = {"version": 2, "read_scope": default_scope}
            await conn.execute("UPDATE skills SET share_config = $1::jsonb WHERE slug = $2", json.dumps(valid_default), slug)
            for endpoint in ("/api/skills", "/api/system/skills"):
                response = await test_client.get(endpoint, headers=admin_headers)
                assert response.status_code == 200, response.text
                card = next(item for item in response.json()["data"] if item["slug"] == slug)
                assert card["share_config"]["read_scope"]["access_level"] == "global"
                assert card["share_config_invalid"] is False
        await conn.execute("UPDATE skills SET share_config = $1::jsonb WHERE slug = $2", json.dumps(damaged), slug)
        for headers in (standard_user["headers"], admin_headers):
            response = await test_client.get("/api/skills", headers=headers)
            assert response.status_code == 200, response.text
            cards = {item["slug"]: item for item in response.json()["data"]}
            if headers == admin_headers:
                assert cards[slug]["share_config_invalid"] is True
                assert cards[slug]["share_config"] == damaged
                assert cards[slug]["can_manage"] is True
            else:
                assert slug not in cards
        response = await test_client.get("/api/system/skills", headers=admin_headers)
        assert response.status_code == 200, response.text
        card = next(item for item in response.json()["data"] if item["slug"] == slug)
        assert card["share_config_invalid"] is True
        assert card["share_config"] == damaged
        response = await test_client.put(
            f"/api/system/skills/{slug}/share-config",
            headers=admin_headers,
            json={"share_config": {"version": 2, "read_scope": {"access_level": "global"}}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["share_config_invalid"] is False
        persisted = json.loads(await conn.fetchval("SELECT share_config FROM skills WHERE slug = $1", slug))
        assert persisted["read_scope"]["access_level"] == "global"
        response = await test_client.get("/api/skills", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert slug in {item["slug"] for item in response.json()["data"]}
    finally:
        try:
            response = await test_client.delete(f"/api/system/skills/{slug}", headers=admin_headers)
            assert response.status_code == 200, response.text
            assert await conn.fetchval("SELECT id FROM skills WHERE slug = $1", slug) is None
        finally:
            await conn.close()


@pytest.mark.parametrize("null_kind", ["sql", "json"])
async def test_null_knowledge_permission_does_not_become_global(test_client, admin_headers, standard_user, null_kind):
    """创建默认共享合法，持久 NULL 不得在列表和工具读取中扩大成全局权限。"""
    response = await test_client.post(
        "/api/knowledge/knowledge-bases",
        headers=admin_headers,
        json={
            "name": "pytest_null_kb_share_" + uuid4().hex[:12],
            "description": "Pytest null knowledge sharing",
            "kb_type": "dify",
            "share_config": None,
            "additional_params": {
                "dify_api_url": "http://127.0.0.1:1/v1",
                "dify_token": "fixture-token",
                "dify_dataset_id": "fixture-dataset",
            },
        },
    )
    assert response.status_code == 200, response.text
    kb_id = response.json()["kb_id"]
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        await conn.execute("UPDATE users SET role = 'admin' WHERE uid = $1", standard_user["user"]["uid"])
        persisted = json.loads(await conn.fetchval("SELECT share_config FROM knowledge_bases WHERE kb_id = $1", kb_id))
        assert persisted["read_scope"]["access_level"] == "global"
        response = await test_client.get("/api/knowledge/knowledge-bases/accessible", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert kb_id in {item["kb_id"] for item in response.json()["knowledge_bases"]}
        response = await test_client.get(f"/api/knowledge/knowledge-bases/{kb_id}", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        if null_kind == "sql":
            await conn.execute("UPDATE knowledge_bases SET share_config = NULL WHERE kb_id = $1", kb_id)
            assert await conn.fetchval("SELECT share_config IS NULL FROM knowledge_bases WHERE kb_id = $1", kb_id)
        else:
            await conn.execute("UPDATE knowledge_bases SET share_config = 'null'::jsonb WHERE kb_id = $1", kb_id)
            assert await conn.fetchval("SELECT share_config FROM knowledge_bases WHERE kb_id = $1", kb_id) == "null"
        response = await test_client.get("/api/knowledge/knowledge-bases/accessible", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        assert kb_id not in {item["kb_id"] for item in response.json()["knowledge_bases"]}
        response = await test_client.get(f"/api/knowledge/knowledge-bases/{kb_id}", headers=standard_user["headers"])
        assert response.status_code == 403, response.text
        from yuxi.infrastructure.postgres.manager import pg_manager
        from yuxi.modules.knowledge.runtime import knowledge_base

        try:
            assert await knowledge_base.get_accessible_knowledge_base_info_by_uid(standard_user["user"]["uid"], kb_id) is None
        finally:
            # 参数化测试使用独立事件循环，不能把本次连接池传到下一项。
            await pg_manager.async_engine.dispose()
    finally:
        try:
            response = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
            assert response.status_code == 200, response.text
            row = await conn.fetchrow("SELECT deleted_at FROM knowledge_bases WHERE kb_id = $1", kb_id)
            assert row is None or row["deleted_at"] is not None
            async with asyncio.timeout(120):
                while await conn.fetchval("SELECT kb_id FROM knowledge_bases WHERE kb_id = $1", kb_id) is not None:
                    await asyncio.sleep(0.2)
        finally:
            await conn.close()
