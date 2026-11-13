"""身份用例的真实 HTTP、PostgreSQL 与事务回滚证据。"""

import asyncio
import os
import socket
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.api.dependencies.auth import get_db
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.identity.auth import auth
from yuxi.api.routers.identity.departments import department
from yuxi.api.routers.identity.users import user_router
from yuxi.infrastructure.postgres.base import Base
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.migrations.schema import IDENTITY_PERMISSION_STATEMENTS
from yuxi.modules.identity.models import APIKey, CLIAuthSession, Department, User, UserConfig
from yuxi.modules.identity.preferences import UserConfigSchema
from yuxi.modules.identity.repositories.api_keys import APIKeyRepository
from yuxi.modules.identity.repositories.departments import DepartmentRepository
from yuxi.modules.identity.repositories.preferences import upsert_user_config
from yuxi.modules.identity.services import api_keys, administration, cli_auth, preferences, users
from yuxi.modules.identity.security import AuthUtils
from yuxi.shared.errors import DomainError, PermissionDeniedError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def scenario(monkeypatch):
    """只使用独立 schema 与本地 TCP，不读取或清理共享用户。"""
    schema = "pytest_identity_" + uuid4().hex
    owner = create_async_engine(os.environ["POSTGRES_URL"])
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    for router in (auth, user_router, department):
        app.include_router(router, prefix="/api")

    async def request_db():
        """HTTP 请求只借用会话，提交必须由真实用例执行。"""
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False))
    task = None
    try:
        async with owner.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
            for statement in IDENTITY_PERMISSION_STATEMENTS:
                await db.execute(text(statement))
        async with factory() as db:
            first = await administration.initialize_system_admin(db, uid="fixture_admin", password="FixturePassword123!", phone_number=None)
            admin_id = first.admin.id
            dep = Department(name="Other")
            db.add(dep)
            await db.flush()
            member = User(
                username="Member",
                uid="member",
                password_hash=AuthUtils.hash_password("FixturePassword123!"),
                role="user",
                department_id=first.department.id,
                avatar="http://minio:9000/public/avatar/test.png",
            )
            manager = User(username="Manager", uid="manager", password_hash="!disabled", role="admin", department_id=dep.id)
            db.add_all([member, manager])
            await db.commit()
            ids = (admin_id, member.id, manager.id, dep.id)
        headers = [
            {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(i), "auth_method": "password"})} for i in ids[:3]
        ]
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", trust_env=False) as http:
            yield {"db": factory, "http": http, "ids": ids, "admin": headers[0], "member": headers[1], "manager": headers[2]}
    finally:
        if task:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
        sock.close()
        await engine.dispose()
        async with owner.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await owner.dispose()


