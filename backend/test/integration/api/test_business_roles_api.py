"""业务角色的真实 HTTP 授权测试。"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_business_roles_compose_without_expanding_legacy_admin_access(test_client) -> None:
    """超级管理员可组合角色，普通管理员只能沿用辅导人员默认值。"""

    suffix = uuid.uuid4().hex[:10]
    department_name = f"pytest_business_roles_{suffix}"
    super_uid = f"roles_super_{suffix}"
    admin_uid = f"roles_admin_{suffix}"
    created_uids = {super_uid, admin_uid}
    connection = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        department_id = await connection.fetchval(
            "INSERT INTO departments (name, description) VALUES ($1, $2) RETURNING id",
            department_name,
            "business role authorization test",
        )
        super_id = await connection.fetchval(
            """
            INSERT INTO users
                (username, uid, password_hash, role, business_roles, department_id,
                 login_failed_count, is_deleted, created_at)
            VALUES ($1, $1, 'test', 'superadmin', '["technical_admin"]'::jsonb, $2, 0, 0, NOW())
            RETURNING id
            """,
            super_uid,
            department_id,
        )
        admin_id = await connection.fetchval(
            """
            INSERT INTO users
                (username, uid, password_hash, role, business_roles, department_id,
                 login_failed_count, is_deleted, created_at)
            VALUES ($1, $1, 'test', 'admin', '["business_admin"]'::jsonb, $2, 0, 0, NOW())
            RETURNING id
            """,
            admin_uid,
            department_id,
        )
        super_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(super_id)})}"}
        admin_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(admin_id)})}"}

        profile_response = await test_client.get("/api/auth/me", headers=super_headers)
        assert profile_response.status_code == 200, profile_response.text
        assert profile_response.json()["business_roles"] == ["technical_admin"]

        forbidden_response = await test_client.post(
            "/api/auth/users",
            json={
                "username": f"forbidden_{suffix}",
                "password": "routerTest123!",
                "role": "user",
                "business_roles": ["business_admin"],
            },
            headers=admin_headers,
        )
        assert forbidden_response.status_code == 403, forbidden_response.text
        assert forbidden_response.json()["detail"] == "只有超级管理员才能指定业务角色"

        legacy_admin_response = await test_client.post(
            "/api/auth/users",
            json={"username": f"counselor_{suffix}", "password": "routerTest123!", "role": "user"},
            headers=admin_headers,
        )
        assert legacy_admin_response.status_code == 200, legacy_admin_response.text
        counselor = legacy_admin_response.json()
        created_uids.add(counselor["uid"])
        assert counselor["business_roles"] == ["counselor"]

        update_response = await test_client.put(
            f"/api/auth/users/{counselor['id']}",
            json={"business_roles": ["business_admin", "counselor", "counselor"]},
            headers=super_headers,
        )
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["business_roles"] == ["counselor", "business_admin"]

        persisted_roles = await connection.fetchval(
            "SELECT business_roles FROM users WHERE id = $1",
            counselor["id"],
        )
        assert json.loads(persisted_roles) == ["counselor", "business_admin"]
    finally:
        user_ids = await connection.fetch("SELECT id FROM users WHERE uid = ANY($1::varchar[])", list(created_uids))
        ids = [row["id"] for row in user_ids]
        if ids:
            await connection.execute("DELETE FROM operation_logs WHERE user_id = ANY($1::integer[])", ids)
            await connection.execute("DELETE FROM users WHERE id = ANY($1::integer[])", ids)
        await connection.execute("DELETE FROM departments WHERE name = $1", department_name)
        await connection.close()
