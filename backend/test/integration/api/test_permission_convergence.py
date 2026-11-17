"""权限收敛的真实 HTTP 与持久状态验收。"""

import asyncio
import asyncpg
import os
import uuid

import pytest
from test.support.permission_convergence import SHARED, create_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_department_member_update_rechecks_after_concurrent_promotion(actors):
    """另一事务晋升期间的成员更新，等待锁后必须按最新角色拒绝。"""
    client, db = actors["client"], actors["db"]
    admin, member = (actors["identities"][key] for key in ("admin0", "user0"))
    transaction = db.transaction()
    await transaction.start()
    pending = None
    try:
        await db.execute("UPDATE users SET role='admin' WHERE id=$1", member["id"])
        pending = asyncio.create_task(
            client.put(f"/api/auth/users/{member['id']}", headers=admin["headers"], json={"username": "forbidden_race_edit"})
        )
        for _ in range(100):
            await db.execute("SELECT pg_stat_clear_snapshot()")
            if await db.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%users%' "
                "AND pid<>pg_backend_pid())"
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("成员更新没有到达受控的数据库锁")
        await transaction.commit()
        response = await asyncio.wait_for(pending, 5)
        assert response.status_code == 403, response.text
        state = await db.fetchrow("SELECT role,username FROM users WHERE id=$1", member["id"])
        assert state["role"] == "admin" and state["username"] == member["username"]
    finally:
        if transaction._state.name == "STARTED":
            await transaction.rollback()
        if pending is not None and not pending.done():
            await pending


async def test_shared_transfer_requires_superadmin_and_active_admin_recipient(actors):
    """共享配置不能改所有者，专用转移路径保留定义身份并拒绝普通接收者。"""
    client, db = actors["client"], actors["db"]
    first, second, user = (actors["identities"][key] for key in ("admin0", "admin1", "user0"))
    agent = await create_agent(actors, first, visibility="shared", share_config={"version": 2, "read_scope": None, "manage_scope": None})
    path = f"/api/system/resources/agent/{agent['slug']}/owner"
    for headers, recipient, status in [(first["headers"], second["uid"], 403), (actors["root"], user["uid"], 422)]:
        response = await client.put(path, headers=headers, json={"owner_uid": recipient})
        assert response.status_code == status, response.text
        assert await db.fetchval("SELECT created_by FROM agents WHERE slug=$1", agent["slug"]) == first["uid"]
    response = await client.put(path, headers=actors["root"], json={"owner_uid": second["uid"]})
    assert response.status_code == 200, response.text
    state = await db.fetchrow("SELECT id,created_by,visibility FROM agents WHERE slug=$1", agent["slug"])
    assert state["id"] == agent["id"] and state["created_by"] == second["uid"] and state["visibility"] == "shared"
    assert (await client.get(f"/api/agent/{agent['slug']}", headers=first["headers"])).status_code == 404
    response = await client.get(f"/api/agent/{agent['slug']}", headers=second["headers"])
    assert response.status_code == 200 and response.json()["agent"]["can_manage"]


async def test_ordinary_shared_knowledge_and_skill_creation_is_rejected_without_records(actors):
    """普通用户的共享建设写入失败后没有数据库定义。"""
    client, db = actors["client"], actors["db"]
    user = actors["identities"]["user0"]
    name = f"permission-rejected-{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/api/knowledge/knowledge-bases",
        headers=user["headers"],
        json={"name": name, "description": "forbidden", "share_config": SHARED},
    )
    assert response.status_code == 403, response.text
    assert await db.fetchval("SELECT count(*) FROM knowledge_bases WHERE name=$1", name) == 0
    prepared = await client.post(
        "/api/skills/import/prepare",
        headers=user["headers"],
        files={"file": ("SKILL.md", f"---\nname: {name}\ndescription: rejected\n---\nTest\n".encode(), "text/markdown")},
    )
    assert prepared.status_code == 200, prepared.text
    draft = prepared.json()["data"]["draft_id"]
    try:
        response = await client.post(
            f"/api/skills/install-drafts/{draft}/confirm",
            headers=user["headers"],
            json={"slugs": [name], "share_config": SHARED},
        )
        assert response.status_code in {403, 422}, response.text
        assert await db.fetchval("SELECT count(*) FROM skills WHERE slug=$1", name) == 0
    finally:
        await client.delete(f"/api/skills/install-drafts/{draft}", headers=user["headers"])