async def test_http_key_replay_permissions_and_tombstone(scenario):
    """重复创建只产生一条事实，撤销后原请求不能复活密钥。"""
    http = scenario["http"]
    payload = {"request_id": "fixture-request", "name": "fixture"}
    responses = await asyncio.gather(*[http.post("/api/user/apikey/", headers=scenario["member"], json=payload) for _ in range(2)])
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    result = responses[0].json()
    key_id = result["api_key"]["id"]
    changed = await http.post("/api/user/apikey/", headers=scenario["member"], json=payload | {"name": "other"})
    assert changed.status_code == 409
    denied = await http.put(f"/api/user/apikey/{key_id}", headers=scenario["manager"], json={"name": "stolen"})
    assert denied.status_code == 403
    invalid = await http.put(f"/api/user/apikey/{key_id}", headers=scenario["member"], json={"access_level": "agents"})
    assert invalid.status_code == 422
    valid = await http.put(f"/api/user/apikey/{key_id}", headers=scenario["member"], json={"name": "renamed"})
    assert valid.status_code == 200 and valid.json()["api_key"]["name"] == "renamed"
    assert (await http.delete(f"/api/user/apikey/{key_id}", headers=scenario["member"])).status_code == 200
    assert (await http.post("/api/user/apikey/", headers=scenario["member"], json=payload)).status_code == 409
    async with scenario["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(APIKey)) == 1
        key = await db.get(APIKey, key_id)
        assert key.name == "renamed" and key.revoked_at is not None and not key.is_enabled
    credential = {"Authorization": "Bearer " + result["secret"]}
    assert (await http.get("/api/auth/me", headers=credential)).status_code == 401


async def test_http_cli_exchange_replays_and_rejects_revocation(scenario):
    """CLI 与 Web 共用凭据签发，已消费会话重放受撤销约束。"""
    http = scenario["http"]
    response = await http.post("/api/auth/cli/sessions", json={"key_name": "fixture cli"})
    assert response.status_code == 200
    session = response.json()
    approval = await http.post(f"/api/auth/cli/sessions/{session['user_code']}/approve", headers=scenario["member"])
    assert approval.status_code == 200
    responses = await asyncio.gather(
        *[http.post("/api/auth/cli/sessions/token", json={"device_code": session["device_code"]}) for _ in range(2)]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    data = responses[0].json()
    async with scenario["db"]() as db:
        row = await db.scalar(select(CLIAuthSession))
        assert row.status == "consumed" and row.api_key_id == data["api_key"]["id"]
        assert await db.scalar(select(func.count()).select_from(APIKey)) == 1
    assert (await http.delete(f"/api/user/apikey/{data['api_key']['id']}", headers=scenario["member"])).status_code == 200
    retry = await http.post("/api/auth/cli/sessions/token", json={"device_code": session["device_code"]})
    assert retry.status_code == 409 and retry.json()["detail"]["error"] == "api_key_revoked"


async def test_http_user_department_and_config_changes(scenario):
    """部门范围、用户资料、配置和迁移结果通过新连接回读。"""
    http = scenario["http"]
    _, member_id, _, other_dep = scenario["ids"]
    denied = await http.put(f"/api/auth/users/{member_id}", headers=scenario["manager"], json={"username": "stolen"})
    assert denied.status_code == 403
    created = await http.post(
        "/api/auth/users", headers=scenario["manager"], json={"username": "Department_member", "password": "FixturePassword123!"}
    )
    assert created.status_code == 200 and created.json()["department_id"] == other_dep
    changed = await http.put("/api/auth/profile", headers=scenario["member"], json={"username": "Updated_member"})
    assert changed.status_code == 200 and changed.json()["username"] == "Updated_member"
    assert (await http.put("/api/user/config", headers=scenario["member"], json={"enable_memory": False})).status_code == 200
    moved = await http.put(f"/api/auth/users/{member_id}", headers=scenario["admin"], json={"department_id": other_dep})
    assert moved.status_code == 200
    key = await http.post(
        "/api/user/apikey/", headers=scenario["member"], json={"request_id": "department-key", "name": "scoped", "department_id": other_dep}
    )
    assert key.status_code == 200
    updated = await http.put(f"/api/departments/{other_dep}", headers=scenario["admin"], json={"description": "changed"})
    assert updated.status_code == 200 and updated.json()["description"] == "changed"
    assert (await http.delete(f"/api/departments/{other_dep}", headers=scenario["admin"])).status_code == 200
    async with scenario["db"]() as db:
        member = await db.get(User, member_id)
        assert member.username == "Updated_member" and member.department_id == 1
        assert await db.get(Department, other_dep) is None
        config = await db.scalar(select(UserConfig).where(UserConfig.uid == "member"))
        assert config.enable_memory is False
        key = await db.get(APIKey, key.json()["api_key"]["id"])
        assert key.revoked_at is not None and key.department_id is None


@pytest.mark.parametrize("actor", ["member", "manager", "admin"])
async def test_http_password_change_updates_only_self_and_new_password_can_login(scenario, actor):
    """各角色均可自助改密，数据库与真实登录共同证明结果。"""
    http = scenario["http"]
    user_id = scenario["ids"][["admin", "member", "manager"].index(actor)]
    async with scenario["db"]() as db:
        user = await db.get(User, user_id)
        user.password_hash = AuthUtils.hash_password("FixturePassword123!")
        uid = user.uid
        await db.commit()
        before = dict((await db.execute(select(User.id, User.password_hash))).all())

    response = await http.put(
        "/api/auth/password",
        headers=scenario[actor],
        json={"current_password": "FixturePassword123!", "new_password": "NewFixturePassword456!"},
    )
    assert response.status_code == 200, response.text
    async with scenario["db"]() as db:
        after = dict((await db.execute(select(User.id, User.password_hash))).all())
    password_hash = after.pop(user_id)
    assert AuthUtils.verify_password(password_hash, "NewFixturePassword456!")
    assert not AuthUtils.verify_password(password_hash, "FixturePassword123!")
    before.pop(user_id)
    assert before == after

    await close_async_redis_client()
    try:
        old_login = await http.post("/api/auth/token", data={"username": uid, "password": "FixturePassword123!"})
        assert old_login.status_code == 401
        new_login = await http.post("/api/auth/token", data={"username": uid, "password": "NewFixturePassword456!"})
        assert new_login.status_code == 200, new_login.text
        assert new_login.json()["user_id"] == user_id
    finally:
        await close_async_redis_client()


@pytest.mark.parametrize("invalid", ["wrong_current", "short_new", "target_id", "missing_current", "unauthenticated"])
async def test_http_password_change_rejects_invalid_requests_without_writes(scenario, invalid):
    """请求校验与原密码校验拒绝时，所有账号哈希保持原值。"""
    async with scenario["db"]() as db:
        before = dict((await db.execute(select(User.id, User.password_hash))).all())
    payload = {"current_password": "FixturePassword123!", "new_password": "NewFixturePassword456!"}
    headers = scenario["member"]
    if invalid == "wrong_current":
        payload["current_password"] = "wrong-password"
    elif invalid == "short_new":
        payload["new_password"] = "short"
    elif invalid == "target_id":
        payload["user_id"] = scenario["ids"][0]
    elif invalid == "missing_current":
        del payload["current_password"]
    else:
        headers = {}
    response = await scenario["http"].put("/api/auth/password", headers=headers, json=payload)
    assert response.status_code == (401 if invalid == "unauthenticated" else 422), response.text
    if invalid == "wrong_current":
        assert response.json()["detail"]["code"] == "incorrect_current_password"
    async with scenario["db"]() as db:
        assert dict((await db.execute(select(User.id, User.password_hash))).all()) == before


@pytest.mark.parametrize("identity", ["enterprise", "deleted", "end_user"])
async def test_password_change_rechecks_identity_in_service(scenario, identity):
    """绕过前端或携带陈旧身份也不能为企业、已删除或终端用户改密。"""
    factory = scenario["db"]
    async with factory() as db:
        actor = await db.get(User, scenario["ids"][1])
        async with factory() as writer:
            user = await writer.get(User, actor.id)
            if identity == "enterprise":
                user.uid = "oidc:fixture"
            elif identity == "deleted":
                user.is_deleted = 1
            else:
                user.user_kind = "end_user"
                user.owner_user_id = scenario["ids"][0]
                user.app_id = "fixture"
                user.end_user_id = "fixture"
            await writer.commit()
        with pytest.raises(PermissionDeniedError):
            await users.change_password(
                db, current_user=actor, current_password="FixturePassword123!", new_password="NewFixturePassword456!"
            )
    assert AuthUtils.verify_password(await _read_password_hash(scenario, scenario["ids"][1]), "FixturePassword123!")
    if identity == "enterprise":
        response = await scenario["http"].put(
            "/api/auth/password",
            headers=scenario["member"],
            json={"current_password": "FixturePassword123!", "new_password": "NewFixturePassword456!"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "enterprise_password_managed"


async def test_concurrent_password_changes_recheck_current_hash(scenario):
    """同一原密码的并发提交只成功一次，后到请求必须读取新哈希。"""
    passwords = ["NewFixturePassword456!", "OtherFixturePassword789!"]
    responses = await asyncio.gather(
        *[
            scenario["http"].put(
                "/api/auth/password",
                headers=scenario["member"],
                json={"current_password": "FixturePassword123!", "new_password": password},
            )
            for password in passwords
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 422]
    winner = next(password for password, response in zip(passwords, responses, strict=True) if response.status_code == 200)
    assert AuthUtils.verify_password(await _read_password_hash(scenario, scenario["ids"][1]), winner)


async def _read_password_hash(scenario, user_id):
    """通过独立会话读取已提交的密码事实。"""
    async with scenario["db"]() as db:
        return (await db.get(User, user_id)).password_hash


@pytest.mark.parametrize("operation", ["key_update", "key_revoke", "config", "department"])
async def test_repositories_do_not_commit_callers_transaction(scenario, operation):
    """恢复任一仓储内部 commit 都会让新连接看到本应回滚的事实。"""
    async with scenario["db"]() as db:
        member = await db.get(User, scenario["ids"][1])
        result = await api_keys.create_api_key(db, actor=member, request_id="rollback-key", name="original")
    key_id = result["api_key"]["id"]
    async with scenario["db"]() as db:
        if operation == "key_update":
            await APIKeyRepository(db).update(await db.get(APIKey, key_id), {"name": "must rollback"})
        elif operation == "key_revoke":
            await APIKeyRepository(db).delete(await db.get(APIKey, key_id))
        elif operation == "config":
            await upsert_user_config(db, uid="member", enable_memory=False)
        else:
            await DepartmentRepository(db).update(scenario["ids"][3], {"name": "must rollback"})
        await db.rollback()
    async with scenario["db"]() as db:
        key = await db.get(APIKey, key_id)
        assert key.name == "original" and key.revoked_at is None and key.is_enabled
        assert await db.scalar(select(UserConfig).where(UserConfig.uid == "member")) is None
        assert (await db.get(Department, scenario["ids"][3])).name == "Other"


@pytest.mark.parametrize(
    "operation",
    [
        "profile",
        "password_change",
        "user_update",
        "user_create",
        "config",
        "department_update",
        "department_delete",
        "key_create",
        "cli_exchange",
    ],
)
async def test_usecase_commit_failure_rolls_back_pending_facts(scenario, monkeypatch, operation):
    """提交失败后故意再次提交原会话，证明用例已经清空待写事实。"""
    factory = scenario["db"]
    device_code = None
    if operation == "cli_exchange":
        async with factory() as db:
            session, device_code = await cli_auth.create_cli_auth_session(db)
            await cli_auth.approve_cli_auth_session(db, session.user_code, await db.get(User, scenario["ids"][1]))
    async with factory() as db:
        actor = await db.get(User, scenario["ids"][0])
        member = await db.get(User, scenario["ids"][1])
        original_commit = db.commit

        async def fail_commit():
            """在 flush 之后切断提交点。"""
            await db.flush()
            raise RuntimeError("fixture commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="fixture commit failure"):
            if operation == "profile":
                await users.update_profile(db, current_user=member, username="Must_rollback")
            elif operation == "password_change":
                await users.change_password(
                    db, current_user=member, current_password="FixturePassword123!", new_password="NewFixturePassword456!"
                )
            elif operation == "user_update":
                await users.update_user(db, current_user=actor, user_id=member.id, username="Must_rollback")
            elif operation == "user_create":
                await users.create_user(db, current_user=actor, username="Must_rollback", password="FixturePassword123!")
            elif operation == "config":
                await preferences.save_user_config(db, uid="member", schema=UserConfigSchema(enable_memory=False))
            elif operation == "department_update":
                await administration.update_department(db, department_id=scenario["ids"][3], name="Must_rollback")
            elif operation == "department_delete":
                await administration.delete_department(db, department_id=scenario["ids"][3])
            elif operation == "key_create":
                await api_keys.create_api_key(db, actor=member, request_id="fail-key", name="Must_rollback")
            else:
                await cli_auth.exchange_cli_auth_token(db, device_code)
        await original_commit()
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(APIKey)) == 0
        assert await db.scalar(select(func.count()).select_from(UserConfig)) == 0
        assert await db.scalar(select(func.count()).select_from(User)) == 3
        assert (await db.get(User, scenario["ids"][1])).username == "Member"
        assert AuthUtils.verify_password((await db.get(User, scenario["ids"][1])).password_hash, "FixturePassword123!")
        assert (await db.get(User, scenario["ids"][2])).department_id == scenario["ids"][3]
        assert (await db.get(Department, scenario["ids"][3])).name == "Other"
        if device_code:
            assert (await db.scalar(select(CLIAuthSession))).status == "approved"


async def test_deleted_actor_is_rechecked_by_usecase(scenario):
    """请求鉴权后的身份失效不能以陈旧 ORM 对象绕过写入授权。"""
    async with scenario["db"]() as db:
        actor = await db.get(User, scenario["ids"][2])
        async with scenario["db"]() as writer:
            current = await writer.get(User, actor.id)
            current.is_deleted = 1
            await writer.commit()
        with pytest.raises(PermissionDeniedError, match="身份已失效"):
            await users.create_user(db, current_user=actor, username="Must reject", password="FixturePassword123!")
    async with scenario["db"]() as db:
        assert await db.scalar(select(User).where(User.username == "Must reject")) is None


async def test_concurrent_config_save_keeps_one_row(scenario):
    """并发首次保存依靠数据库唯一约束，两个请求均可完成。"""

    async def save(enabled):
        """使用独立事务保存同一用户。"""
        async with scenario["db"]() as db:
            return await preferences.save_user_config(db, uid="member", schema=UserConfigSchema(enable_memory=enabled))

    results = await asyncio.gather(save(False), save(True))
    assert {r.schema.enable_memory for r in results} == {False, True}
    async with scenario["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(UserConfig)) == 1


async def test_schedule_cleanup_failure_rolls_back_user_and_key(scenario, monkeypatch):
    """跨域清理失败时账号和凭据不能提前提交。"""
    async with scenario["db"]() as db:
        actor = await db.get(User, scenario["ids"][0])
        member = await db.get(User, scenario["ids"][1])
        result = await api_keys.create_api_key(db, actor=member, request_id="delete-key", name="before deletion")

        async def fail_cleanup(db, *, uid):
            """真实身份写入已 flush 后中断后续域操作。"""
            raise RuntimeError("fixture cleanup failure")

        monkeypatch.setattr(users, "delete_user_schedules", fail_cleanup)
        with pytest.raises(RuntimeError, match="fixture cleanup failure"):
            await users.delete_user(db, current_user=actor, user_id=member.id)
        await db.commit()
    async with scenario["db"]() as db:
        assert (await db.get(User, scenario["ids"][1])).is_deleted == 0
        key = await db.get(APIKey, result["api_key"]["id"])
        assert key.is_enabled and key.revoked_at is None


async def test_http_avatar_persists_reference_and_uploaded_bytes(scenario):
    """真实头像上传同时回读 PostgreSQL 引用和 MinIO 字节。"""
    import base64
    from urllib.parse import urlsplit

    from yuxi.infrastructure.minio import get_minio_client

    content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")
    object_name = None
    try:
        response = await scenario["http"].post(
            "/api/auth/upload-avatar",
            headers=scenario["member"],
            files={"file": ("fixture.png", content, "image/png")},
        )
        assert response.status_code == 200, response.text
        url = response.json()["avatar_url"]
        object_name = urlsplit(url).path.split("/public/", 1)[1]
        async with scenario["db"]() as db:
            assert (await db.get(User, scenario["ids"][1])).avatar == url
        assert await get_minio_client().adownload_file("public", object_name, max_bytes=1024) == content
        invalid = await scenario["http"].post(
            "/api/auth/upload-avatar",
            headers=scenario["member"],
            files={"file": ("fake.png", b"not an image", "image/png")},
        )
        assert invalid.status_code == 400
        async with scenario["db"]() as db:
            assert (await db.get(User, scenario["ids"][1])).avatar == url
    finally:
        if object_name:
            await get_minio_client().adelete_file("public", object_name)
