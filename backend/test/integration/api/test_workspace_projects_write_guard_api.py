"""projects 写守卫与展示口径（issue #1184）：implicit 子树拒写、用户目录与文件可见、绑定流程保留。"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest
from test.live_api_cleanup import make_test_resource_id

from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@asynccontextmanager
async def _database_connection():
    """为当前测试 loop 创建独立 PostgreSQL 连接。"""
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")
    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()


async def _upload_file(test_client, headers, parent_path: str, filename: str = "guard.txt"):
    return await test_client.post(
        "/api/workspace/upload",
        data={"parent_path": parent_path},
        files=[("files", (filename, b"guard", "text/plain"))],
        headers=headers,
    )


async def test_user_directories_and_files_in_projects_are_visible(test_client, standard_user):
    """个人空间在 projects 顶层/未绑定目录创建与上传：成功且立即出现在列表口径，不再是黑洞。"""
    headers = standard_user["headers"]
    dir_name = f"pytest-guard-{uuid4().hex}"
    created = await test_client.post("/api/workspace/directory", json={"parent_path": "/", "name": "projects"}, headers=headers)
    assert created.status_code == 200, created.text

    response = await _upload_file(test_client, headers, "/projects")
    assert response.status_code == 200, response.text

    created = await test_client.post("/api/workspace/directory", json={"parent_path": "/projects", "name": dir_name}, headers=headers)
    assert created.status_code == 200, created.text
    response = await _upload_file(test_client, headers, f"/projects/{dir_name}", filename="inner.txt")
    assert response.status_code == 200, response.text

    tree = await test_client.get("/api/workspace/tree", params={"path": "/projects"}, headers=headers)
    assert tree.status_code == 200, tree.text
    names = {entry["name"] for entry in tree.json()["entries"]}
    assert {"guard.txt", dir_name} <= names

    tree = await test_client.get("/api/workspace/tree", params={"path": f"/projects/{dir_name}"}, headers=headers)
    assert {entry["name"] for entry in tree.json()["entries"]} == {"inner.txt"}


async def test_upload_into_bound_project_visible(test_client, standard_user):
    """弹窗建的目录绑定为 linked Project 后，上传成功且出现在列表口径中。"""
    headers = standard_user["headers"]
    dir_name = f"pytest-guard-{uuid4().hex}"
    await test_client.post("/api/workspace/directory", json={"parent_path": "/", "name": "projects"}, headers=headers)
    await test_client.post("/api/workspace/directory", json={"parent_path": "/projects", "name": dir_name}, headers=headers)
    project = await test_client.post(
        "/api/projects",
        json={
            "request_id": make_test_resource_id("linked-project"),
            "name": dir_name,
            "workdir": {"mode": "linked", "path": f"projects/{dir_name}"},
        },
        headers=headers,
    )
    assert project.status_code == 200, project.text

    response = await _upload_file(test_client, headers, f"/projects/{dir_name}")
    assert response.status_code == 200, response.text

    tree = await test_client.get("/api/workspace/tree", params={"path": f"/projects/{dir_name}"}, headers=headers)
    assert {entry["name"] for entry in tree.json()["entries"]} == {"guard.txt"}


async def test_upload_into_implicit_project_rejected(test_client, standard_user):
    """会话 implicit 项目仍被隐藏，向其子树写文件 fail-closed 拒绝且不落盘。"""
    headers = standard_user["headers"]
    uid = standard_user["user"]["uid"]
    implicit_rel = f"projects/20261120/{uuid.uuid4().hex[:6]}"
    workdir = user_workspace_dir(str(uid)) / implicit_rel
    workdir.mkdir(parents=True, exist_ok=True)
    project_id = str(uuid.uuid4())
    async with _database_connection() as connection:
        await connection.execute(
            "INSERT INTO projects (id, uid, name, selection_status, workdir_path, directory_mode, status, created_at, updated_at) "
            "VALUES ($1, $2, NULL, 'implicit', $3, 'managed', 'active', now(), now())",
            project_id,
            str(uid),
            implicit_rel,
        )
    try:
        await test_client.post("/api/workspace/directory", json={"parent_path": "/", "name": "projects"}, headers=headers)
        response = await _upload_file(test_client, headers, f"/{implicit_rel}")
        assert response.status_code == 400, response.text
        assert "workspace_hidden_target" in response.text
        assert not (workdir / "guard.txt").exists()

        tree = await test_client.get("/api/workspace/tree", params={"path": "/projects/20261120"}, headers=headers)
        assert tree.json()["entries"] == []
    finally:
        async with _database_connection() as connection:
            await connection.execute("DELETE FROM projects WHERE id = $1", project_id)
        import shutil

        shutil.rmtree(user_workspace_dir(str(uid)) / "projects" / "20261120", ignore_errors=True)