async def test_private_agent_crud_isolation_governance_and_app_default(actors):
    client, db = actors["client"], actors["db"]
    owner = actors["identities"]["user0"]
    agent = await create_agent(actors, owner, config_json={"context": {"system_prompt": "private prompt"}})
    slug = agent["slug"]
    assert agent["visibility"] == "private" and agent["can_manage"] and agent["can_run"]
    assert await db.fetchval("SELECT visibility FROM agents WHERE slug=$1", slug) == "private"
    for actor in (actors["identities"]["user1"], actors["identities"]["admin0"], actors["identities"]["admin1"]):
        listing = await client.get("/api/agent", headers=actor["headers"])
        assert slug not in {item["slug"] for item in listing.json()["agents"]}
        for method in (client.get, client.put, client.delete):
            kwargs = {"json": {"name": "forged"}} if method == client.put else {}
            response = await method(f"/api/agent/{slug}", headers=actor["headers"], **kwargs)
            assert response.status_code == 404, response.text
        response = await client.post(
            "/api/v1/agents/sessions",
            headers={**actor["headers"], "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug},
        )
        assert response.status_code in {403, 404}, response.text
    response = await client.put(f"/api/agent/{slug}", headers=owner["headers"], json={"name": "Owner edit"})
    assert response.status_code == 200, response.text
    assert await db.fetchval("SELECT name FROM agents WHERE slug=$1", slug) == "Owner edit"
    governed = await client.get(f"/api/agent/{slug}", headers=actors["root"])
    assert governed.status_code == 200 and governed.json()["agent"]["can_manage"]
    assert not governed.json()["agent"]["can_run"]
    run = await client.post(
        "/api/v1/agents/sessions",
        headers={**actors["root"], "Idempotency-Key": str(uuid.uuid4())},
        json={"agent_id": slug},
    )
    assert run.status_code in {403, 404}, run.text
    personal_key = await client.post(
        "/api/user/apikey/",
        headers=owner["headers"],
        json={"name": "Personal private Agent", "request_id": str(uuid.uuid4()), "access_level": "agents"},
    )
    assert personal_key.status_code == 200, personal_key.text
    actors["keys"].append(personal_key.json()["api_key"]["id"])
    personal_headers = {"Authorization": f"Bearer {personal_key.json()['secret']}"}
    personal_listing = await client.get("/api/v1/agents", headers=personal_headers)
    assert personal_listing.status_code == 200 and slug in {item["id"] for item in personal_listing.json()["data"]}
    personal_detail = await client.get(f"/api/v1/agents/{slug}", headers=personal_headers)
    assert personal_detail.status_code == 200 and personal_detail.json()["id"] == slug

    key = await client.post(
        "/api/user/apikey/",
        headers=owner["headers"],
        json={
            "name": "Permission APP",
            "request_id": str(uuid.uuid4()),
            "access_level": "agents",
            "app_id": uuid.uuid4().hex,
        },
    )
    assert key.status_code == 200, key.text
    actors["keys"].append(key.json()["api_key"]["id"])
    for identity in (None, "explicit-end-user"):
        headers = {"Authorization": f"Bearer {key.json()['secret']}"}
        if identity:
            headers["X-End-User-Id"] = identity
        listing = await client.get("/api/v1/agents", headers=headers)
        assert listing.status_code == 200 and slug not in {item["id"] for item in listing.json()["data"]}
        assert (await client.get(f"/api/v1/agents/{slug}", headers=headers)).status_code == 404
        run = await client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug},
        )
        assert run.status_code in {403, 404}, run.text
    deleted = await client.delete(f"/api/agent/{slug}", headers=owner["headers"])
    assert deleted.status_code == 200
    assert await db.fetchval("SELECT id FROM agents WHERE slug=$1", slug) is None


