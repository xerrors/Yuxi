"""个人空间全量展示 projects（含会话 Workdir）+ 会话引用 Workdir 的整目录删除保护。"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest
from test.live_api_cleanup import make_test_resource_id, make_test_session_title

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


async def test_session_workdir_visible_and_ancestor_delete_rejected(test_client, admin_headers):
    """会话自动 Workdir 在个人空间逐级可见可写；上层日期目录整删被拒且文件保留。"""
    headers = admin_headers
    response = await test_client.post(
        "/api/v1/agents/sessions",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"agent_id": await _default_agent_slug(test_client, headers), "title": make_test_session_title("open-display")},
    )
    assert response.status_code == 201, response.text
    thread_id = response.json()["id"]
    async with _database_connection() as db:
        row = await db.fetchrow(
            "SELECT c.uid, p.workdir_path FROM sessions c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid WHERE c.thread_id = $1",
            thread_id,
        )
    workdir = str(row["workdir_path"])
    loose_name = f"loose-{uuid.uuid4().hex[:8]}.txt"
    inner_name = f"from-user-{uuid.uuid4().hex[:8]}.txt"
    date_layer = "/projects/" + workdir.split("/")[1]
    workdir_name = workdir.rsplit("/", 1)[-1]

    assert date_layer.rsplit("/", 1)[-1] in await _tree_names(test_client, headers, "/projects")
    assert workdir_name in await _tree_names(test_client, headers, date_layer)

    uploaded = await _upload_file(test_client, headers, date_layer, loose_name)
    assert uploaded.status_code == 200, uploaded.text
    assert loose_name in await _tree_names(test_client, headers, date_layer)

    inside = await _upload_file(test_client, headers, f"/{workdir}", inner_name)
    assert inside.status_code == 200, inside.text
    assert inner_name in await _tree_names(test_client, headers, f"/{workdir}")

    blocked = await test_client.delete("/api/workspace/file", params={"path": date_layer}, headers=headers)
    assert blocked.status_code == 400, blocked.text
    assert "workspace_session_workdir_protected" in blocked.text
    assert loose_name in await _tree_names(test_client, headers, date_layer)

    file_in_workdir = await test_client.delete("/api/workspace/file", params={"path": f"/{workdir}/{inner_name}"}, headers=headers)
    assert file_in_workdir.status_code == 200, file_in_workdir.text
    assert inner_name not in await _tree_names(test_client, headers, f"/{workdir}")


async def test_linked_workdir_delete_rejected_until_project_removed(test_client, admin_headers):
    """linked Workdir 被会话绑定时整删拒绝；删除 Project 后残留目录可整删。"""
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

    response = await test_client.post(
        "/api/v1/agents/sessions",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "agent_id": await _default_agent_slug(test_client, headers),
            "project_id": project["id"],
            "title": make_test_session_title("open-linked"),
        },
    )
    assert response.status_code == 201, response.text

    uploaded = await _upload_file(test_client, headers, f"/{dir_name}", keep_name)
    assert uploaded.status_code == 200, uploaded.text

    blocked = await test_client.delete("/api/workspace/file", params={"path": f"/{dir_name}"}, headers=headers)
    assert blocked.status_code == 400, blocked.text
    assert keep_name in await _tree_names(test_client, headers, f"/{dir_name}")

    deleted = await test_client.delete(f"/api/projects/{project['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    allowed = await test_client.delete("/api/workspace/file", params={"path": f"/{dir_name}"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert dir_name not in await _tree_names(test_client, headers, "/")
