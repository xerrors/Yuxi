"""个人空间展示和删除会话 Workdir，历史与当前执行条件分别读取。"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest
from test.e2e.e2e_helpers import (
    archive_public_thread,
    public_message,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.live_api_cleanup import make_test_resource_id, make_test_session_title
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _database_connection():
    """为当前测试 loop 创建独立 PostgreSQL 连接。"""
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")
    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()


async def _default_agent_slug(test_client, headers: dict[str, str]) -> str:
    response = await test_client.get("/api/agent", headers=headers)
    assert response.status_code == 200, response.text
    agent = next(item for item in response.json()["agents"] if item.get("is_default"))
    return str(agent.get("slug") or agent["agent_id"])


async def _upload_file(test_client, headers, parent_path: str, filename: str):
    return await test_client.post(
        "/api/workspace/upload",
        data={"parent_path": parent_path},
        files=[("files", (filename, b"payload", "text/plain"))],
        headers=headers,
    )


async def _tree_names(test_client, headers, path: str) -> set[str]:
    response = await test_client.get("/api/workspace/tree", params={"path": path}, headers=headers)
    assert response.status_code == 200, response.text
    return {entry["name"] for entry in response.json()["entries"]}


async def test_session_workdir_visible_and_ancestor_delete_allowed(test_client, admin_headers, standard_user):
    """网页草稿重放同一绑定，任务目录可见，删除上级目录后保留会话。"""
    headers = standard_user["headers"]
    create_headers = {**headers, "Idempotency-Key": str(uuid.uuid4())}
    body = {
        "agent_id": await _default_agent_slug(test_client, admin_headers),
        "title": make_test_session_title("open-display"),
        "workdir_naming_text": "Draft quarterly report",
    }
    response = await test_client.post("/api/agent/threads", headers=create_headers, json=body)
    assert response.status_code == 201, response.text
    thread_id = response.json()["id"]
    replay = await test_client.post("/api/agent/threads", headers=create_headers, json=body)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == thread_id
    assert replay.json()["yuxi"]["project_id"] == response.json()["yuxi"]["project_id"]
    assert replay.json()["yuxi"]["receipt"] == response.json()["yuxi"]["receipt"]

    conflict = await test_client.post(
        "/api/agent/threads", headers=create_headers, json={**body, "workdir_naming_text": "Changed draft"}
    )
    assert conflict.status_code == 409, conflict.text

    async with _database_connection() as db:
        row = await db.fetchrow(
            "SELECT p.id AS project_id, p.workdir_path FROM sessions c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid "
            "WHERE c.thread_id = $1",
            thread_id,
        )
        assert await db.fetchval("SELECT COUNT(*) FROM agent_inputs WHERE thread_id = $1", thread_id) == 0
    workdir = row["workdir_path"]
    expected_name = f"draft-quarterly-report-{row['project_id'][:6]}"
    assert re.fullmatch(rf"agents/tasks/\d{{4}}-\d{{2}}-\d{{2}}/{expected_name}", workdir)
    loose_name = f"loose-{uuid.uuid4().hex[:8]}.txt"
    inner_name = f"from-user-{uuid.uuid4().hex[:8]}.txt"
    date_layer = "/" + workdir.rsplit("/", 1)[0]

    assert "tasks" in await _tree_names(test_client, headers, "/agents")
    assert date_layer.rsplit("/", 1)[-1] in await _tree_names(test_client, headers, "/agents/tasks")
    assert expected_name in await _tree_names(test_client, headers, date_layer)

    new_title = make_test_session_title("renamed-display")
    response = await test_client.post(f"/api/v1/agents/sessions/{thread_id}", headers=headers, json={"yuxi": {"title": new_title}})
    assert response.status_code == 200, response.text
    reopened = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=headers)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["yuxi"]["project_id"] == row["project_id"]
    assert reopened.json()["yuxi"]["title"] == new_title
    async with _database_connection() as db:
        assert await db.fetchval("SELECT workdir_path FROM projects WHERE id = $1", row["project_id"]) == workdir

    uploaded = await _upload_file(test_client, headers, date_layer, loose_name)
    assert uploaded.status_code == 200, uploaded.text
    assert loose_name in await _tree_names(test_client, headers, date_layer)

    inside = await _upload_file(test_client, headers, f"/{workdir}", inner_name)
    assert inside.status_code == 200, inside.text
    assert inner_name in await _tree_names(test_client, headers, f"/{workdir}")

    file_in_workdir = await test_client.delete("/api/workspace/file", params={"path": f"/{workdir}/{inner_name}"}, headers=headers)
    assert file_in_workdir.status_code == 200, file_in_workdir.text
    assert inner_name not in await _tree_names(test_client, headers, f"/{workdir}")

    candidates = await test_client.get("/api/projects/history-candidates", params={"q": new_title}, headers=headers)
    assert candidates.status_code == 200, candidates.text
    assert [item["thread_id"] for item in candidates.json()["items"]] == [thread_id]

    workdir_url = f"/api/agent/threads/{thread_id}/workdir"
    available = await test_client.get(workdir_url, headers=headers)
    assert available.status_code == 204 and available.headers["Cache-Control"] == "no-store"
    deleted = await test_client.delete("/api/workspace/file", params={"path": date_layer}, headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert date_layer.rsplit("/", 1)[-1] not in await _tree_names(test_client, headers, "/agents/tasks")

    # 重放创建回执也不能重建已删目录。
    replay = await test_client.post("/api/agent/threads", headers=create_headers, json=body)
    assert replay.status_code == 201 and replay.json()["id"] == thread_id
    missing = await test_client.get(workdir_url, headers=headers)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "session_workdir_unavailable"
    candidates = await test_client.get("/api/projects/history-candidates", params={"q": new_title}, headers=headers)
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["items"] == []
    async with _database_connection() as db:
        assert await db.fetchval("SELECT workdir_path FROM projects WHERE id = $1", row["project_id"]) == workdir
        assert await db.fetchval("SELECT status FROM sessions WHERE thread_id = $1", thread_id) == "active"


@pytest.mark.parametrize("parent_kind", ["file", "symlink"])
async def test_session_workdir_parent_conflict_does_not_create_binding(test_client, admin_headers, standard_user, parent_kind):
    """父目录被文件或链接占用时返回冲突，数据库和原条目保持完整。"""
    uid = str(standard_user["user"]["uid"])
    ensure_user_workspace(uid)
    workspace = user_workspace_dir(uid)
    blocked_parent = workspace / "agents" / "tasks"
    marker = b"keep this workspace entry"
    target = workspace / "unrelated-test-directory"
    if parent_kind == "symlink":
        target.mkdir()
        blocked_parent.symlink_to(target, target_is_directory=True)
    else:
        blocked_parent.write_bytes(marker)
    try:
        response = await test_client.post(
            "/api/v1/agents/sessions",
            headers={**standard_user["headers"], "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": await _default_agent_slug(test_client, admin_headers), "title": make_test_session_title("parent-conflict")},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "工作目录的父路径已被占用"
        async with _database_connection() as db:
            assert await db.fetchval("SELECT COUNT(*) FROM projects WHERE uid = $1", uid) == 0
            assert await db.fetchval("SELECT COUNT(*) FROM sessions WHERE uid = $1", uid) == 0
        if parent_kind == "symlink":
            assert blocked_parent.readlink() == target
            assert list(target.iterdir()) == []
        else:
            assert blocked_parent.read_bytes() == marker
    finally:
        blocked_parent.unlink()
        if parent_kind == "symlink":
            target.rmdir()


async def test_linked_workdir_delete_makes_all_bound_sessions_unavailable(test_client, admin_headers):
    """删除共享 linked 目录后所有会话均不可继续，项目仍存在。"""
    headers = admin_headers
    dir_name = f"pytest-open-{uuid.uuid4().hex[:10]}"
    keep_name = f"keep-{uuid.uuid4().hex[:8]}.txt"
    created = await test_client.post("/api/workspace/directory", json={"parent_path": "/", "name": dir_name}, headers=headers)
    assert created.status_code == 200, created.text
    project_response = await test_client.post(
        "/api/projects",
        headers=headers,
        json={"request_id": make_test_resource_id("open-linked"), "name": dir_name, "workdir": {"mode": "linked", "path": dir_name}},
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()

    slug = await _default_agent_slug(test_client, headers)
    threads = []
    for naming_text in ("First shared draft", "Second shared draft"):
        response = await test_client.post(
            "/api/agent/threads",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "agent_id": slug,
                "project_id": project["id"],
                "title": make_test_session_title("open-linked"),
                "workdir_naming_text": naming_text,
            },
        )
        assert response.status_code == 201, response.text
        threads.append(response.json()["id"])
    async with _database_connection() as db:
        rows = await db.fetch(
            "SELECT c.thread_id, p.workdir_path FROM sessions c JOIN projects p ON p.id = c.project_id WHERE c.thread_id = ANY($1::text[])",
            threads,
        )
    assert {row["thread_id"]: row["workdir_path"] for row in rows} == dict.fromkeys(threads, dir_name)

    uploaded = await _upload_file(test_client, headers, f"/{dir_name}", keep_name)
    assert uploaded.status_code == 200, uploaded.text

    assert keep_name in await _tree_names(test_client, headers, f"/{dir_name}")
    allowed = await test_client.delete("/api/workspace/file", params={"path": f"/{dir_name}"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert dir_name not in await _tree_names(test_client, headers, "/")
    for thread_id in threads:
        missing = await test_client.get(f"/api/agent/threads/{thread_id}/workdir", headers=headers)
        assert missing.status_code == 409 and missing.json()["detail"]["code"] == "session_workdir_unavailable"

    new_thread = await test_client.post(
        "/api/v1/agents/sessions",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"agent_id": slug, "project_id": project["id"], "title": make_test_session_title("missing-linked")},
    )
    assert new_thread.status_code == 409 and new_thread.json()["detail"]["code"] == "session_workdir_unavailable"
    deleted = await test_client.delete(f"/api/projects/{project['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize(
    ("path_template", "id_length"),
    [("projects/20240902/{short_id}", 6), ("projects/2024-09-02_14-35-08_{short_id}", 8)],
    ids=["date-grouped", "timestamped"],
)
async def test_reopen_legacy_workdir_and_send_message_preserves_binding(
    test_client, admin_headers, standard_user, path_template: str, id_length: int
):
    """旧托管目录存在时正常续聊，删除后保留历史且拒绝新输入。"""
    headers = standard_user["headers"]
    uid = standard_user["user"]["uid"]
    provider_id = f"ci-workdir-history-{uuid.uuid4().hex[:8]}"
    await register_replay_provider(test_client, admin_headers, provider_id=provider_id)
    thread_id = turn_id = None
    try:
        response = await test_client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "agent_id": await _default_agent_slug(test_client, admin_headers),
                "title": make_test_session_title("legacy-workdir"),
                "agent": {"model": f"{provider_id}:deterministic-chat"},
            },
        )
        assert response.status_code == 201, response.text
        thread_id = response.json()["id"]
        session_url = f"/api/v1/agents/sessions/{thread_id}"

        # 将本例创建的空会话回放为已有历史绑定，其后从真实 HTTP 打开和继续对话。
        binding_sql = (
            "SELECT p.id, p.workdir_path FROM sessions c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid WHERE c.thread_id = $1"
        )
        async with _database_connection() as db, db.transaction():
            row = await db.fetchrow(binding_sql, thread_id)
            project_id = row["id"]
            legacy_path = path_template.format(short_id=project_id[:id_length])
            workspace = user_workspace_dir(uid)
            legacy_dir = workspace / legacy_path
            legacy_dir.parent.mkdir(parents=True, exist_ok=True)
            (workspace / row["workdir_path"]).rename(legacy_dir)
            await db.execute(
                "UPDATE projects SET workdir_path = $1, created_at = TIMESTAMPTZ '2024-09-02 14:35:08+08:00' WHERE id = $2",
                legacy_path,
                project_id,
            )
            await db.execute("UPDATE sessions SET created_at = TIMESTAMPTZ '2024-09-02 14:35:08+08:00' WHERE thread_id = $1", thread_id)
            assert await db.fetchval("SELECT COUNT(*) FROM agent_inputs WHERE thread_id = $1", thread_id) == 0
        original = b"bytes saved by the earlier conversation"
        (legacy_dir / "keep.txt").write_bytes(original)

        reopened = await test_client.get(session_url, headers=headers)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["yuxi"]["project_id"] == project_id
        viewer_params = {"thread_id": thread_id, "path": "/keep.txt"}
        before = await test_client.get("/api/viewer/filesystem/download", params=viewer_params, headers=headers)
        assert before.status_code == 200, before.text
        assert before.content == original

        message = "DETERMINISTIC_AGENT_E2E_OK DETERMINISTIC_FEEDBACK Review the earlier records"
        accepted = await test_client.post(
            f"{session_url}/events",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [{"type": "agent.session.input.message", "input": [public_message(message)], "yuxi": {"mode": "follow_up"}}]},
        )
        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        consumed = await wait_for_consumed_input(test_client, headers, receipt)
        turn_id = consumed["turn_id"]
        async with _database_connection() as db:
            assert tuple(await db.fetchrow(binding_sql, thread_id)) == (project_id, legacy_path)
            inputs = await db.fetch(
                "SELECT id, status, messages->0->'content'->0->>'text' FROM agent_inputs WHERE thread_id = $1",
                thread_id,
            )
            assert [tuple(row) for row in inputs] == [(receipt["input_id"], "consumed", message)]
        after = await test_client.get("/api/viewer/filesystem/download", params=viewer_params, headers=headers)
        assert after.status_code == 200, after.text
        assert after.content == original
        terminal = await wait_for_terminal_turn(test_client, headers, thread_id, turn_id)
        assert terminal["status"] == "completed", terminal

        removed_workdir = await test_client.delete("/api/workspace/file", params={"path": f"/{legacy_path}"}, headers=headers)
        assert removed_workdir.status_code == 200 and not legacy_dir.exists()
        rejected = await test_client.post(
            f"{session_url}/events",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [{"type": "agent.session.input.message", "input": [public_message("Cannot continue")]}]},
        )
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "session_workdir_unavailable"
        history = await test_client.get(f"{session_url}/items", headers=headers)
        assert history.status_code == 200, history.text
        assert any(
            part.get("text") == message
            for item in history.json()["data"]
            for part in item.get("content", [])
        )
        async with _database_connection() as db:
            assert await db.fetchval("SELECT COUNT(*) FROM agent_inputs WHERE thread_id = $1", thread_id) == 1
            assert tuple(await db.fetchrow(binding_sql, thread_id)) == (project_id, legacy_path)
        assert not legacy_dir.exists()
    finally:
        try:
            if thread_id:
                if turn_id is None:
                    async with _database_connection() as db:
                        turn_id = await db.fetchval("SELECT turn_id FROM agent_inputs WHERE thread_id = $1", thread_id)
                await archive_public_thread(test_client, headers, thread_id, turn_id=turn_id)
        finally:
            removed = await test_client.delete(f"/api/system/model-providers/{provider_id}", headers=admin_headers)
            assert removed.status_code == 200, removed.text
