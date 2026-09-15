"""真实PostgreSQL、HTTP和no-follow文件验证个人回收生命周期。"""

import asyncio
import os
import uuid
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from server.routers.personal_trash_router import personal_trash
from server.routers.workspace_router import workspace
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services import personal_trash_service as service
from yuxi.services.attachment_service import delete_thread_attachment_view, list_thread_attachments_view
from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Base, Conversation, Message, Project, User
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.workspace import paths as workspace_paths
from yuxi.workspace import trash as trash_files

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """仅使用显式允许的独立数据库，每项仅清理自己的uid。"""
    if os.environ.get("TEST_ALLOW_PERSONAL_TRASH_DB") != "1":
        pytest.skip("Set TEST_ALLOW_PERSONAL_TRASH_DB=1 for an isolated PostgreSQL database")
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uid = "trash-" + uuid.uuid4().hex
    outsider = "outsider-" + uuid.uuid4().hex
    user = User(uid=uid, username=uid, role="user", password_hash="fixture")
    monkeypatch.setattr(workspace_paths, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(trash_files, "get_user_data_dir", lambda: tmp_path)
    workspace_paths.ensure_user_workspace(uid)
    root = workspace_paths.user_workspace_dir(uid)
    app = FastAPI()
    app.include_router(workspace, prefix="/api")
    app.include_router(personal_trash, prefix="/api")

    async def database():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_required_user] = lambda: user
    async with sessions() as db:
        db.add(user)
        await db.commit()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            yield sessions, uid, outsider, root, tmp_path, client, app, user
    finally:
        async with sessions() as db:
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.uid == uid))
            await db.execute(delete(AgentRun).where(AgentRun.uid == uid))
            await db.execute(
                delete(Message).where(
                    Message.conversation_id.in_(select(Conversation.id).where(Conversation.uid == uid))
                )
            )
            await db.execute(delete(Conversation).where(Conversation.uid == uid))
            await db.execute(delete(Project).where(Project.uid == uid))
            await db.execute(delete(PersonalTrashEntry).where(PersonalTrashEntry.uid == uid))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


