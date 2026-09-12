"""通过真实 HTTP 与 PostgreSQL 验证团队知识库的业务入口和权限。"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_team_knowledge_read_and_manage_boundaries(test_client) -> None:
    """同部门辅导人员只读，业务管理员维护，跨部门及个人资料隔离。"""
    suffix = uuid.uuid4().hex[:10]
    connection = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    departments: list[int] = []
    actors: dict[str, dict] = {}
    databases: list[tuple[str, dict]] = []
    try:
        for name in ("team", "other"):
            department_id = await connection.fetchval(
                "INSERT INTO departments (name, description) VALUES ($1, 'team knowledge test') RETURNING id",
                f"pytest_team_{name}_{suffix}",
            )
            departments.append(department_id)

        for name, role, roles, department_id in (
            ("counselor", "user", ["counselor"], departments[0]),
            ("teammate", "user", ["counselor"], departments[0]),
            ("outsider", "user", ["counselor"], departments[1]),
            ("business", "user", ["business_admin"], departments[0]),
            ("other_business", "user", ["business_admin"], departments[1]),
            ("business_no_department", "user", ["business_admin"], None),
            ("no_role", "user", [], departments[0]),
            ("platform_admin", "admin", ["business_admin"], departments[0]),
        ):
            uid = f"team_{name}_{suffix}"
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
            actors[name] = {
                "id": user_id,
                "uid": uid,
                "headers": {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user_id)})}"},
            }

        payload = {
            "database_name": f"pytest_team_knowledge_{suffix}",
            "description": "team knowledge integration test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
        }
        for bad_share in (
            {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
            {
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [departments[1]]},
                "manage_scope": None,
            },
        ):
            denied = await test_client.post(
                "/api/knowledge/databases",
                json={**payload, "share_config": bad_share},
                headers=actors["business"]["headers"],
            )
            assert denied.status_code == 403, denied.text
        denied = await test_client.post(
            "/api/knowledge/databases",
            json={**payload, "share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
            headers=actors["business"]["headers"],
        )
        assert denied.status_code == 403, denied.text
        denied = await test_client.post(
            "/api/knowledge/databases", json=payload, headers=actors["business_no_department"]["headers"]
        )
        assert denied.status_code == 400, denied.text
        denied = await test_client.post(
            "/api/knowledge/databases",
            json={**payload, "database_name": f"pytest_personal_knowledge_{suffix}"},
            headers=actors["counselor"]["headers"],
        )
        assert denied.status_code == 200, denied.text
        personal_id = denied.json()["kb_id"]
        databases.append((personal_id, actors["counselor"]["headers"]))

        team_created = await test_client.post(
            "/api/knowledge/databases", json=payload, headers=actors["business"]["headers"]
        )
        assert team_created.status_code == 200, team_created.text
        team_id = team_created.json()["kb_id"]
        databases.append((team_id, actors["business"]["headers"]))
        team_config = {
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [departments[0]], "user_uids": []},
            "manage_scope": None,
        }
        record = await connection.fetchrow(
            "SELECT created_by, share_config FROM knowledge_bases WHERE kb_id = $1", team_id
        )
        assert record["created_by"] == actors["business"]["uid"]
        assert json.loads(record["share_config"]) == team_config

        for name, can_see_team, can_see_personal, can_manage_team in (
            ("counselor", True, True, False),
            ("teammate", True, False, False),
            ("outsider", False, False, False),
            ("business", True, False, True),
            ("other_business", False, False, False),
            ("platform_admin", True, False, False),
        ):
            listed = await test_client.get("/api/knowledge/databases", headers=actors[name]["headers"])
            assert listed.status_code == 200, listed.text
            visible = {item["kb_id"]: item for item in listed.json()["databases"]}
            assert (team_id in visible) is can_see_team, name
            assert (personal_id in visible) is can_see_personal, name
            if can_see_team:
                assert visible[team_id]["can_manage"] is can_manage_team

        for name in ("outsider", "other_business"):
            denied = await test_client.get(
                f"/api/knowledge/databases/{team_id}", headers=actors[name]["headers"]
            )
            assert denied.status_code == 403, denied.text
        denied = await test_client.get(
            f"/api/knowledge/databases/{personal_id}", headers=actors["business"]["headers"]
        )
        assert denied.status_code == 403, denied.text
        denied = await test_client.get("/api/knowledge/databases", headers=actors["no_role"]["headers"])
        assert denied.status_code == 403, denied.text
        for name in ("outsider", "other_business", "no_role"):
            denied = await test_client.get(
                "/api/workspace/knowledge/tree",
                params={"kb_id": team_id},
                headers=actors[name]["headers"],
            )
            assert denied.status_code == 403, f"{name}: {denied.status_code} {denied.text}"

        update = {"name": f"pytest_team_updated_{suffix}", "description": "maintained by business role"}
        saved = await test_client.put(
            f"/api/knowledge/databases/{team_id}", json=update, headers=actors["business"]["headers"]
        )
        assert saved.status_code == 200, saved.text
        assert (
            await connection.fetchval("SELECT description FROM knowledge_bases WHERE kb_id = $1", team_id)
            == update["description"]
        )
        content = f"team material {suffix}".encode()
        uploaded = await test_client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": team_id},
            files={"file": ("team.txt", content, "text/plain")},
            headers=actors["business"]["headers"],
        )
        assert uploaded.status_code == 200, uploaded.text
        source = uploaded.json()["file_path"]
        content_hash = uploaded.json()["content_hash"]
        added = await test_client.post(
            f"/api/knowledge/databases/{team_id}/documents/add",
            json={"items": [source], "params": {"content_hashes": {source: content_hash}}},
            headers=actors["business"]["headers"],
        )
        assert added.status_code == 200 and added.json()["status"] == "success", added.text
        file_id = added.json()["items"][0]["file_id"]
        assert await connection.fetchval(
            "SELECT created_by FROM knowledge_files WHERE file_id = $1 AND kb_id = $2", file_id, team_id
        ) == actors["business"]["uid"]
        for name in ("counselor", "teammate", "platform_admin"):
            read = await test_client.get(
                f"/api/knowledge/databases/{team_id}/documents/{file_id}/download",
                headers=actors[name]["headers"],
            )
            assert read.status_code == 200 and read.content == content
        for name in ("outsider", "other_business"):
            read = await test_client.get(
                f"/api/knowledge/databases/{team_id}/documents/{file_id}/download",
                headers=actors[name]["headers"],
            )
            assert read.status_code == 403, read.text

        await connection.execute(
            "UPDATE knowledge_bases SET created_by = $1 WHERE kb_id = $2", actors["counselor"]["uid"], team_id
        )
        for name in ("counselor", "teammate", "outsider", "other_business"):
            response = await test_client.put(
                f"/api/knowledge/databases/{team_id}", json=update, headers=actors[name]["headers"]
            )
            assert response.status_code == 403, f"{name}: {response.status_code} {response.text}"
        for name in ("counselor", "teammate", "outsider", "other_business"):
            response = await test_client.post(
                "/api/knowledge/files/upload",
                params={"kb_id": team_id},
                files={"file": ("blocked.txt", b"blocked", "text/plain")},
                headers=actors[name]["headers"],
            )
            assert response.status_code == 403, f"{name}: {response.status_code} {response.text}"
        forbidden_share = await test_client.put(
            f"/api/knowledge/databases/{team_id}",
            json={**update, "share_config": {"version": 2, "read_scope": {"access_level": "global"}}},
            headers=actors["business"]["headers"],
        )
        assert forbidden_share.status_code == 403, forbidden_share.text
        assert json.loads(
            await connection.fetchval("SELECT share_config FROM knowledge_bases WHERE kb_id = $1", team_id)
        ) == team_config
        owner_read = await test_client.get(
            f"/api/knowledge/databases/{team_id}", headers=actors["counselor"]["headers"]
        )
        assert owner_read.status_code == 200 and owner_read.json()["can_manage"] is False
        manager_read = await test_client.get(
            f"/api/knowledge/databases/{team_id}", headers=actors["business"]["headers"]
        )
        assert manager_read.status_code == 200 and manager_read.json()["can_manage"] is True
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM knowledge_files WHERE kb_id = $1", team_id
        ) == 1
    finally:
        for kb_id, headers in reversed(databases):
            deleted = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=headers)
            assert deleted.status_code == 200, deleted.text
        ids = [actor["id"] for actor in actors.values()]
        if ids:
            await connection.execute("DELETE FROM operation_logs WHERE user_id = ANY($1::integer[])", ids)
            await connection.execute("DELETE FROM users WHERE id = ANY($1::integer[])", ids)
        for department_id in departments:
            await connection.execute("DELETE FROM departments WHERE id = $1", department_id)
        await connection.close()