async def test_private_definition_sharing_rejects_unauthorized_or_implicit_grants(actors):
    """发布需要管理权、管理员身份与明确授权，失败保持原始私有记录。"""
    client, db = actors["client"], actors["db"]
    user = actors["identities"]["user0"]
    agent = await create_agent(actors, user)
    path = f"/api/agent/{agent['slug']}"
    for payload in (
        {"visibility": "shared"},
        {"share_config": SHARED},
        {"created_by": "forged"},
        {"config_json": {"context": {"max_execution_steps": 999}}},
    ):
        response = await client.post("/api/agent", headers=user["headers"], json={"name": "forged", **payload})
        assert response.status_code == 422, response.text
    for payload in (
        {"share_config": SHARED},
        {"created_by": "forged"},
        {"is_subagent": True},
    ):
        response = await client.put(path, headers=user["headers"], json=payload)
        assert response.status_code == 422, response.text
    response = await client.put(path, headers=user["headers"], json={"visibility": "shared", "share_config": SHARED})
    assert response.status_code == 403 and "仅管理员" in response.text, response.text
    response = await client.put(
        path, headers=actors["identities"]["admin0"]["headers"], json={"visibility": "shared", "share_config": SHARED}
    )
    assert response.status_code == 404, response.text
    for payload in ({"visibility": "shared"}, {"share_config": SHARED}):
        response = await client.put(path, headers=actors["root"], json=payload)
        assert response.status_code == 422, response.text
    for headers in (user["headers"], actors["root"]):
        response = await client.post(path + "/publish", headers=headers, json={"share_config": SHARED})
        assert response.status_code == 404, response.text
    assert await db.fetchval("SELECT visibility FROM agents WHERE slug=$1", agent["slug"]) == "private"
    row = await db.fetchrow("SELECT id, created_by, visibility FROM agents WHERE slug=$1", agent["slug"])
    assert row["id"] == agent["id"] and row["created_by"] == user["uid"] and row["visibility"] == "private"
    detail = await client.get(path, headers=user["headers"])
    assert "can_publish" not in detail.json()["agent"]
    assert detail.json()["agent"]["can_share"] is False


@pytest.mark.parametrize("publisher", ["admin", "superadmin"])
async def test_share_private_agent_preserves_identity_owner_and_config(actors, publisher):
    """管理员共享自己的定义，系统管理员可治理共享他人的私有定义。"""
    client, db = actors["client"], actors["db"]
    owner = actors["identities"]["admin0" if publisher == "admin" else "user0"]
    headers = owner["headers"] if publisher == "admin" else actors["root"]
    agent = await create_agent(actors, owner, config_json={"context": {"system_prompt": "retain original role"}})
    path = f"/api/agent/{agent['slug']}"
    before = await db.fetchrow("SELECT id,slug,created_by,config_json,share_config FROM agents WHERE slug=$1", agent["slug"])
    detail = await client.get(path, headers=headers)
    assert detail.json()["agent"]["can_share"] is True
    for invalid in (
        {"visibility": "shared"},
        {
            "visibility": "shared",
            "share_config": {"version": 2, "read_scope": None, "manage_scope": {"access_level": "global"}},
        },
    ):
        denied = await client.put(path, headers=headers, json=invalid)
        assert denied.status_code == 422, denied.text
        unchanged = await db.fetchrow("SELECT visibility,share_config FROM agents WHERE slug=$1", agent["slug"])
        assert unchanged["visibility"] == "private" and unchanged["share_config"] == before["share_config"]

    shared = await client.put(path, headers=headers, json={"visibility": "shared", "share_config": SHARED})
    assert shared.status_code == 200, shared.text
    after = await db.fetchrow("SELECT id,slug,created_by,config_json,visibility FROM agents WHERE slug=$1", agent["slug"])
    assert after["visibility"] == "shared"
    assert {key: after[key] for key in before.keys() if key != "share_config"} == {
        key: before[key] for key in before.keys() if key != "share_config"
    }
    assert await db.fetchval("SELECT count(*) FROM agents WHERE id=$1", before["id"]) == 1
    reader = actors["identities"]["user1"]
    readable = await client.get(path, headers=reader["headers"])
    assert readable.status_code == 200, readable.text
    assert readable.json()["agent"]["can_run"] is True
    assert readable.json()["agent"]["can_manage"] is False
    reverse = await client.put(path, headers=headers, json={"visibility": "private"})
    assert reverse.status_code == 422, reverse.text
    assert await db.fetchval("SELECT visibility FROM agents WHERE id=$1", before["id"]) == "shared"


