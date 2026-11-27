"""Realtime Project Workdir HTTP integration tests."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from test.integration.api.helpers import create_default_agent_thread, get_thread_workdir_path
from test.integration.support.module_audit import serve_http
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.main import app
from yuxi.modules.workspace import filesystem
from yuxi.modules.workspace.paths import user_workdir_host_dir
from yuxi.modules.workspace.services import viewer
from yuxi.modules.workspace.services.bindings import AuthorizedWorkdir
from yuxi.modules.workspace.workdir import Workdir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_viewer_tree_requires_authentication(test_client):
    response = await test_client.get("/api/viewer/filesystem/tree", params={"thread_id": "x", "path": "/"})
    assert response.status_code == 401


@pytest.mark.self_managed_resources
@pytest.mark.parametrize(
    "range_header,status,content,content_range",
    [
        (None, 200, b"report", None),
        ("bytes=1-3", 206, b"epo", "bytes 1-3/6"),
        ("invalid", 400, None, None),
        ("bytes=9-10", 416, None, "bytes */6"),
    ],
)
async def test_viewer_download_preserves_range_protocol_and_removes_prepared_file(
    monkeypatch, tmp_path, range_header, status, content, content_range
):
    """真实 HTTP 下载保留 Range 响应，正常和拒绝请求都回收临时文件。"""
    user = SimpleNamespace(uid="fixture-user")
    workspace_root = tmp_path / "workspace"
    workdir_path = "projects/20261125/abcdef"
    workdir_root = workspace_root / workdir_path
    workdir_root.mkdir(parents=True)
    filename = "报告.txt"
    source = workdir_root / filename
    source.write_bytes(b"report")
    transfer_dir = tmp_path / "transfer"
    transfer_dir.mkdir()
    monkeypatch.setattr(filesystem, "user_workspace_dir", lambda _uid: workspace_root)
    monkeypatch.setattr(viewer.tempfile, "tempdir", str(transfer_dir))
    monkeypatch.setattr(app, "dependency_overrides", {get_required_user: lambda: user, get_db: object})
    binding = AuthorizedWorkdir(1, "fixture-thread", Workdir.open_existing(user.uid, workdir_path))

    async def resolve_workdir(**kwargs):
        """隔离授权绑定，真实 Workdir 仍负责复制。"""
        assert kwargs["thread_id"] == binding.thread_id and kwargs["uid"] == user.uid
        return binding

    monkeypatch.setattr(viewer, "resolve_authorized_workdir", resolve_workdir)
    async with serve_http(app) as client:
        response = await client.get(
            "/api/viewer/filesystem/download",
            params={"thread_id": binding.thread_id, "path": f"/{filename}"},
            headers={"Range": range_header} if range_header else {},
        )
        assert response.status_code == status, response.text
        assert response.headers.get("content-range") == content_range
        if content is not None:
            assert response.content == content
            assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt"
    assert source.read_bytes() == b"report"
    assert list(transfer_dir.iterdir()) == []


async def test_created_file_is_immediately_visible_to_tree_preview_and_artifact(
    test_client,
    standard_user,
    admin_headers,
):
    headers = standard_user["headers"]
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem")

    upload = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/"},
        files={"files": ("live.txt", b"live bytes\n", "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    [entry] = upload.json()["entries"]
    file_path = entry["path"]
    assert file_path == "/live.txt"

    collision = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/"},
        files={"files": ("live.txt", b"replacement", "text/plain")},
        headers=headers,
    )
    assert collision.status_code == 409, collision.text

    tree = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert tree.status_code == 200, tree.text
    assert "live.txt" in {item["name"] for item in tree.json()["entries"]}

    preview = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["content"] == "live bytes\n"

    artifact = await test_client.get(entry["artifact_url"], headers=headers)
    assert artifact.status_code == 200, artifact.text
    assert artifact.content == b"live bytes\n"

    cross_user = await test_client.get(entry["artifact_url"], headers=admin_headers)
    assert cross_user.status_code == 404

    deleted = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    missing = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert missing.status_code == 404


async def test_viewer_rejects_paths_outside_current_workdir(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem")
    for path in (
        "/home/user/workspace/agents/skills/private.txt",
        "/home/user/workspace/projects/other/file.txt",
    ):
        response = await test_client.get(
            "/api/viewer/filesystem/file",
            params={"thread_id": thread_id, "path": path},
            headers=headers,
        )
        assert response.status_code == 403


async def test_mention_search_observes_live_viewer_files_without_cache(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem")
    filename = "mention-live-file.txt"
    upload = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/"},
        files={"files": (filename, b"live", "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    artifact_path = upload.json()["entries"][0]["artifact_url"].split("/artifacts/", 1)[1]

    found = await test_client.get(
        "/api/mention/search",
        params={"thread_id": thread_id, "query": "mention-live", "sources": "thread"},
        headers=headers,
    )
    assert found.status_code == 200, found.text
    assert found.json() == [
        {
            "name": filename,
            "path": f"/{artifact_path}",
            "is_dir": False,
            "source": "thread",
        }
    ]

    deleted = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": f"/{filename}"},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    missing = await test_client.get(
        "/api/mention/search",
        params={"thread_id": thread_id, "query": "mention-live", "sources": "thread"},
        headers=headers,
    )
    assert missing.status_code == 200, missing.text
    assert missing.json() == []


async def test_viewer_download_blocks_project_symlink_escape(test_client, standard_user):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem-security")
    workdir_path = await get_thread_workdir_path(thread_id)

    project_root = f"/home/user/workspace/{workdir_path}"
    file_path = f"{project_root}/escape.txt"
    (user_workdir_host_dir(uid, workdir_path) / "escape.txt").symlink_to("/etc/hosts")

    response = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )

    assert response.status_code == 403, response.text


async def test_viewer_upload_blocks_project_symlink_escape(test_client, standard_user, tmp_path: Path):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem-security")
    workdir_path = await get_thread_workdir_path(thread_id)

    project_root = f"/home/user/workspace/{workdir_path}"
    outside_dir = tmp_path / f"yuxi-viewer-{uuid.uuid4().hex}"
    outside_dir.mkdir()
    parent_path = f"{project_root}/escape-dir"
    (user_workdir_host_dir(uid, workdir_path) / "escape-dir").symlink_to(outside_dir)

    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": parent_path},
        files={"files": ("escape.txt", b"outside", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert not (outside_dir / "escape.txt").exists()


async def test_viewer_upload_preserves_non_directory_parent_error(test_client, standard_user):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem-security")
    workdir_path = await get_thread_workdir_path(thread_id)

    (user_workdir_host_dir(uid, workdir_path) / "occupied").write_text("file", encoding="utf-8")

    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/occupied"},
        files={"files": ("child.txt", b"content", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "目录不存在"


async def test_viewer_upload_does_not_replace_hidden_target_symlink(test_client, standard_user, tmp_path: Path):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await create_default_agent_thread(test_client, headers, title_prefix="viewer-filesystem-security")
    workdir_path = await get_thread_workdir_path(thread_id)

    outside = tmp_path / f"yuxi-viewer-target-{uuid.uuid4().hex}.txt"
    outside.write_text("outside", encoding="utf-8")
    target = user_workdir_host_dir(uid, workdir_path) / "hidden.txt"
    target.symlink_to(outside)

    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/"},
        files={"files": ("hidden.txt", b"replacement", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    assert target.is_symlink()
    assert outside.read_text(encoding="utf-8") == "outside"
