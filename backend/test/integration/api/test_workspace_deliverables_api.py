"""真实 HTTP、PostgreSQL 与 no-follow 文件边界验证个人成果。"""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from datetime import timedelta
from pathlib import PurePosixPath

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.api.dependencies.auth import get_db
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.workspace.deliverables import deliverables
from yuxi.infrastructure.postgres.base import BusinessBase
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Deliverable, Project
from yuxi.modules.workspace.paths import ensure_bound_user_workdir, global_user_data_dir, user_workspace_dir
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH
from yuxi.modules.workspace.services.deliverables import save_run_deliverables
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import ContentTooLargeError, DomainError, PermissionDeniedError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
BASE = "/api/workspace/deliverables"


@pytest_asyncio.fixture
async def deliverables_env(monkeypatch, tmp_path):
    """创建独立 Schema、Workspace 和真实认证 HTTP，不触碰业务数据。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "skill-projections"))
    monkeypatch.setenv("JWT_SECRET_KEY", "deliverables-integration-jwt-key-at-least-32-characters")
    schema = f"pytest_deliverables_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    load_models()
    async with engine.begin() as connection:
        await connection.run_sync(BusinessBase.metadata.create_all)
    async with factory() as db:
        department = Department(name="Deliverables test department")
        db.add(department)
        await db.flush()
        users = [
            User(uid=name, username=name, password_hash="unused", role="user", department_id=department.id) for name in ("alice", "bob")
        ]
        db.add_all(users)
        await db.flush()
        headers = {
            user.uid: {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id), 'auth_method': 'password'})}"}
            for user in users
        }
        for index, (uid, app_id) in enumerate((("alice", None), ("alice", "external-app"), ("bob", None))):
            key = f"scope-{index}"
            workdir_path = f"projects/20261116/{index:06x}"
            db.add(Project(id=key, uid=uid, selection_status="implicit", workdir_path=workdir_path, directory_mode="managed"))
            await db.flush()
            thread = Session(thread_id=key, uid=uid, app_id=app_id, project_id=key, agent_id="fixture-agent", title=f"Source {key}")
            db.add(thread)
            await db.flush()
            turn = AgentTurn(id=key, thread_id=key, uid=uid, app_id=app_id, status="running")
            db.add(turn)
            await db.flush()
            db.add(
                AgentRun(
                    id=key,
                    thread_id=key,
                    runtime_scope_id=key,
                    turn_id=key,
                    uid=uid,
                    app_id=app_id,
                    session_record_id=thread.id,
                    agent_slug="fixture-agent",
                    status="running",
                    worker_id="worker:attempt",
                    lease_expires_at=utc_now() + timedelta(minutes=5),
                )
            )
            await db.flush()
            turn.current_run_id = key
            ensure_bound_user_workdir(uid, workdir_path)
        await db.commit()

    async def isolated_db():
        """请求会话只连接本例独立 Schema，认证保持生产实现。"""
        async with factory() as db:
            yield db

    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(deliverables, prefix="/api")
    app.dependency_overrides[get_db] = isolated_db
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    listener = config.bind_socket()
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=10) as client:
            yield {"client": client, "db": factory, "headers": headers}
    finally:
        server.should_exit = True
        await task
        listener.close()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


async def publish(env, *, name="report.md", content=b"# Original report\n", scope=0, operation="call-publish", **overrides):
    """通过生产成果用例发布真实文件，独立构造预期字节。"""
    uid = str(overrides.pop("uid", "bob" if scope == 2 else "alice"))
    source_scope = f"/projects/20261116/{scope:06x}/{name}"
    Workspace(uid).upload_authorized_file_from_stream(source_scope, io.BytesIO(content), max_bytes=None)
    arguments = {
        "uid": uid,
        "thread_id": f"scope-{scope}",
        "run_id": f"scope-{scope}",
        "worker_id": "worker:attempt",
        "operation_id": operation,
        "paths": [f"{WORKSPACE_PATH}{source_scope}"],
        **overrides,
    }
    async with env["db"]() as db:
        [item] = await save_run_deliverables(db=db, **arguments)
        return item


async def test_publication_is_idempotent_and_snapshot_survives_source_changes(deliverables_env):
    """同次发布重试只有一行，新的工具发布保留独立版本和同名文件。"""
    env = deliverables_env
    first = await publish(env)
    source = user_workspace_dir("alice") / "projects/20261116/000000/report.md"
    source.write_bytes(b"# Changed\n")
    async with env["db"]() as db:
        [replayed] = await save_run_deliverables(
            db=db,
            uid="alice",
            thread_id="scope-0",
            run_id="scope-0",
            worker_id="worker:attempt",
            operation_id="call-publish",
            paths=[first.source_path, first.source_path],
        )
    assert replayed.id == first.id
    second = await publish(env, content=b"# Second version\n", operation="call-publish-new")
    source.unlink()
    assert first.id != second.id and first.saved_path != second.saved_path
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(Deliverable)) == 2
        thread = await db.scalar(select(Session).where(Session.thread_id == "scope-0"))
        thread.status = "archived"
        await db.commit()
    for item, expected in ((first, b"# Original report\n"), (second, b"# Second version\n")):
        downloaded = await env["client"].get(f"{BASE}/{item.id}/download", headers=env["headers"]["alice"])
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == expected
        assert "attachment" in downloaded.headers["content-disposition"]
        assert Workspace("alice", root_path=global_user_data_dir("alice")).read_authorized_file(item.saved_path, 1024) == expected
    preview = await env["client"].get(f"{BASE}/{first.id}/preview", headers=env["headers"]["alice"])
    assert preview.status_code == 200, preview.text
    assert preview.json()["content"] == "# Original report\n" and preview.json()["supported"] is True


async def test_concurrent_replay_reuses_one_snapshot_and_cleans_loser(deliverables_env):
    """同一发布身份并发执行只保留一条记录和一个副本。"""
    env = deliverables_env
    source_scope = "/projects/20261116/000000/race.md"
    Workspace("alice").upload_authorized_file_from_stream(source_scope, io.BytesIO(b"race"), max_bytes=None)
    arguments = {
        "uid": "alice",
        "thread_id": "scope-0",
        "run_id": "scope-0",
        "worker_id": "worker:attempt",
        "operation_id": "call-race",
        "paths": [f"{WORKSPACE_PATH}{source_scope}"],
    }

    async def publish_once():
        async with env["db"]() as db:
            return (await save_run_deliverables(db=db, **arguments))[0]

    first, second = await asyncio.gather(publish_once(), publish_once())
    assert first.id == second.id
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(Deliverable)) == 1
    snapshot_root = global_user_data_dir("alice") / "deliverables" / first.id
    assert [path for path in snapshot_root.rglob("*") if path.is_file()] == [
        snapshot_root / PurePosixPath(first.saved_path).parent.name / first.name
    ]


async def test_publication_rejects_excessive_file_count_before_copy(deliverables_env):
    """模型一次提交过多路径时在文件复制前拒绝并保持空目录。"""
    env = deliverables_env
    paths = [f"{WORKSPACE_PATH}/projects/20261116/000000/file-{index}.md" for index in range(101)]
    async with env["db"]() as db:
        with pytest.raises(ContentTooLargeError, match="最多交付"):
            await save_run_deliverables(
                db=db,
                uid="alice",
                thread_id="scope-0",
                run_id="scope-0",
                worker_id="worker:attempt",
                operation_id="call-too-many",
                paths=paths,
            )
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(Deliverable)) == 0


async def test_http_list_filters_pagination_and_uid_app_visibility(deliverables_env):
    """列表、所有文件入口和搜索都执行用户及 Web APP 边界。"""
    env = deliverables_env
    own = await publish(env, name="report_100%.md")
    image = await publish(env, name="chart.png", content=b"\x89PNG\r\n\x1a\nimage", operation="call-image")
    await publish(env, name="clip.mp4", content=b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", operation="call-video")
    await publish(env, name="voice.mp3", content=b"ID3audio", operation="call-audio")
    await publish(env, name="archive.zip", content=b"PKfixture", operation="call-other")
    external = await publish(env, name="external.md", scope=1)
    other = await publish(env, name="other.md", scope=2)
    client, headers = env["client"], env["headers"]["alice"]
    for category, names in (
        ("document", {own.name}),
        ("image", {image.name}),
        ("video", {"clip.mp4"}),
        ("audio", {"voice.mp3"}),
        ("other", {"archive.zip"}),
    ):
        response = await client.get(BASE, headers=headers, params={"file_type": category})
        assert response.status_code == 200, response.text
        assert {item["name"] for item in response.json()["items"]} == names
        assert response.json()["total"] == 1
    pages = [await client.get(BASE, headers=headers, params={"page": page, "page_size": 2}) for page in (1, 2, 3)]
    assert all(response.json()["total"] == 5 for response in pages)
    items = [item for response in pages for item in response.json()["items"]]
    assert len({item["id"] for item in items}) == 5
    assert all(item["thread_title"] == "Source scope-0" and item["run_id"] == "scope-0" for item in items)
    searched = await client.get(BASE, headers=headers, params={"query": "100%"})
    assert [item["id"] for item in searched.json()["items"]] == [own.id]
    assert (await client.get(BASE, headers=headers, params={"query": "missing"})).json()["total"] == 0
    for item in (external, other):
        for endpoint in ("preview", "download", "content"):
            denied = await client.get(f"{BASE}/{item.id}/{endpoint}", headers=headers)
            assert denied.status_code == 404, denied.text
    for path in (BASE, f"{BASE}/{own.id}/download", f"{BASE}/{own.id}/content", f"{BASE}/{own.id}/preview"):
        assert (await client.get(path)).status_code == 401
    for params in ({"page": 0}, {"page_size": 101}, {"file_type": "unknown"}):
        assert (await client.get(BASE, headers=headers, params=params)).status_code == 422

    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    async with env["db"]() as db:
        alice = await db.scalar(select(User).where(User.uid == "alice"))
        db.add(
            APIKey(
                key_hash=key_hash,
                key_prefix=key_prefix,
                name="deliverables app key",
                access_level="full",
                app_id="external-app",
                user_id=alice.id,
                department_id=alice.department_id,
                created_by="alice",
            )
        )
        await db.commit()
    api_response = await client.get(BASE, headers={"Authorization": f"Bearer {secret}"})
    assert api_response.status_code == 403, api_response.text


async def test_publish_rejects_wrong_owner_and_source_symlink_without_metadata(deliverables_env):
    """伪造运行用户、过期执行 owner 和链接源文件都不能发布成果。"""
    env = deliverables_env
    with pytest.raises(PermissionDeniedError, match="来源对话"):
        await publish(env, uid="bob")
    with pytest.raises(ValueError, match="lease owner"):
        await publish(env, worker_id="other:attempt")
    source = user_workspace_dir("alice") / "projects/20261116/000000/report.md"
    link = source.with_name("linked.md")
    link.symlink_to(source)
    async with env["db"]() as db:
        with pytest.raises(PermissionDeniedError, match="artifact access denied"):
            await save_run_deliverables(
                db=db,
                uid="alice",
                thread_id="scope-0",
                run_id="scope-0",
                worker_id="worker:attempt",
                operation_id="call-link",
                paths=[f"{WORKSPACE_PATH}/projects/20261116/000000/linked.md"],
            )
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(Deliverable)) == 0
    response = await env["client"].get(BASE, headers=env["headers"]["alice"])
    assert response.json()["items"] == []


async def test_snapshot_symlink_and_missing_file_fail_explicitly(deliverables_env):
    """已保存记录不能通过被替换的链接读取文件，也不伪装副本丢失为成功。"""
    env = deliverables_env
    item = await publish(env)
    saved = global_user_data_dir("alice").joinpath(*PurePosixPath(item.saved_path).parts[1:])
    saved.unlink()
    saved.symlink_to(user_workspace_dir("alice") / "projects/20261116/000000/report.md")
    for endpoint in ("preview", "download", "content"):
        response = await env["client"].get(f"{BASE}/{item.id}/{endpoint}", headers=env["headers"]["alice"])
        assert response.status_code == 403, response.text
    saved.unlink()
    for endpoint in ("preview", "download", "content"):
        response = await env["client"].get(f"{BASE}/{item.id}/{endpoint}", headers=env["headers"]["alice"])
        assert response.status_code == 404, response.text


async def test_shared_skill_source_rechecks_current_grant_and_retains_existing_snapshot(deliverables_env):
    """共享文件撤权后不能再次归集，已授权时形成的个人副本仍可下载。"""
    env = deliverables_env
    async with env["db"]() as db:
        db.add(
            Skill(
                slug="fixture-skill",
                name="Fixture skill",
                description="Shared file authorization fixture",
                source_type="upload",
                dir_path="shared/fixture-skill",
                enabled=True,
                created_by="bob",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["alice", "bob"]},
                    "manage_scope": None,
                },
            )
        )
        await db.commit()
    projection = get_user_skills_root_dir("alice") / "fixture-skill"
    projection.mkdir(parents=True)
    (projection / "guide.md").write_bytes(b"# Shared guide\n")
    async with env["db"]() as db:
        [item] = await save_run_deliverables(
            db=db,
            uid="alice",
            thread_id="scope-0",
            run_id="scope-0",
            worker_id="worker:attempt",
            operation_id="call-authorized-skill",
            paths=["/home/user/skills/fixture-skill/guide.md"],
        )
        skill = await db.scalar(select(Skill).where(Skill.slug == "fixture-skill"))
        skill.share_config = {
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["bob"]},
            "manage_scope": None,
        }
        await db.commit()
    async with env["db"]() as db:
        with pytest.raises(PermissionDeniedError, match="artifact access denied"):
            await save_run_deliverables(
                db=db,
                uid="alice",
                thread_id="scope-0",
                run_id="scope-0",
                worker_id="worker:attempt",
                operation_id="call-revoked-skill",
                paths=["/home/user/skills/fixture-skill/guide.md"],
            )
    async with env["db"]() as db:
        assert await db.scalar(select(func.count()).select_from(Deliverable)) == 1
    downloaded = await env["client"].get(f"{BASE}/{item.id}/download", headers=env["headers"]["alice"])
    assert downloaded.status_code == 200 and downloaded.content == b"# Shared guide\n"