async def test_shared_grants_role_ceiling_and_system_configuration(actors):
    client, db = actors["client"], actors["db"]
    owner, manager, user = (actors["identities"][name] for name in ("admin0", "admin1", "user1"))
    grants = {**SHARED, "manage_scope": {"access_level": "global"}}
    agent = await create_agent(actors, owner, visibility="shared", share_config=grants)
    path = f"/api/agent/{agent['slug']}"
    readable = await client.get(path, headers=user["headers"])
    assert readable.status_code == 200 and not readable.json()["agent"]["can_manage"]
    assert (await client.put(path, headers=user["headers"], json={"name": "forged"})).status_code == 403
    valid = {**SHARED, "manage_scope": {"access_level": "user", "user_uids": [manager["uid"]]}}
    response = await client.put(path, headers=manager["headers"], json={"share_config": valid})
    assert response.status_code == 200, response.text
    saved = await db.fetchval("SELECT share_config FROM agents WHERE slug=$1", agent["slug"])
    for invalid in (
        {"version": 2, "read_scope": None, "manage_scope": {"access_level": "global"}},
        {
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [owner["department_id"]]},
            "manage_scope": {"access_level": "user", "user_uids": [owner["uid"]]},
        },
        {**SHARED, "manage_scope": {"access_level": "user", "user_uids": [user["uid"]]}},
    ):
        response = await client.put(path, headers=owner["headers"], json={"share_config": invalid})
        assert response.status_code == 422, response.text
        assert await db.fetchval("SELECT share_config FROM agents WHERE slug=$1", agent["slug"]) == saved
    for route, payload in (
        ("/api/system/mcp-servers", {"slug": "forged"}),
        ("/api/system/model-providers", {"provider_id": "forged"}),
        ("/api/system/config/update", {}),
    ):
        for actor in (user, manager):
            response = await client.post(route, headers=actor["headers"], json=payload)
            assert response.status_code == 403, response.text
    default = await client.put("/api/agent/default-chatbot", headers=manager["headers"], json={"name": "forged"})
    assert default.status_code == 403, default.text


async def test_department_admin_limits_role_promotion_and_deleted_owner_retention(actors):
    client, db = actors["client"], actors["db"]
    admin, user, other = (actors["identities"][name] for name in ("admin0", "user0", "user1"))
    for payload in ({"password": actors["password"]}, {"role": "admin"}, {"department_id": other["department_id"]}):
        response = await client.put(f"/api/auth/users/{user['id']}", headers=admin["headers"], json=payload)
        assert response.status_code == 403, response.text
    assert (await client.put(f"/api/auth/users/{other['id']}", headers=admin["headers"], json={"username": "forged"})).status_code == 403
    for old, target in (("admin", "user"), ("superadmin", "admin"), ("superadmin", "user")):
        if old == "superadmin":
            response = await client.put(f"/api/auth/users/{admin['id']}", headers=actors["root"], json={"role": "superadmin"})
            assert response.status_code == 200, response.text
        response = await client.put(f"/api/auth/users/{admin['id']}", headers=actors["root"], json={"role": target})
        assert response.status_code == 422, response.text
        assert await db.fetchval("SELECT role FROM users WHERE id=$1", admin["id"]) == old
    private = await create_agent(actors, user)
    shared = await create_agent(actors, admin, visibility="shared", share_config=SHARED)
    for identity in (user, admin):
        response = await client.delete(f"/api/auth/users/{identity['id']}", headers=actors["root"])
        assert response.status_code == 200, response.text
        assert await db.fetchval("SELECT is_deleted FROM users WHERE id=$1", identity["id"]) == 1
    for agent, visibility in ((private, "private"), (shared, "shared")):
        row = await db.fetchrow("SELECT visibility, created_by FROM agents WHERE slug=$1", agent["slug"])
        assert row["visibility"] == visibility and row["created_by"] == agent["created_by"]
    governed = await client.put(f"/api/agent/{private['slug']}", headers=actors["root"], json={"name": "governed"})
    assert governed.status_code == 200, governed.text
    transfer = await client.put(
        f"/api/system/resources/agent/{private['slug']}/owner",
        headers=actors["root"],
        json={"owner_uid": actors["identities"]["admin1"]["uid"]},
    )
    assert transfer.status_code == 422, transfer.text