async def test_http_delete_read_download_restore_and_owner_isolation(env):
    """回收字节存在但原API不可读，跨用户回收站和恢复均隔离。"""
    sessions, uid, outsider, root, data, client, app, user = env
    (root / "proof.txt").write_bytes(b"owned bytes")
    response = await client.delete("/api/workspace/file", params={"path": "/proof.txt"})
    assert response.status_code == 200, response.text
    entry = response.json()["trash"]
    assert entry["state"] == "trashed"
    assert not (root / "proof.txt").exists()
    assert (data / "trash" / uid / entry["id"] / "0").read_bytes() == b"owned bytes"
    assert (await client.get("/api/workspace/file", params={"path": "/proof.txt"})).status_code == 404
    assert (await client.get("/api/workspace/download", params={"path": "/proof.txt"})).status_code == 404
    listed = (await client.get("/api/workspace/tree", params={"path": "/"})).json()
    assert all(row["name"] != "proof.txt" for row in listed["entries"])
    app.dependency_overrides[get_required_user] = lambda: User(uid=outsider, role="superadmin")
    assert (await client.get("/api/personal-trash")).json() == {"items": [], "total": 0}
    assert (await client.post(f"/api/personal-trash/{entry['id']}/restore")).status_code == 404
    app.dependency_overrides[get_required_user] = lambda: user
    restored = await client.post(f"/api/personal-trash/{entry['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["state"] == "restored"
    assert (root / "proof.txt").read_bytes() == b"owned bytes"
    assert (await client.get("/api/personal-trash")).json()["total"] == 0


async def test_restore_conflict_never_overwrites_and_expiry_rejects_restore(env):
    """同名的新字节保留；到期后只清理隔离对象。"""
    sessions, uid, _, root, data, client, *_ = env
    (root / "same.txt").write_bytes(b"original")
    entry = (await client.delete("/api/workspace/file", params={"path": "/same.txt"})).json()["trash"]
    (root / "same.txt").write_bytes(b"new occupant")
    response = await client.post(f"/api/personal-trash/{entry['id']}/restore")
    assert response.status_code == 409
    assert (root / "same.txt").read_bytes() == b"new occupant"
    async with sessions() as db:
        row = await db.get(PersonalTrashEntry, entry["id"])
        assert row.state == "trashed"
        row.purge_after = utc_now_naive() - timedelta(seconds=1)
        await db.commit()
    assert (await client.post(f"/api/personal-trash/{entry['id']}/restore")).status_code == 409
    async with sessions() as db:
        result = await service.process_personal_entry(db=db, uid=uid, entry_id=entry["id"])
        assert result["state"] == "purged"
    assert not (data / "trash" / uid / entry["id"] / "0").exists()
    assert (root / "same.txt").read_bytes() == b"new occupant"
    async with sessions() as db:
        assert "/same.txt" in await service.list_deleted_user_paths(db, uid)


async def test_interrupted_move_restarts_from_real_inode_and_blocks_reads(env, monkeypatch):
    """移动后数据库完成前中断：新Session恢复同一对象，pending期间HTTP拒绝读取。"""
    sessions, uid, _, root, data, client, *_ = env
    (root / "fault.txt").write_bytes(b"durable")
    original = service.transition_files

    def crash_after_move(*args):
        original(*args)
        raise OSError("injected after rename")

    monkeypatch.setattr(service, "transition_files", crash_after_move)
    response = await client.delete("/api/workspace/file", params={"path": "/fault.txt"})
    assert response.status_code == 503
    listed = (await client.get("/api/personal-trash")).json()["items"]
    assert len(listed) == 1 and listed[0]["state"] == "pending_delete" and listed[0]["error"]
    assert (await client.get("/api/workspace/file", params={"path": "/fault.txt"})).status_code == 409
    monkeypatch.setattr(service, "transition_files", original)
    async with sessions() as fresh_db:
        result = await service.process_personal_entry(db=fresh_db, uid=uid, entry_id=listed[0]["id"])
        assert result["state"] == "trashed"
    assert (await client.post(f"/api/personal-trash/{listed[0]['id']}/restore")).status_code == 200
    assert (root / "fault.txt").read_bytes() == b"durable"


@pytest.mark.parametrize("path", ["/", "/../outside.txt", "/link/secret.txt", "/filelink"])
async def test_http_rejects_root_traversal_and_symlinks(env, path):
    """路径拒绝来自真实fd边界，外部字节始终不变。"""
    _, _, _, root, data, client, *_ = env
    outside = data / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"not owned by workspace")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "filelink").symlink_to(outside / "secret.txt")
    response = await client.delete("/api/workspace/file", params={"path": path})
    assert response.status_code in (400, 403), response.text
    assert (outside / "secret.txt").read_bytes() == b"not owned by workspace"
    assert (await client.get("/api/personal-trash")).json()["total"] == 0


