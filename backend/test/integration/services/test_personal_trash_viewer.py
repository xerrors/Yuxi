"""通过真实Viewer与聊天artifact路由验证个人回收读入口。"""

import uuid

import pytest
from sqlalchemy import select
from server.routers.filesystem_router import filesystem_router
from server.routers.mention_router import mention_router
from server.routers.chat_router import chat
from yuxi.storage.postgres.models_business import Conversation, Project
from yuxi.storage.postgres.models_lifecycle import PersonalTrashEntry
from test.integration.services.test_personal_trash_lifecycle import env as trash_env  # noqa: F401

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def env(trash_env):  # noqa: F811
    """复用隔离PG和Workspace，挂真实路由而非假文件handler。"""
    return trash_env


async def test_viewer_and_artifact_old_path_new_inode_and_restore_collision(env):
    sessions, uid, _, root, _, client, app, _ = env
    app.include_router(filesystem_router, prefix="/api")
    app.include_router(chat, prefix="/api")
    app.include_router(mention_router, prefix="/api")
    project_id = str(uuid.uuid4())
    relative = "projects/" + project_id
    folder = root / relative
    folder.mkdir(parents=True)
    (folder / "proof.txt").write_bytes(b"original-private-file")
    thread = uuid.uuid4().hex
    async with sessions() as db:
        db.add(
            Project(
                id=project_id, uid=uid, workdir_path=relative, selection_status="selectable", directory_mode="managed"
            )
        )
        await db.flush()
        db.add(Conversation(thread_id=thread, uid=uid, agent_id="fixture", project_id=project_id))
        await db.commit()
    params = {"thread_id": thread, "path": "/proof.txt"}
    artifact = f"/api/chat/thread/{thread}/artifacts/home/gem/user-data/{relative}/proof.txt"
    assert (await client.get(artifact)).content == b"original-private-file"
    assert (await client.get("/api/viewer/filesystem/download", params=params)).content == b"original-private-file"
    response = await client.delete("/api/viewer/filesystem/file", params=params)
    assert response.status_code == 200, response.text
    entry = response.json()["trash"]
    mentions = await client.get("/api/mention/search", params={"query": "proof", "sources": "workspace"})
    assert mentions.status_code == 200 and mentions.json() == []
    assert (await client.get(artifact)).status_code == 404
    assert (await client.get("/api/viewer/filesystem/file", params=params)).status_code == 404
    (folder / "proof.txt").write_bytes(b"new-independent-inode")
    assert (await client.get(artifact)).content == b"new-independent-inode"
    assert (await client.post(f"/api/personal-trash/{entry['id']}/restore")).status_code == 409
    assert (folder / "proof.txt").read_bytes() == b"new-independent-inode"
    (folder / "proof.txt").unlink()
    assert (await client.post(f"/api/personal-trash/{entry['id']}/restore")).status_code == 200
    assert (await client.get(artifact)).content == b"original-private-file"
    # 中断意图存在但bytes尚未移动时，两个读取入口也必须拒绝。
    async with sessions() as db:
        row = await db.scalar(select(PersonalTrashEntry).where(PersonalTrashEntry.id == entry["id"]))
        row.state = "pending_delete"
        await db.commit()
    assert (await client.get(artifact)).status_code == 409
    assert (await client.get("/api/viewer/filesystem/download", params=params)).status_code == 409
    mentions = await client.get("/api/mention/search", params={"query": "proof", "sources": "workspace"})
    assert mentions.status_code == 409
