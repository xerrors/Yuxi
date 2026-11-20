"""
Integration tests for authentication-related API routes.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.modules.identity.services.login_limits as login_limiter
from yuxi.modules.identity.models import User as UserModel
from yuxi.infrastructure.redis import close_async_redis_client, create_async_redis_client, get_async_redis_client
from yuxi.shared.datetime import utc_now
from test.integration.api.helpers import create_api_user, create_department, delete_department_by_id, delete_user_by_id

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture()
async def isolated_redis_client():
    """把进程内共享 Redis 客户端绑定到当前用例的 loop，结束后关闭。

    集成套件跨用例共享 pytest 进程内的 Redis 单例；若其在上一个用例的 loop
    中创建、本用例 loop 中复用会报 "attached to a different loop"。先关闭再
    重建，保证本用例拿到绑定当前 loop 的新客户端。
    """
    await close_async_redis_client()
    client = await get_async_redis_client()
    yield client
    await close_async_redis_client()


async def _expire_login_lock(user_id: int) -> None:
    """用一次性引擎把用户锁定截止时间改到过去。

    不复用 pg_manager 的共享引擎：其连接池绑定在别的 loop 上，
    跨用例事件循环复用会报 "attached to a different loop"。
    """
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            await session.execute(
                update(UserModel).where(UserModel.id == user_id).values(login_locked_until=utc_now() - timedelta(seconds=1))
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _clear_login_failure_keys():
    # 每个用例的事件循环不同，不复用共享单例客户端
    redis = await create_async_redis_client()
    try:
        keys = [key async for key in redis.scan_iter(match="yuxi:login-failure:*")]
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_login_rate_limits(test_client):
    """清理 Redis 登录失败计数，避免用例间通过共享 Redis 相互影响。"""
    await _clear_login_failure_keys()
    yield
    await _clear_login_failure_keys()
    # 一次成功登录会清空内存中间件在同一 IP 上的滑动窗口计数，
    # 避免失败密集的用例把同 IP 的后续用例顶到中间件限速。
    username = os.getenv("TEST_USERNAME")
    password = os.getenv("TEST_PASSWORD")
    if username and password:
        await test_client.post("/api/auth/token", data={"username": username, "password": password})


async def _require_superadmin(test_client, headers):
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    if response.json()["role"] != "superadmin":
        pytest.fail("This test requires TEST_USERNAME to be a superadmin account.")


async def _create_department_with_admin(test_client, headers, label: str) -> dict:
    suffix = uuid.uuid4().hex[:8]
    admin_uid = f"adm{label}_{suffix}"
    admin_password = f"Pw!{suffix}"
    department = await create_department(
        test_client,
        headers,
        {
            "name": f"pytest_{label}_{suffix}",
            "description": "pytest managed department",
            "admin_uid": admin_uid,
            "admin_password": admin_password,
        },
    )

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": admin_uid, "password": admin_password},
    )
    assert login_response.status_code == 200, login_response.text

    login_payload = login_response.json()
    return {
        "department": department,
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

    return await create_api_user(test_client, headers, payload)


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


async def test_login_rate_limit_blocks_repeated_failures_per_ip_and_account(test_client, isolated_redis_client):
    request_headers = {"X-Forwarded-For": "192.0.2.77"}
    identifier = f"nouser_{uuid.uuid4().hex[:8]}"
    for _ in range(login_limiter.LOGIN_FAILURE_IP_ACCOUNT_MAX):
        response = await test_client.post(
            "/api/auth/token", headers=request_headers, data={"username": identifier, "password": "wrong-password"}
        )
        assert response.status_code == 401, response.text

    # 第 10 次失败后，同 IP+账号组合进入滑动窗口限速
    blocked_response = await test_client.post(
        "/api/auth/token",
        headers=request_headers,
        data={"username": identifier, "password": "wrong-password"},
    )
    assert blocked_response.status_code == 429, blocked_response.text
    assert int(blocked_response.headers["Retry-After"]) >= 1
    assert "过于频繁" in blocked_response.json()["detail"]


async def test_expired_lock_resets_failure_count_before_next_failure(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for _ in range(4):
        response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
        assert response.status_code == 401, response.text
    # 第 5 次失败触发账号锁定
    locked_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert locked_response.status_code == 423, locked_response.text

    # 把锁定截止时间改到过去，模拟锁定到期
    await _expire_login_lock(standard_user["user"]["id"])

    # 锁定过期后首次失败应是普通 401，而不是失败计数残留导致的立即再锁定
    wrong_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert wrong_response.status_code == 401, wrong_response.text

    # 正确密码可正常登录
    success_response = await test_client.post(
        "/api/auth/token",
        data={"username": uid, "password": standard_user["password"]},
    )
    assert success_response.status_code == 200, success_response.text


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

    list_response = await test_client.get("/api/auth/users?limit=1000", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    assert created_user["id"] not in {user["id"] for user in list_response.json()}


async def test_admin_user_page_filters_before_pagination_and_excludes_deleted(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    created_users = []
    try:
        for index in range(3):
            response = await test_client.post(
                "/api/auth/users",
                json={
                    "username": f"paged_{suffix}_{index}",
                    "password": "routerTest123!",
                    "role": "user",
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            created_users.append(response.json())

        delete_response = await test_client.delete(f"/api/auth/users/{created_users[1]['id']}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text

        page_response = await test_client.get(
            "/api/auth/users/page",
            params={"search": f"paged_{suffix}", "offset": 1, "limit": 1, "role": "user"},
            headers=admin_headers,
        )
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert page["total"] == 2
        assert page["limit"] == 1
        assert page["offset"] == 1
        assert [item["id"] for item in page["items"]] == [created_users[2]["id"]]
        assert created_users[1]["id"] not in {item["id"] for item in page["items"]}
    finally:
        for user in created_users:
            await test_client.delete(f"/api/auth/users/{user['id']}", headers=admin_headers)


async def test_admin_user_page_accepts_multi_select_role_and_department_filters(test_client, admin_headers):
    """恢复单值相等过滤时，表头多选筛选必须在真实 HTTP 链路上失败。"""
    await _require_superadmin(test_client, admin_headers)

    created_users = []
    department_ids: list[int] = []
    try:
        dept_a = await _create_department_with_admin(test_client, admin_headers, "m1")
        dept_b = await _create_department_with_admin(test_client, admin_headers, "m2")
        department_ids = [dept_a["department"]["id"], dept_b["department"]["id"]]

        for label in ("u1", "u2"):
            response = await test_client.post(
                "/api/auth/users",
                json={
                    "username": f"mu_{uuid.uuid4().hex[:8]}_{label}",
                    "password": "routerTest123!",
                    "role": "user",
                    "department_id": dept_b["department"]["id"],
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            created_users.append(response.json())

        # 超管视角：角色与部门多选同时生效。
        multi_response = await test_client.get(
            "/api/auth/users/page",
            params={
                "roles": "admin,user",
                "department_ids": f"{dept_a['department']['id']},{dept_b['department']['id']}",
                "limit": 100,
            },
            headers=admin_headers,
        )
        assert multi_response.status_code == 200, multi_response.text
        multi_page = multi_response.json()
        assert {item["id"] for item in multi_page["items"]} == {
            dept_a["admin_id"],
            dept_b["admin_id"],
            created_users[0]["id"],
            created_users[1]["id"],
        }

        single_role_response = await test_client.get(
            "/api/auth/users/page",
            params={"roles": "admin", "limit": 100},
            headers=admin_headers,
        )
        assert single_role_response.status_code == 200, single_role_response.text
        single_role_ids = {item["id"] for item in single_role_response.json()["items"]}
        assert dept_a["admin_id"] in single_role_ids
        assert {created_users[0]["id"], created_users[1]["id"]}.isdisjoint(single_role_ids)

        # 部门管理员视角：多选角色生效，但可见部门始终限定本部门。
        dept_view_response = await test_client.get(
            "/api/auth/users/page",
            params={
                "roles": "admin,user",
                "department_ids": str(dept_b["department"]["id"]),
                "limit": 100,
            },
            headers=dept_a["admin_headers"],
        )
        assert dept_view_response.status_code == 200, dept_view_response.text
        dept_view = dept_view_response.json()
        assert {item["id"] for item in dept_view["items"]} == {dept_a["admin_id"]}
    finally:
        for user in created_users:
            await test_client.delete(f"/api/auth/users/{user['id']}", headers=admin_headers)
        for department_id in department_ids:
            await delete_department_by_id(test_client, admin_headers, department_id)


async def test_admin_password_mutations_reject_passwords_shorter_than_eight_characters(test_client, admin_headers, standard_user):
    create_response = await test_client.post(
        "/api/auth/users",
        json={"username": f"weak_{uuid.uuid4().hex[:8]}", "password": "short", "role": "user"},
        headers=admin_headers,
    )
    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["detail"][0]["loc"] == ["body", "password"]

    update_response = await test_client.put(
        f"/api/auth/users/{standard_user['user']['id']}",
        json={"password": "short"},
        headers=admin_headers,
    )
    assert update_response.status_code == 422, update_response.text
    assert update_response.json()["detail"][0]["loc"] == ["body", "password"]


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

        page_response = await test_client.get(
            "/api/auth/users/page",
            params={"department_id": department_b["id"], "limit": 100},
            headers=dept_a["admin_headers"],
        )
        assert page_response.status_code == 200, page_response.text
        paged_users = page_response.json()["items"]
        paged_user_ids = {user["id"] for user in paged_users}
        assert user_a["id"] in paged_user_ids
        assert user_b["id"] not in paged_user_ids
        assert all(user["department_id"] == department_a["id"] for user in paged_users)

        options_response = await test_client.get("/api/auth/users/access-options", headers=dept_a["admin_headers"])
        assert options_response.status_code == 200, options_response.text
        access_options = options_response.json()
        option_uids = {user["uid"] for user in access_options}
        assert user_a["uid"] in option_uids
        assert user_b["uid"] in option_uids
        assert all(set(user) == {"uid", "username", "role", "department_id", "department_name"} for user in access_options)

        superadmin_list_response = await test_client.get("/api/auth/users?limit=1000", headers=admin_headers)
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

        role_escalation = await test_client.put(f"/api/auth/users/{user_a['id']}", json={"role": "admin"}, headers=dept_a["admin_headers"])
        assert role_escalation.status_code == 403, role_escalation.text
        assert role_escalation.json()["detail"] == "只有系统管理员可以提升角色"

        cross_delete = await test_client.delete(f"/api/auth/users/{user_b['id']}", headers=dept_a["admin_headers"])
        assert cross_delete.status_code == 403, cross_delete.text

        own_delete = await test_client.delete(f"/api/auth/users/{user_a['id']}", headers=dept_a["admin_headers"])
        assert own_delete.status_code == 200, own_delete.text
        user_ids.remove(user_a["id"])
    finally:
        for user_id in user_ids:
            await delete_user_by_id(test_client, admin_headers, user_id)
        for admin_id in admin_ids:
            await delete_user_by_id(test_client, admin_headers, admin_id)
        for department_id in department_ids:
            await delete_department_by_id(test_client, admin_headers, department_id)


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


async def test_user_access_options_search_and_selected_backfill(test_client, admin_headers):
    """access-options 支持服务端搜索，并用 include_uids 回填已选用户（修大表上限搜不到）。"""
    token = uuid.uuid4().hex[:8]
    created = []
    try:
        for label in ("alpha", "beta"):
            response = await test_client.post(
                "/api/auth/users",
                json={"username": f"opt_{token}_{label}", "password": "routerTest123!", "role": "user"},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            created.append(response.json())
        alpha, beta = created

        # 命中 token 的用户都返回
        both = await test_client.get("/api/auth/users/access-options", params={"search": token}, headers=admin_headers)
        assert both.status_code == 200, both.text
        both_uids = {user["uid"] for user in both.json()}
        assert {alpha["uid"], beta["uid"]} <= both_uids

        # 搜索收窄到单个用户
        narrowed = await test_client.get("/api/auth/users/access-options", params={"search": f"opt_{token}_alpha"}, headers=admin_headers)
        narrowed_uids = {user["uid"] for user in narrowed.json()}
        assert alpha["uid"] in narrowed_uids
        assert beta["uid"] not in narrowed_uids

        # 不匹配搜索词但被 include_uids 指定 → 仍随结果返回，避免翻页后选中项丢失
        backfill = await test_client.get(
            "/api/auth/users/access-options",
            params={"search": "opt_nonexistent_zzz", "include_uids": f" {beta['uid']} , ,opt_ghost_zzz "},
            headers=admin_headers,
        )
        assert backfill.status_code == 200, backfill.text
        backfill_uids = {user["uid"] for user in backfill.json()}
        assert beta["uid"] in backfill_uids
        assert alpha["uid"] not in backfill_uids

        # 无搜索词首屏：已选用户落在前 limit 条之外也要被补回（limit=1 模拟万级用户首页）
        first_page = await test_client.get(
            "/api/auth/users/access-options",
            params={"limit": 1, "include_uids": beta["uid"]},
            headers=admin_headers,
        )
        assert first_page.status_code == 200, first_page.text
        first_page_uids = {user["uid"] for user in first_page.json()}
        assert beta["uid"] in first_page_uids
        # 首屏基础分页仍只有 1 条 + 回填至多 1 条，不放大成全量
        assert len(first_page_uids) <= 2
    finally:
        for user in created:
            await test_client.delete(f"/api/auth/users/{user['id']}", headers=admin_headers)