async def test_attachment_original_and_parsed_share_owner_tombstone_restore(env):
    """正式附件的两个字节路径同时隔离，Conversation元数据恢复而非另建资源。"""
    sessions, uid, _, root, data, _, _, _ = env
    project_id = uuid.uuid4().hex
    relative = "projects/" + project_id
    folder = root / relative / "uploads"
    folder.mkdir(parents=True)
    (folder / "original.txt").write_bytes(b"raw attachment")
    (folder / "parsed.md").write_bytes(b"parsed attachment")
    thread = uuid.uuid4().hex
    file_id = uuid.uuid4().hex
    attachment = {
        "file_id": file_id,
        "file_name": "original.txt",
        "path": f"/home/gem/user-data/{relative}/uploads/parsed.md",
        "original_path": f"/home/gem/user-data/{relative}/uploads/original.txt",
    }
    async with sessions() as db:
        db.add(
            Project(
                id=project_id, uid=uid, workdir_path=relative, selection_status="selectable", directory_mode="managed"
            )
        )
        await db.flush()
        conversation = Conversation(
            thread_id=thread,
            uid=uid,
            agent_id="fixture",
            project_id=project_id,
            extra_metadata={"attachments": [attachment]},
        )
        db.add(conversation)
        await db.commit()
        message = Message(conversation_id=conversation.id, role="user", content="queued")
        db.add(message)
        await db.flush()
        queued = AgentRunRequest(
            request_id=uuid.uuid4().hex,
            uid=uid,
            agent_slug="fixture",
            conversation_thread_id=thread,
            input_message_id=message.id,
            status="queued",
        )
        db.add(queued)
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await delete_thread_attachment_view(thread_id=thread, file_id=file_id, db=db, current_uid=uid)
        assert error.value.status_code == 409
        assert (folder / "original.txt").read_bytes() == b"raw attachment"
        queued.status = "cancelled"
        await db.commit()
        result = await delete_thread_attachment_view(thread_id=thread, file_id=file_id, db=db, current_uid=uid)
        entry_id = result["trash"]["id"]
    assert not list(folder.iterdir())
    async with sessions() as db:
        assert (await list_thread_attachments_view(thread_id=thread, db=db, current_uid=uid))["attachments"] == []
        stored = await db.scalar(select(Conversation).where(Conversation.thread_id == thread))
        assert stored.extra_metadata["attachments"][0]["trash_id"] == entry_id
        assert await ConversationRepository(db).bind_attachments_to_request(stored.id, "new-request", [file_id]) == []
        await db.rollback()
        await service.restore_personal_entry(db=db, uid=uid, entry_id=entry_id)
    assert (folder / "original.txt").read_bytes() == b"raw attachment"
    assert (folder / "parsed.md").read_bytes() == b"parsed attachment"
    async with sessions() as db:
        attachments = (await list_thread_attachments_view(thread_id=thread, db=db, current_uid=uid))["attachments"]
        assert [item["file_id"] for item in attachments] == [file_id]
        # 从工作区仅选原件删除，解析件由同一个Conversation Owner扩入回收批次。
        again = await service.trash_personal_paths(
            db=db, uid=uid, paths=[f"/{relative}/uploads/original.txt"], name="original", kind="workspace"
        )
        assert len(again["paths"]) == 2
    assert not list(folder.iterdir())
    async with sessions() as db:
        assert (await list_thread_attachments_view(thread_id=thread, db=db, current_uid=uid))["attachments"] == []


async def test_active_run_and_user_admission_lock_prevent_racing_delete(env):
    """真实PG排它锁等待与活动Run负控，不移动运行中的文件。"""
    sessions, uid, _, root, _, client, *_ = env
    (root / "active.txt").write_bytes(b"in use")
    async with sessions() as db:
        db.add(
            AgentRun(
                id=uuid.uuid4().hex,
                uid=uid,
                agent_slug="fixture",
                conversation_thread_id="thread",
                runtime_scope_id="thread",
                request_id=uuid.uuid4().hex,
                status="running",
            )
        )
        await db.commit()
    response = await client.delete("/api/workspace/file", params={"path": "/active.txt"})
    assert response.status_code == 409
    assert (root / "active.txt").read_bytes() == b"in use"
    async with sessions() as owner, sessions() as contender:
        await service.lock_user_files(owner, uid)
        waiting = asyncio.create_task(service.lock_user_files(contender, uid))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await owner.rollback()
        await asyncio.wait_for(waiting, timeout=3)
        await contender.rollback()


async def test_directory_purge_unlinks_inner_symlink_without_touching_target(env):
    """回收目录中的链接清理只unlink链接，永久不访问外部目标。"""
    sessions, uid, _, root, data, client, *_ = env
    outside = data / "keep.txt"
    outside.write_bytes(b"keep external")
    folder = root / "folder"
    folder.mkdir()
    (folder / "inner-link").symlink_to(outside)
    (folder / "owned.txt").write_bytes(b"delete owned")
    response = await client.delete("/api/workspace/file", params={"path": "/folder"})
    assert response.status_code == 200, response.text
    entry_id = response.json()["trash"]["id"]
    async with sessions() as db:
        entry = await db.get(PersonalTrashEntry, entry_id)
        entry.purge_after = utc_now_naive() - timedelta(days=1)
        await db.commit()
        result = await service.process_personal_entry(db=db, uid=uid, entry_id=entry_id)
        assert result["state"] == "purged"
    assert outside.read_bytes() == b"keep external"


