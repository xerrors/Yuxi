"""真实 HTTP、文件系统及 PostgreSQL 验证个人文件打开历史。"""

import asyncio
import socket
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def recent_files_user(standard_user, postgres_sessions):
    """临时账号软删除前，回收独立创建的文件打开历史。"""
    yield standard_user
    async with postgres_sessions() as db:
        await db.execute(text("DELETE FROM workspace_recent_files WHERE uid=:uid"), {"uid": standard_user["user"]["uid"]})
        await db.commit()


async def test_recent_files_follow_only_successful_personal_opens(
    test_client, recent_files_user, admin_headers, postgres_sessions, monkeypatch, api_key_factory
):
    """只记录当前用户主动打开成功的文件，去重排序并重新检查文件边界。"""
    headers = recent_files_user["headers"]
    uid = str(recent_files_user["user"]["uid"])
    prefix = f"pytest-recent-{uuid4().hex}"
    first, second, binary = (f"{prefix}-{name}" for name in ("a.txt", "b.txt", "binary.bin"))
    payload = [(first, b"first"), (second, b"second"), (binary, b"\x00\x01\x00")]
    uploaded = await test_client.post(
        "/api/workspace/upload",
        data={"parent_path": "/"},
        files=[("files", (name, body)) for name, body in payload],
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    workspace = user_workspace_dir(uid)
    paths = [f"/{name}" for name, _ in payload]
    try:
        read = await test_client.get("/api/workspace/file", params={"path": paths[0]}, headers=headers)
        assert read.json()["content"] == "first"
        unsupported = await test_client.post("/api/workspace/open-file", params={"path": paths[2]}, headers=headers)
        assert unsupported.json()["supported"] is False
        missing = await test_client.post("/api/workspace/open-file", params={"path": f"/missing-{prefix}"}, headers=headers)
        assert missing.status_code == 404
        denied = await test_client.post("/api/workspace/open-file", params={"path": f"/../{first}"}, headers=headers)
        assert denied.status_code == 403
        initial = await test_client.get("/api/workspace/recent-files", headers=headers)
        assert initial.json()["entries"] == []

        key = await api_key_factory(
            {"request_id": str(uuid4()), "name": "APP history boundary", "app_id": "recent-files-test", "access_level": "full"},
            owner_headers=headers,
            cleanup_headers=admin_headers,
        )
        assert key.response.status_code == 200
        app_read = await test_client.get("/api/workspace/recent-files", headers=key.headers)
        app_open = await test_client.post("/api/workspace/open-file", params={"path": paths[0]}, headers=key.headers)
        assert (app_read.status_code, app_open.status_code) == (403, 403)

        opened = await asyncio.gather(
            *(test_client.post("/api/workspace/open-file", params={"path": path}, headers=headers) for path in paths[:2])
        )
        assert [res.json()["content"] for res in opened] == ["first", "second"]
        reopened = await test_client.post("/api/workspace/open-file", params={"path": f"//{first}"}, headers=headers)
        assert reopened.json()["content"] == "first"

        recent = (await test_client.get("/api/workspace/recent-files", headers=headers)).json()["entries"]
        assert [entry["path"] for entry in recent] == paths[:2]
        assert recent[0]["opened_at"] > recent[1]["opened_at"]
        assert recent[0]["name"] == first and recent[0]["size"] == len(b"first")
        async with postgres_sessions() as db:
            persisted = await db.scalar(text("SELECT entries FROM workspace_recent_files WHERE uid=:uid"), {"uid": uid})
            assert persisted == [{"path": entry["path"], "opened_at": entry["opened_at"]} for entry in recent]

        foreign = (await test_client.get("/api/workspace/recent-files", headers=admin_headers)).json()["entries"]
        assert set(paths).isdisjoint(entry["path"] for entry in foreign)
        foreign_open = await test_client.post("/api/workspace/open-file", params={"path": paths[0]}, headers=admin_headers)
        assert foreign_open.status_code == 404

        (workspace / first).chmod(0o000)
        unreadable = await test_client.post("/api/workspace/open-file", params={"path": paths[0]}, headers=headers)
        assert unreadable.status_code == 403, unreadable.text
        recent = await test_client.get("/api/workspace/recent-files", headers=headers)
        assert [entry["path"] for entry in recent.json()["entries"]] == [paths[1]]

        (workspace / first).unlink()
        (workspace / first).symlink_to(workspace / second)
        assert [e["path"] for e in (await test_client.get("/api/workspace/recent-files", headers=headers)).json()["entries"]] == [paths[1]]
        symlink = await test_client.post("/api/workspace/open-file", params={"path": paths[0]}, headers=headers)
        assert symlink.status_code == 403
        (workspace / second).unlink()
        assert (await test_client.get("/api/workspace/recent-files", headers=headers)).json()["entries"] == []

        # 用短相对地址创建 Unix socket，替换同一路径后仍不是可读的普通文件。
        with socket.socket(socket.AF_UNIX) as local_socket:
            with monkeypatch.context() as patch:
                patch.chdir(workspace)
                local_socket.bind(second)
            socket_open = await test_client.post("/api/workspace/open-file", params={"path": paths[1]}, headers=headers)
            assert socket_open.status_code == 403
            assert (await test_client.get("/api/workspace/recent-files", headers=headers)).json()["entries"] == []
    finally:
        for name, _ in payload:
            (workspace / name).unlink(missing_ok=True)


async def test_overview_retains_the_five_latest_distinct_files(test_client, recent_files_user):
    """打开第六个文件后，记录保留最近五项。"""
    headers = recent_files_user["headers"]
    uid = str(recent_files_user["user"]["uid"])
    names = [f"recent-{index}.md" for index in range(6)]
    uploaded = await test_client.post(
        "/api/workspace/upload",
        data={"parent_path": "/"},
        files=[("files", (name, "# preview")) for name in names],
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    try:
        for name in names:
            response = await test_client.post("/api/workspace/open-file", params={"path": f"/{name}"}, headers=headers)
            assert response.json()["content"] == "# preview"
        entries = (await test_client.get("/api/workspace/recent-files", headers=headers)).json()["entries"]
        assert [entry["name"] for entry in entries] == list(reversed(names[1:]))
    finally:
        for name in names:
            (user_workspace_dir(uid) / name).unlink(missing_ok=True)
