"""真实 HTTP、PostgreSQL 学生档案归属和迁移测试。"""

import os
import uuid

import asyncpg
import pytest

from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_student_owner_and_manager_access_are_isolated(test_client):
    """不同负责人、部门及技术管理员不能读取或覆盖背景。"""
    suffix = uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    departments = []
    users = []

    async def actor(department_id, role, business_roles):
        uid = f"student_{suffix}_{len(users)}"
        user_id = await conn.fetchval(
            """
            INSERT INTO users (username, uid, password_hash, role, business_roles,
                               department_id, login_failed_count, is_deleted, created_at)
            VALUES ($1, $1, 'test', $2, $3::jsonb, $4, 0, 0, NOW()) RETURNING id
            """,
            uid,
            role,
            business_roles,
            department_id,
        )
        users.append(user_id)
        return user_id, {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user_id)})}"}

    try:
        for number in (1, 2):
            departments.append(
                await conn.fetchval(
                    "INSERT INTO departments (name, description) VALUES ($1, 'test') RETURNING id",
                    f"student_dept_{suffix}_{number}",
                )
            )
        manager_id, manager = await actor(departments[0], "admin", '["business_admin"]')
        owner_id, owner = await actor(departments[0], "user", '["counselor"]')
        other_id, other = await actor(departments[0], "user", '["counselor"]')
        tech_id, tech = await actor(departments[0], "superadmin", '["technical_admin"]')
        _, foreign_manager = await actor(departments[1], "admin", '["business_admin"]')
        _, no_role = await actor(departments[0], "user", "[]")

        denied = await test_client.post(
            "/api/counseling/students",
            headers=manager,
            json={"student_code": "S-001", "counselor_id": tech_id},
        )
        assert denied.status_code == 422, denied.text
        denied = await test_client.post(
            "/api/counseling/students",
            headers=foreign_manager,
            json={"student_code": "S-001", "counselor_id": owner_id},
        )
        assert denied.status_code == 422, denied.text
        for headers in (owner, tech, no_role):
            denied = await test_client.post(
                "/api/counseling/students",
                headers=headers,
                json={"student_code": "S-001", "counselor_id": owner_id},
            )
            assert denied.status_code == 403, denied.text

        created = await test_client.post(
            "/api/counseling/students",
            headers=manager,
            json={"student_code": "S-001", "counselor_id": owner_id},
        )
        assert created.status_code == 201, created.text
        student_id = created.json()["id"]
        assert "background_summary" not in created.json()
        duplicate = await test_client.post(
            "/api/counseling/students",
            headers=manager,
            json={"student_code": "S-001", "counselor_id": other_id},
        )
        assert duplicate.status_code == 409, duplicate.text

        updated = await test_client.put(
            f"/api/counseling/students/{student_id}",
            headers=owner,
            json={"background_summary": "虚构背景，仅测试隔离", "status": "closed"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["background_summary"] == "虚构背景，仅测试隔离"
        persisted = await conn.fetchrow(
            "SELECT department_id, student_code, counselor_id, background_summary, status "
            "FROM counseling_students WHERE id = $1",
            student_id,
        )
        assert tuple(persisted.values()) == (departments[0], "S-001", owner_id, "虚构背景，仅测试隔离", "closed")

        for headers in (manager, other, tech, foreign_manager, no_role):
            detail = await test_client.get(f"/api/counseling/students/{student_id}", headers=headers)
            assert detail.status_code in {403, 404}, detail.text
            assert "虚构背景" not in detail.text
            write = await test_client.put(
                f"/api/counseling/students/{student_id}",
                headers=headers,
                json={"background_summary": "越权覆盖", "status": "active"},
            )
            assert write.status_code in {403, 404}, write.text

        for headers in (manager, owner):
            listed = await test_client.get("/api/counseling/students", headers=headers)
            assert listed.status_code == 200, listed.text
            assert listed.json() == [
                {"id": student_id, "student_code": "S-001", "counselor_id": owner_id, "status": "closed"}
            ]
            assert "background_summary" not in listed.text
        for headers in (other, foreign_manager):
            listed = await test_client.get("/api/counseling/students", headers=headers)
            assert listed.status_code == 200 and listed.json() == [], listed.text
        for headers in (tech, no_role):
            listed = await test_client.get("/api/counseling/students", headers=headers)
            assert listed.status_code == 403, listed.text
        assert (
            await conn.fetchval("SELECT background_summary FROM counseling_students WHERE id = $1", student_id)
        ) == "虚构背景，仅测试隔离"
    finally:
        await conn.execute("DELETE FROM counseling_students WHERE department_id = ANY($1::integer[])", departments)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::integer[])", users)
        await conn.execute("DELETE FROM departments WHERE id = ANY($1::integer[])", departments)
        await conn.close()


async def test_student_migration_is_idempotent_and_checks_status():
    """迁移可重复执行，数据库拒绝非法状态。"""
    pg_manager.initialize()
    await pg_manager.upgrade_business_schema_v8_to_v9()
    await pg_manager.upgrade_business_schema_v8_to_v9()
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        columns = {
            row["column_name"]
            for row in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'counseling_students'"
            )
        }
        assert {"department_id", "student_code", "counselor_id", "background_summary", "status"} <= columns
        constraints = {
            row["conname"]
            for row in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'counseling_students'::regclass"
            )
        }
        assert {"uq_counseling_students_department_code", "ck_counseling_students_status"} <= constraints
        transaction = conn.transaction()
        await transaction.start()
        try:
            dept = await conn.fetchval(
                "INSERT INTO departments (name, description) VALUES ($1, 'test') RETURNING id",
                f"migration_student_{uuid.uuid4().hex}",
            )
            user = await conn.fetchval(
                """INSERT INTO users (username, uid, password_hash, role, department_id,
                                      login_failed_count, is_deleted, created_at)
                   VALUES ($1, $1, 'test', 'user', $2, 0, 0, NOW()) RETURNING id""",
                f"migration_student_{uuid.uuid4().hex}",
                dept,
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """INSERT INTO counseling_students (department_id, student_code, counselor_id, status)
                       VALUES ($1, $2, $3, 'invalid')""",
                    dept,
                    "invalid-state",
                    user,
                )
        finally:
            await transaction.rollback()
    finally:
        await conn.close()