async def test_superadmin_cannot_mint_owner_token_to_bypass_private_execution(actors):
    """治理私有定义不能经模拟登录取得所有者身份。"""
    owner = actors["identities"]["admin0"]
    agent = await create_agent(actors, owner)
    response = await actors["client"].post(f"/api/auth/impersonate/{owner['id']}", headers=actors["root"])
    assert response.status_code == 404
    row = await actors["db"].fetchrow("SELECT created_by,visibility FROM agents WHERE slug=$1", agent["slug"])
    assert row["created_by"] == owner["uid"] and row["visibility"] == "private"


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("revocation", ["department", "deleted"])
async def test_member_write_rechecks_actor_after_waiting_for_target(actors, operation, revocation):
    """等待成员锁时操作人的部门或账号变更，不能沿用鉴权时的旧身份写入。"""
    client, db = actors["client"], actors["db"]
    admin, other, member = (actors["identities"][key] for key in ("admin0", "admin1", "user0"))
    observer = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    transaction = db.transaction()
    await transaction.start()
    pending = None
    try:
        await db.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", member["id"])
        path = f"/api/auth/users/{member['id']}"
        pending = asyncio.create_task(
            client.put(path, headers=admin["headers"], json={"username": "forbidden_actor_race"})
            if operation == "update"
            else client.delete(path, headers=admin["headers"])
        )
        for _ in range(200):
            await observer.execute("SELECT pg_stat_clear_snapshot()")
            waiting = await observer.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid)) AND query LIKE '%users%')",
                db.get_server_pid(),
            )
            if waiting:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("成员操作未等待当前测试连接持有的行锁")
        if revocation == "department":
            await observer.execute("UPDATE users SET department_id=$1 WHERE id=$2", other["department_id"], admin["id"])
        else:
            await observer.execute("UPDATE users SET is_deleted=1 WHERE id=$1", admin["id"])
        await transaction.commit()
        response = await asyncio.wait_for(pending, 5)
        assert response.status_code == 403, response.text
        row = await observer.fetchrow("SELECT username,is_deleted FROM users WHERE id=$1", member["id"])
        assert row["username"] == member["username"] and row["is_deleted"] == 0
    finally:
        if transaction._state.name == "STARTED":
            await transaction.rollback()
        if pending is not None and not pending.done():
            await pending
        await observer.close()


async def test_each_skill_install_transaction_revalidates_live_managers(actors):
    """首项提交后管理者删除，第二项不能复用已释放锁的旧校验。"""
    import json
    import shutil
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from yuxi.modules.extensions.skills.shared import confirm_skill_install_draft
    from yuxi.infrastructure.runtime_settings import get_skill_data_dir
    from yuxi.modules.extensions.skills.install_draft import get_skill_drafts_root_dir
    from yuxi.modules.identity.models import User

    admin, manager = (actors["identities"][key] for key in ("admin0", "admin1"))
    slugs = [f"grant-batch-{index}-{uuid.uuid4().hex[:8]}" for index in range(2)]
    draft_ids = []
    for slug in slugs:
        response = await actors["client"].post(
            "/api/skills/import/prepare",
            headers=admin["headers"],
            files={
                "file": (
                    "SKILL.md",
                    f"---\nname: {slug}\ndescription: grant test\n---\n# {slug}\n".encode(),
                    "text/markdown",
                )
            },
        )
        assert response.status_code == 200, response.text
        draft_ids.append(response.json()["data"]["draft_id"])
    # 复用两个已校验的上传快照，组成远程批量安装消费的同一草稿输入协议。
    draft_dir, second_dir = (get_skill_drafts_root_dir() / draft_id for draft_id in draft_ids)
    metadata = json.loads((draft_dir / "metadata.json").read_text())
    second = json.loads((second_dir / "metadata.json").read_text())
    for item in second["items"]:
        shutil.move(second_dir / item["source_dir"], draft_dir / item["source_dir"])
    metadata["items"].extend(second["items"])
    (draft_dir / "metadata.json").write_text(json.dumps(metadata))
    draft_id = draft_ids[0]
    revoked = False

    class CommitThenRevoke(AsyncSession):
        """真实提交后通过独立连接撤销管理员。"""

        async def commit(self):
            """模拟两个独立安装事务之间的已提交身份变更。"""
            nonlocal revoked
            await super().commit()
            if not revoked:
                await actors["db"].execute("UPDATE users SET is_deleted=1 WHERE id=$1", manager["id"])
                revoked = True

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        sessions = async_sessionmaker(engine, class_=CommitThenRevoke, expire_on_commit=False)
        async with sessions() as db:
            operator = await db.scalar(select(User).where(User.uid == admin["uid"]))
            results = await confirm_skill_install_draft(
                db,
                draft_id=draft_id,
                slugs=slugs,
                operator=operator,
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global"},
                    "manage_scope": {"access_level": "user", "user_uids": [manager["uid"]]},
                },
            )
        rows = await actors["db"].fetch("SELECT slug, dir_path FROM skills WHERE slug=ANY($1::varchar[])", slugs)
        assert [row["slug"] for row in rows] == [slugs[0]]
        assert results[0]["success"] is True and results[1]["success"] is False
        assert "有效管理员" in results[1]["error"]
        root = get_skill_data_dir()
        assert slugs[0] in (root / rows[0]["dir_path"] / "SKILL.md").read_text()
        assert not (root / "packages" / slugs[1]).exists()
        assert await actors["db"].fetchval("SELECT is_deleted FROM users WHERE id=$1", manager["id"]) == 1
    finally:
        await engine.dispose()
        for directory in (draft_dir, second_dir):
            shutil.rmtree(directory, ignore_errors=True)
        for slug in slugs:
            response = await actors["client"].delete(f"/api/system/skills/{slug}", headers=actors["root"])
            assert response.status_code in {200, 404}, response.text