async def test_concurrent_restore_and_due_purge_are_serialized(env):
    """到期时并发恢复必定拒绝，多个清理器只能处理同一隔离字节。"""
    sessions, uid, _, root, data, client, *_ = env
    (root / "race.txt").write_bytes(b"expired bytes")
    entry_id = (await client.delete("/api/workspace/file", params={"path": "/race.txt"})).json()["trash"]["id"]
    async with sessions() as db:
        entry = await db.get(PersonalTrashEntry, entry_id)
        entry.purge_after = utc_now_naive() - timedelta(seconds=1)
        await db.commit()

    async def purge():
        async with sessions() as db:
            return await service.process_personal_entry(db=db, uid=uid, entry_id=entry_id)

    first, second, restore = await asyncio.gather(
        purge(), purge(), client.post(f"/api/personal-trash/{entry_id}/restore")
    )
    assert first["state"] == second["state"] == "purged"
    assert restore.status_code == 409
    assert not (root / "race.txt").exists()
    assert not (data / "trash" / uid / entry_id / "0").exists()


async def test_partial_restore_and_purge_error_recover_without_overwrite(env, monkeypatch):
    """多文件半恢复重试辨认inode；清理失败延后且保留字节与journal。"""
    sessions, uid, _, root, data, _, *_ = env
    (root / "first.txt").write_bytes(b"first")
    (root / "second.txt").write_bytes(b"second")
    async with sessions() as db:
        entry = await service.trash_personal_paths(
            db=db, uid=uid, paths=["/first.txt", "/second.txt"], name="two", kind="attachment"
        )
    original = service.transition_files

    def interrupted_restore(owner, entry_id, paths, operation):
        if operation == "restore":
            original(owner, entry_id, paths[:1], operation)
            raise OSError("injected partial restore")
        return original(owner, entry_id, paths, operation)

    monkeypatch.setattr(service, "transition_files", interrupted_restore)
    async with sessions() as db:
        with pytest.raises(HTTPException):
            await service.restore_personal_entry(db=db, uid=uid, entry_id=entry["id"])
    assert (root / "first.txt").read_bytes() == b"first"
    assert not (root / "second.txt").exists()
    monkeypatch.setattr(service, "transition_files", original)
    async with sessions() as db:
        result = await service.process_personal_entry(db=db, uid=uid, entry_id=entry["id"])
        assert result["state"] == "restored"
        assert (root / "second.txt").read_bytes() == b"second"
        fresh = await service.trash_personal_paths(
            db=db, uid=uid, paths=["/second.txt"], name="second", kind="workspace"
        )
        row = await db.get(PersonalTrashEntry, fresh["id"])
        row.purge_after = utc_now_naive() - timedelta(days=1)
        await db.commit()

    def fail_purge(*args):
        raise OSError("injected purge IO failure")

    monkeypatch.setattr(service, "transition_files", fail_purge)
    async with sessions() as db:
        with pytest.raises(HTTPException):
            await service.process_personal_entry(db=db, uid=uid, entry_id=fresh["id"])
    assert (data / "trash" / uid / fresh["id"] / "0").read_bytes() == b"second"
    async with sessions() as db:
        row = await db.get(PersonalTrashEntry, fresh["id"])
        assert row.state == "purging" and row.error and row.retry_after > utc_now_naive()
        from yuxi.repositories.personal_trash_repository import PersonalTrashRepository

        assert (uid, fresh["id"]) not in await PersonalTrashRepository(db).due(100)
    monkeypatch.setattr(service, "transition_files", original)
    async with sessions() as db:
        result = await service.process_personal_entry(db=db, uid=uid, entry_id=fresh["id"])
        assert result["state"] == "purged"


async def test_failed_intent_commit_keeps_original_bytes_and_no_journal(env, monkeypatch):
    """真实PG意图提交失败时不移动字节，也不留下可见假成功。"""
    sessions, uid, _, root, _, _, *_ = env
    (root / "commit.txt").write_bytes(b"must remain")
    async with sessions() as db:

        async def fail_commit():
            raise RuntimeError("injected database commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failure"):
            await service.trash_personal_paths(db=db, uid=uid, paths=["/commit.txt"], name="commit", kind="workspace")
        await db.rollback()
    assert (root / "commit.txt").read_bytes() == b"must remain"
    async with sessions() as db:
        assert await db.scalar(select(PersonalTrashEntry.id).where(PersonalTrashEntry.uid == uid)) is None


