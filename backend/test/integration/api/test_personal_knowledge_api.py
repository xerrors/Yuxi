"""通过真实 HTTP、PostgreSQL 与对象存储验证个人知识库隔离。"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_personal_knowledge_two_users_and_files_are_isolated(test_client) -> None:
    """辅导人员可维护私库，其他账号和伪造文件来源均不能越权。"""
    suffix = uuid.uuid4().hex[:10]
    department_name = f"pytest_personal_kb_{suffix}"
    connection = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    actors = {}
    databases = []
    try:
        department_id = await connection.fetchval(
            "INSERT INTO departments (name, description) VALUES ($1, 'personal knowledge test') RETURNING id",
            department_name,
        )
        for actor, role, roles in (
            ("a", "user", ["counselor"]),
            ("b", "user", ["counselor"]),
            ("technical", "superadmin", ["technical_admin"]),
            ("no_capability", "user", []),
        ):
            uid = f"personal_{actor}_{suffix}"
            user_id = await connection.fetchval(
                """
                INSERT INTO users
                    (username, uid, password_hash, role, business_roles, department_id,
                     login_failed_count, is_deleted, created_at)
                VALUES ($1, $1, 'test', $2, $3::jsonb, $4, 0, 0, NOW()) RETURNING id
                """,
                uid,
                role,
                json.dumps(roles),
                department_id,
            )
            actors[actor] = {
                "id": user_id,
                "uid": uid,
                "headers": {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user_id)})}"},
            }

        payload = {
            "database_name": f"pytest_personal_denied_{suffix}",
            "description": "private knowledge integration test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
        }
        denied = await test_client.post(
            "/api/knowledge/databases", json=payload, headers=actors["no_capability"]["headers"]
        )
        assert denied.status_code == 403, denied.text
        for extra in (
            {"share_config": {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None}},
            {"kb_type": "dify"},
        ):
            denied = await test_client.post(
                "/api/knowledge/databases", json={**payload, **extra}, headers=actors["a"]["headers"]
            )
            assert denied.status_code == 403, denied.text
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM knowledge_bases WHERE name = $1", payload["database_name"]
        ) == 0
        for actor in ("a", "b"):
            response = await test_client.post(
                "/api/knowledge/databases",
                json={**payload, "database_name": f"pytest_personal_{actor}_{suffix}"},
                headers=actors[actor]["headers"],
            )
            assert response.status_code == 200, response.text
            kb_id = response.json()["kb_id"]
            actors[actor]["kb_id"] = kb_id
            databases.append((kb_id, actors[actor]["headers"]))
            record = await connection.fetchrow(
                "SELECT created_by, share_config FROM knowledge_bases WHERE kb_id = $1", kb_id
            )
            assert record["created_by"] == actors[actor]["uid"]
            assert json.loads(record["share_config"]) == {"version": 2, "read_scope": None, "manage_scope": None}

        a_id, b_id = actors["a"]["kb_id"], actors["b"]["kb_id"]
        a_headers, b_headers = actors["a"]["headers"], actors["b"]["headers"]
        update = {"name": f"pytest_personal_updated_{suffix}", "description": "updated personal notes"}
        response = await test_client.put(f"/api/knowledge/databases/{a_id}", json=update, headers=a_headers)
        assert response.status_code == 200, response.text
        assert (
            await connection.fetchval("SELECT description FROM knowledge_bases WHERE kb_id = $1", a_id)
            == update["description"]
        )
        content = f"private counselor A notes {suffix}".encode()
        uploaded = await test_client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": a_id},
            files={"file": ("private.txt", content, "text/plain")},
            headers=a_headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        source = uploaded.json()["file_path"]
        content_hash = uploaded.json()["content_hash"]
        document_payload = {"items": [source], "params": {"content_hashes": {source: content_hash}}}
        added = await test_client.post(
            f"/api/knowledge/databases/{a_id}/documents/add", json=document_payload, headers=a_headers
        )
        assert added.status_code == 200 and added.json()["status"] == "success", added.text
        file_id = added.json()["items"][0]["file_id"]
        persisted_file = await connection.fetchrow(
            "SELECT kb_id, created_by, path, status FROM knowledge_files WHERE file_id = $1", file_id
        )
        assert dict(persisted_file) == {
            "kb_id": a_id,
            "created_by": actors["a"]["uid"],
            "path": source,
            "status": "uploaded",
        }
        downloaded = await test_client.get(
            f"/api/knowledge/databases/{a_id}/documents/{file_id}/download", headers=a_headers
        )
        assert downloaded.status_code == 200 and downloaded.content == content

        swapped_base = f"/api/knowledge/databases/{b_id}/documents/{file_id}"
        swapped_basic = await test_client.get(f"{swapped_base}/basic", headers=b_headers)
        assert swapped_basic.status_code == 200 and swapped_basic.json() == {
            "message": "Failed to get file basic info", "status": "failed"
        }, swapped_basic.text
        swapped_download = await test_client.get(f"{swapped_base}/download", headers=b_headers)
        assert swapped_download.status_code == 500, swapped_download.text
        assert "not found" in swapped_download.json()["detail"]
        swapped_delete = await test_client.delete(swapped_base, headers=b_headers)
        assert swapped_delete.status_code == 400, swapped_delete.text
        assert "not found" in swapped_delete.json()["detail"]
        assert await connection.fetchval("SELECT kb_id FROM knowledge_files WHERE file_id = $1", file_id) == a_id
        downloaded = await test_client.get(
            f"/api/knowledge/databases/{a_id}/documents/{file_id}/download", headers=a_headers
        )
        assert downloaded.status_code == 200 and downloaded.content == content

        for actor in ("a", "b", "technical"):
            listed = await test_client.get("/api/knowledge/databases", headers=actors[actor]["headers"])
            assert listed.status_code == 200, listed.text
            visible = {row["kb_id"] for row in listed.json()["databases"]}
            assert (a_id in visible) == (actor == "a")
            assert (b_id in visible) == (actor == "b")

        forbidden_reads = [
            f"/api/knowledge/databases/{a_id}",
            f"/api/knowledge/databases/{a_id}/documents/{file_id}/basic",
            f"/api/knowledge/databases/{a_id}/documents/{file_id}/download",
            f"/api/graph/subgraph?kb_id={a_id}",
            f"/api/workspace/knowledge/tree?kb_id={a_id}",
            f"/api/workspace/knowledge/file?kb_id={a_id}&file_id={file_id}",
            f"/api/workspace/knowledge/download?kb_id={a_id}&file_id={file_id}",
        ]
        for actor in ("b", "technical"):
            for endpoint in forbidden_reads:
                response = await test_client.get(endpoint, headers=actors[actor]["headers"])
                assert response.status_code == 403, f"{actor} {endpoint}: {response.status_code} {response.text}"
        response = await test_client.put(f"/api/knowledge/databases/{a_id}", json=update, headers=b_headers)
        assert response.status_code == 403, response.text
        response = await test_client.post(
            f"/api/knowledge/databases/{a_id}/query", json={"query": "notes", "meta": {}}, headers=b_headers
        )
        assert response.status_code == 403, response.text
        response = await test_client.delete(f"/api/knowledge/databases/{a_id}", headers=b_headers)
        assert response.status_code == 403, response.text
        response = await test_client.put(
            f"/api/knowledge/databases/{a_id}",
            headers=a_headers,
            json={
                **update,
                "share_config": {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
            },
        )
        assert response.status_code == 403, response.text

        for route in ("documents", "documents/add"):
            response = await test_client.post(
                f"/api/knowledge/databases/{b_id}/{route}", json=document_payload, headers=b_headers
            )
            assert response.status_code == 400, response.text
            fake_source = f"minio://knowledgebases/{b_id}/upload/fake.html"
            response = await test_client.post(
                f"/api/knowledge/databases/{b_id}/{route}",
                headers=b_headers,
                json={
                    "items": [fake_source],
                    "params": {"_preprocessed_map": {fake_source: {"path": source, "content_hash": content_hash}}},
                },
            )
            assert response.status_code == 400, response.text
        assert await connection.fetchval("SELECT COUNT(*) FROM knowledge_files WHERE kb_id = $1", b_id) == 0
        assert json.loads(
            await connection.fetchval("SELECT share_config FROM knowledge_bases WHERE kb_id = $1", a_id)
        ) == {"version": 2, "read_scope": None, "manage_scope": None}
    finally:
        for kb_id, headers in reversed(databases):
            response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=headers)
            assert response.status_code == 200, response.text
        ids = [actor["id"] for actor in actors.values()]
        if ids:
            await connection.execute("DELETE FROM operation_logs WHERE user_id = ANY($1::integer[])", ids)
            await connection.execute("DELETE FROM users WHERE id = ANY($1::integer[])", ids)
        await connection.execute("DELETE FROM departments WHERE name = $1", department_name)
        await connection.close()