@pytest.mark.parametrize(
    "operation,revocation",
    [
        ("update", "department"),
        ("delete", "department"),
        ("update", "deleted"),
        ("delete", "deleted"),
        ("transfer", "deleted"),
        ("config", "department"),
    ],
)
async def test_agent_write_rechecks_actor_after_waiting_for_resource(actors, operation, revocation):
    """等待定义行锁期间身份撤销，不能使用请求开始时的旧身份写入。"""
    client, db = actors["client"], actors["db"]
    owner, actor = (actors["identities"][key] for key in ("admin0", "admin1"))
    await db.execute("UPDATE users SET department_id=$1 WHERE id=$2", owner["department_id"], actor["id"])
    if operation == "transfer":
        await db.execute("UPDATE users SET role='superadmin' WHERE id=$1", actor["id"])
    if operation == "config":
        agent = await create_agent(actors, actor)
    else:
        agent = await create_agent(
            actors,
            owner,
            visibility="shared",
            share_config={
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [owner["department_id"]]},
                "manage_scope": {"access_level": "department", "department_ids": [owner["department_id"]]},
            },
        )
    slug = agent["slug"]
    skill = f"actor-race-{uuid.uuid4().hex[:8]}" if operation == "config" else None
    if skill:
        import json

        await db.execute(
            "INSERT INTO skills (slug,name,description,source_type,dir_path,share_config,enabled,created_by,"
            "updated_by,created_at,updated_at) "
            "VALUES ($1,$1,'race','upload',$1,$2::jsonb,true,$3,$3,NOW(),NOW())",
            skill,
            json.dumps(
                {
                    "version": 2,
                    "read_scope": {"access_level": "department", "department_ids": [owner["department_id"]]},
                    "manage_scope": None,
                }
            ),
            owner["uid"],
        )
    before = await db.fetchrow("SELECT name,visibility,created_by,share_config,config_json FROM agents WHERE slug=$1", slug)
    observer = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    transaction = db.transaction()
    await transaction.start()
    pending = None
    try:
        await db.fetchrow("SELECT id FROM agents WHERE slug=$1 FOR UPDATE", slug)
        path = f"/api/agent/{slug}"
        if operation == "update":
            request = client.put(path, headers=actor["headers"], json={"name": "forbidden_actor_race"})
        elif operation == "config":
            request = client.put(path, headers=actor["headers"], json={"config_json": {"context": {"skills": [skill]}}})
        elif operation == "delete":
            request = client.delete(path, headers=actor["headers"])
        else:
            request = client.put(f"/api/system/resources/agent/{slug}/owner", headers=actor["headers"], json={"owner_uid": actor["uid"]})
        pending = asyncio.create_task(request)
        for _ in range(200):
            await observer.execute("SELECT pg_stat_clear_snapshot()")
            if await observer.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid)) AND query LIKE '%agents%')",
                db.get_server_pid(),
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("资源写入未等待当前测试连接持有的行锁")
        if revocation == "department":
            await observer.execute("UPDATE users SET department_id=$1 WHERE id=$2", actor["department_id"], actor["id"])
        else:
            await observer.execute("UPDATE users SET is_deleted=1 WHERE id=$1", actor["id"])
        await transaction.commit()
        response = await asyncio.wait_for(pending, 5)
        after = await observer.fetchrow("SELECT name,visibility,created_by,share_config,config_json FROM agents WHERE slug=$1", slug)
        assert response.status_code == (422 if operation == "config" else 403) and after == before, (
            response.status_code,
            response.text,
            before,
            after,
        )
    finally:
        if transaction._state.name == "STARTED":
            await transaction.rollback()
        if pending is not None and not pending.done():
            await pending
        if skill:
            await observer.execute("DELETE FROM skills WHERE slug=$1", skill)
        await observer.close()