async def test_idle_guard_single_snapshot_survives_atomic_dispatch(env, monkeypatch):
    """首次查询后真实PG原子queued→run；旧两查询guard会同时漏掉两者。"""
    from yuxi.repositories.personal_trash_repository import PersonalTrashRepository

    sessions, uid, _, _, _, _, *_ = env
    project_id, thread, request_id, run_id = (uuid.uuid4().hex for _ in range(4))
    async with sessions() as setup:
        setup.add(
            Project(
                id=project_id,
                uid=uid,
                workdir_path=f"projects/{project_id}",
                selection_status="selectable",
                directory_mode="managed",
            )
        )
        await setup.flush()
        conversation = Conversation(thread_id=thread, uid=uid, agent_id="fixture", project_id=project_id)
        setup.add(conversation)
        await setup.flush()
        message = Message(conversation_id=conversation.id, role="user", content="queued oracle")
        setup.add(message)
        await setup.flush()
        setup.add(
            AgentRunRequest(
                request_id=request_id,
                uid=uid,
                agent_slug="fixture",
                conversation_thread_id=thread,
                input_message_id=message.id,
                status="queued",
            )
        )
        await setup.commit()
    async with sessions() as checked:
        original_scalar = checked.scalar
        first_query = True

        async def dispatch_after_first_snapshot(statement, *args, **kwargs):
            nonlocal first_query
            result = await original_scalar(statement, *args, **kwargs)
            if first_query:
                first_query = False
                async with sessions() as dispatcher:
                    dispatcher.add(
                        AgentRun(
                            id=run_id,
                            uid=uid,
                            agent_slug="fixture",
                            conversation_thread_id=thread,
                            runtime_scope_id=thread,
                            request_id=request_id,
                            status="running",
                        )
                    )
                    await dispatcher.flush()
                    request = await dispatcher.scalar(
                        select(AgentRunRequest).where(AgentRunRequest.request_id == request_id)
                    )
                    request.status = "dispatched"
                    request.dispatched_run_id = run_id
                    await dispatcher.commit()
            return result

        monkeypatch.setattr(checked, "scalar", dispatch_after_first_snapshot)
        with pytest.raises(HTTPException) as error:
            await PersonalTrashRepository(checked).require_idle(uid)
        assert error.value.status_code == 409
    async with sessions() as observer:
        assert (await observer.get(AgentRun, run_id)).status == "running"
        request = await observer.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
        assert request.status == "dispatched"


async def test_new_request_owner_rejects_pending_file_journal(env):
    """普通请求的新持久化 Owner 在写入 Request 前执行真实回收事务门禁。"""
    from yuxi.services.agent_request_service import AgentRequestInput, RunOrigin, _persist_request
    from yuxi.services.input_message_service import build_chat_input_message

    sessions, uid, _, _, _, _, _, user = env
    async with sessions() as db:
        db.add(
            PersonalTrashEntry(
                id="pending-" + uid,
                uid=uid,
                name="pending.txt",
                kind="workspace",
                paths=[],
                state="pending_delete",
                deleted_at=utc_now_naive(),
                purge_after=utc_now_naive() + timedelta(days=30),
            )
        )
        await db.commit()
    async with sessions() as db:
        with pytest.raises(HTTPException) as exc:
            await _persist_request(
                db=db,
                request_input=AgentRequestInput(
                    request_id="request-" + uid,
                    agent_slug="fixture",
                    thread_id="fixture",
                    input_message=build_chat_input_message("hello"),
                    origin=RunOrigin(source="chat", channel="web"),
                ),
                current_user=user,
                agent_item=None,
                agent_backend=None,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "文件回收操作尚在处理，请在统一回收站重试或稍后再试"
        assert (await db.scalars(select(AgentRunRequest).where(AgentRunRequest.uid == uid))).all() == []
