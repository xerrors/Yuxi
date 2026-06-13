"""
Integration tests for authentication-related API routes.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _require_superadmin(test_client, headers):
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    if response.json()["role"] != "superadmin":
        pytest.fail("This test requires TEST_USERNAME to be a superadmin account.")


async def _create_department_with_admin(test_client, headers, label: str) -> dict:
    suffix = uuid.uuid4().hex[:8]
    admin_uid = f"adm{label}_{suffix}"
    admin_password = f"Pw!{suffix}"
    response = await test_client.post(
        "/api/departments",
        json={
            "name": f"pytest_{label}_{suffix}",
            "description": "pytest managed department",
            "admin_uid": admin_uid,
            "admin_password": admin_password,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": admin_uid, "password": admin_password},
    )
    assert login_response.status_code == 200, login_response.text

    login_payload = login_response.json()
    return {
        "department": response.json(),
        "admin_id": login_payload["user_id"],
        "admin_headers": {"Authorization": f"Bearer {login_payload['access_token']}"},
    }


async def _create_user(test_client, headers, label: str, role: str = "user", department_id: int | None = None) -> dict:
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"u{label}_{suffix}",
        "password": f"Pw!{suffix}",
        "role": role,
    }
    if department_id is not None:
        payload["department_id"] = department_id

    response = await test_client.post("/api/auth/users", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _cleanup_user(test_client, headers, user_id: int) -> None:
    response = await test_client.delete(f"/api/auth/users/{user_id}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def _cleanup_department(test_client, headers, department_id: int) -> None:
    response = await test_client.delete(f"/api/departments/{department_id}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def test_login_with_invalid_credentials(test_client):
    response = await test_client.post("/api/auth/token", data={"username": "invalid", "password": "invalid"})
    assert response.status_code == 401
    assert "detail" in response.json()


async def test_user_is_locked_after_repeated_failed_logins(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for attempt in range(1, 5):
        response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "用户名或密码错误"

    locked_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert locked_response.status_code == 423, locked_response.text
    assert "X-Lock-Remaining" in locked_response.headers
    assert "账户已被锁定" in locked_response.json()["detail"]

    still_locked_response = await test_client.post(
        "/api/auth/token",
        data={"username": uid, "password": standard_user["password"]},
    )
    assert still_locked_response.status_code == 423, still_locked_response.text
    assert "X-Lock-Remaining" in still_locked_response.headers
    assert "登录被锁定" in still_locked_response.json()["detail"]


async def test_admin_can_login_and_fetch_profile(test_client, admin_headers):
    profile_response = await test_client.get("/api/auth/me", headers=admin_headers)
    assert profile_response.status_code == 200
    data = profile_response.json()
    assert data["role"] in {"admin", "superadmin"}
    assert data["username"]
    assert data["id"]


async def test_profile_requires_authentication(test_client):
    response = await test_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "请登录后再访问"


async def test_admin_can_create_and_delete_user(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"rtu_{suffix}",
        "password": "routerTest123!",
        "role": "user",
    }
    create_response = await test_client.post("/api/auth/users", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text

    created_user = create_response.json()
    assert created_user["username"] == payload["username"]
    assert created_user["role"] == payload["role"]

    delete_response = await test_client.delete(f"/api/auth/users/{created_user['id']}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text
    delete_payload = delete_response.json()
    assert delete_payload["success"] is True
    assert delete_payload["message"] == "用户已删除"


async def test_department_admin_is_limited_to_own_department_users(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)

    user_ids: list[int] = []
    admin_ids: list[int] = []
    department_ids: list[int] = []

    try:
        dept_a = await _create_department_with_admin(test_client, admin_headers, "a")
        dept_b = await _create_department_with_admin(test_client, admin_headers, "b")
        department_a = dept_a["department"]
        department_b = dept_b["department"]
        department_ids.extend([department_a["id"], department_b["id"]])
        admin_ids.extend([dept_a["admin_id"], dept_b["admin_id"]])

        user_a = await _create_user(test_client, dept_a["admin_headers"], "a")
        user_b = await _create_user(test_client, dept_b["admin_headers"], "b")
        superadmin_created_user = await _create_user(test_client, admin_headers, "s", department_id=department_b["id"])
        user_ids.extend([user_a["id"], user_b["id"], superadmin_created_user["id"]])

        assert user_a["department_id"] == department_a["id"]
        assert superadmin_created_user["department_id"] == department_b["id"]

        forbidden_create = await test_client.post(
            "/api/auth/users",
            json={
                "username": f"ux_{uuid.uuid4().hex[:8]}",
                "password": "routerTest123!",
                "role": "user",
                "department_id": department_b["id"],
            },
            headers=dept_a["admin_headers"],
        )
        assert forbidden_create.status_code == 403, forbidden_create.text

        list_response = await test_client.get("/api/auth/users", headers=dept_a["admin_headers"])
        assert list_response.status_code == 200, list_response.text
        listed_users = list_response.json()
        listed_user_ids = {user["id"] for user in listed_users}
        assert user_a["id"] in listed_user_ids
        assert user_b["id"] not in listed_user_ids
        assert all(user["department_id"] == department_a["id"] for user in listed_users)

        options_response = await test_client.get("/api/auth/users/access-options", headers=dept_a["admin_headers"])
        assert options_response.status_code == 200, options_response.text
        access_options = options_response.json()
        option_uids = {user["uid"] for user in access_options}
        assert user_a["uid"] in option_uids
        assert user_b["uid"] not in option_uids
        assert all(user["department_id"] == department_a["id"] for user in access_options)

        superadmin_list_response = await test_client.get("/api/auth/users", headers=admin_headers)
        assert superadmin_list_response.status_code == 200, superadmin_list_response.text
        superadmin_user_ids = {user["id"] for user in superadmin_list_response.json()}
        assert user_a["id"] in superadmin_user_ids
        assert user_b["id"] in superadmin_user_ids

        own_read = await test_client.get(f"/api/auth/users/{user_a['id']}", headers=dept_a["admin_headers"])
        assert own_read.status_code == 200, own_read.text

        cross_read = await test_client.get(f"/api/auth/users/{user_b['id']}", headers=dept_a["admin_headers"])
        assert cross_read.status_code == 403, cross_read.text

        cross_update = await test_client.put(
            f"/api/auth/users/{user_b['id']}",
            json={"username": f"ub_{uuid.uuid4().hex[:8]}"},
            headers=dept_a["admin_headers"],
        )
        assert cross_update.status_code == 403, cross_update.text

        role_escalation = await test_client.put(
            f"/api/auth/users/{user_a['id']}", json={"role": "admin"}, headers=dept_a["admin_headers"]
        )
        assert role_escalation.status_code == 403, role_escalation.text

        cross_delete = await test_client.delete(f"/api/auth/users/{user_b['id']}", headers=dept_a["admin_headers"])
        assert cross_delete.status_code == 403, cross_delete.text

        own_delete = await test_client.delete(f"/api/auth/users/{user_a['id']}", headers=dept_a["admin_headers"])
        assert own_delete.status_code == 200, own_delete.text
        user_ids.remove(user_a["id"])
    finally:
        for user_id in user_ids:
            await _cleanup_user(test_client, admin_headers, user_id)
        for admin_id in admin_ids:
            await _cleanup_user(test_client, admin_headers, admin_id)
        for department_id in department_ids:
            await _cleanup_department(test_client, admin_headers, department_id)


async def test_invalid_token_is_rejected(test_client):
    headers = {"Authorization": "Bearer not-a-real-token"}
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401


async def test_deleted_user_token_is_rejected(test_client, admin_headers, standard_user):
    user_id = standard_user["user"]["id"]

    delete_response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text

    profile_response = await test_client.get("/api/auth/me", headers=standard_user["headers"])
    assert profile_response.status_code == 401


async def test_locked_user_token_is_rejected(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for _ in range(5):
        await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})

    profile_response = await test_client.get("/api/auth/me", headers=standard_user["headers"])
    assert profile_response.status_code == 423
    assert "X-Lock-Remaining" in profile_response.headers
