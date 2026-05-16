"""Integration tests for viewer-oriented filesystem router endpoints."""

from __future__ import annotations

import importlib
import uuid

import pytest
from yuxi.agents.backends.sandbox import (
    ensure_thread_dirs,
    sandbox_user_data_dir,
    sandbox_workspace_dir,
    virtual_path_for_thread_file,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _get_provider():
    from yuxi.agents.backends import get_sandbox_provider

    return get_sandbox_provider()


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/chat/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    if not agents:
        pytest.skip("No agents available for viewer filesystem integration tests.")

    agent_id = agents[0].get("id")
    if not agent_id:
        pytest.skip("Agent payload missing id field.")

    create_resp = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": f"viewer-filesystem-test-{uuid.uuid4().hex[:8]}",
            "metadata": {},
        },
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    payload = create_resp.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, f"Create thread response missing thread identifier: {payload}"
    return thread_id


async def _upload_attachment_file(
    test_client,
    thread_id: str,
    headers: dict[str, str],
    file_name: str,
    content: str,
) -> str:
    response = await test_client.post(
        f"/api/chat/thread/{thread_id}/attachments",
        files={"file": (file_name, content.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    path = payload.get("path") or payload.get("file_path")
    assert isinstance(path, str) and path
    return path


async def test_viewer_tree_requires_authentication(test_client):
    response = await test_client.get("/api/viewer/filesystem/tree", params={"thread_id": "x", "path": "/"})
    assert response.status_code == 401


async def test_viewer_tree_root_lists_user_data_namespace(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/home/gem/user-data/" in paths


async def test_viewer_tree_root_does_not_require_sandbox_listing(test_client, standard_user, monkeypatch):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    class _FailingSandbox:
        def ls_info(self, path):
            raise AssertionError(f"sandbox ls_info should not be used for root path: {path}")

    class _EmptyBackend:
        def has_entries(self):
            return False

    service_module = importlib.import_module("yuxi.services.viewer_filesystem_service")

    async def _fake_resolve_viewer_state(**kwargs):
        return _FailingSandbox(), _EmptyBackend(), _EmptyBackend(), []

    monkeypatch.setattr(service_module, "_resolve_viewer_state", _fake_resolve_viewer_state)

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/home/gem/user-data/" in paths


async def test_viewer_tree_user_data_uses_local_thread_directory(test_client, standard_user, monkeypatch):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "viewer_tree_demo.txt"
    actual_path.write_text("viewer tree", encoding="utf-8")

    class _FailingSandbox:
        def ls_info(self, path):
            raise AssertionError(f"sandbox ls_info should not be used for user-data path: {path}")

    class _EmptyBackend:
        def has_entries(self):
            return False

    service_module = importlib.import_module("yuxi.services.viewer_filesystem_service")

    async def _fake_resolve_viewer_state(**kwargs):
        return _FailingSandbox(), _EmptyBackend(), _EmptyBackend(), []

    monkeypatch.setattr(service_module, "_resolve_viewer_state", _fake_resolve_viewer_state)

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/home/gem/user-data/workspace/" in paths
    assert "/home/gem/user-data/uploads/" in paths
    assert "/home/gem/user-data/outputs/" in paths
    assert all(str(path).startswith("/home/gem/user-data") for path in paths)


async def test_viewer_tree_user_data_root_keeps_thread_root_files_visible(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    root_file = sandbox_user_data_dir(thread_id) / "root-note.txt"
    root_file.write_text("visible at root", encoding="utf-8")

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    paths = {entry.get("path") for entry in response.json().get("entries", [])}
    assert "/home/gem/user-data/root-note.txt" in paths
    assert "/home/gem/user-data/workspace/" in paths


async def test_workspace_is_shared_across_same_user_threads_but_uploads_remain_thread_local(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)
    other_thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    shared_path = sandbox_workspace_dir(thread_id, user_id) / "shared_across_threads.txt"
    shared_path.write_text("shared workspace", encoding="utf-8")

    upload_path = await _upload_attachment_file(test_client, thread_id, headers, "thread-local.txt", "private upload\n")

    workspace_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": other_thread_id, "path": "/home/gem/user-data/workspace"},
        headers=headers,
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_paths = {entry.get("path") for entry in workspace_response.json().get("entries", [])}
    assert "/home/gem/user-data/workspace/shared_across_threads.txt" in workspace_paths

    shared_file_response = await test_client.get(
        f"/api/chat/thread/{other_thread_id}/artifacts/home/gem/user-data/workspace/shared_across_threads.txt",
        headers=headers,
    )
    assert shared_file_response.status_code == 200, shared_file_response.text
    assert shared_file_response.text == "shared workspace"

    upload_response = await test_client.get(
        f"/api/chat/thread/{other_thread_id}/artifacts/{upload_path.lstrip('/')}",
        headers=headers,
    )
    assert upload_response.status_code == 404, upload_response.text


async def test_viewer_file_returns_raw_content_without_line_numbers(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    file_path = await _upload_attachment_file(test_client, thread_id, headers, "viewer_demo.txt", "alpha\nbeta\n")

    response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert "alpha" in response.json()["content"]
    assert "beta" in response.json()["content"]
    assert response.json()["preview_type"] == "markdown"
    assert response.json()["supported"] is True


async def test_viewer_file_returns_unsupported_for_binary_payload(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "binary.bin"
    actual_path.write_bytes(b"\x00\x01\x02\x03binary")
    file_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content"] is None
    assert payload["preview_type"] == "unsupported"
    assert payload["supported"] is False


async def test_viewer_file_returns_image_preview_metadata(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "demo.png"
    actual_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )
    file_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content"] is None
    assert payload["preview_type"] == "image"
    assert payload["supported"] is True


async def test_viewer_file_returns_pdf_preview_metadata(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "demo.pdf"
    actual_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")
    file_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content"] is None
    assert payload["preview_type"] == "pdf"
    assert payload["supported"] is True


async def test_viewer_download_returns_attachment_response(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    file_path = await _upload_attachment_file(test_client, thread_id, headers, "download_demo.txt", "download-me\n")

    response = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment;" in content_disposition
    assert "download_demo" in content_disposition
    assert "download-me" in response.text


async def test_viewer_download_returns_full_file_for_large_user_data_content(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)
    large_content = "0123456789abcdef" * 4096

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "large_download.txt"
    actual_path.write_text(large_content, encoding="utf-8")
    file_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    response = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.content == large_content.encode("utf-8")


async def test_viewer_delete_removes_user_data_file(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "delete_me.txt"
    actual_path.write_text("delete me", encoding="utf-8")
    file_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    delete_response = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["success"] is True
    assert not actual_path.exists()

    tree_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data/workspace"},
        headers=headers,
    )
    assert tree_response.status_code == 200, tree_response.text
    paths = {entry.get("path") for entry in tree_response.json().get("entries", [])}
    assert file_path not in paths


async def test_viewer_delete_removes_empty_user_data_directory(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "empty-folder"
    actual_path.mkdir()
    dir_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    delete_response = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": dir_path},
        headers=headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["success"] is True
    assert not actual_path.exists()

    tree_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data/workspace"},
        headers=headers,
    )
    assert tree_response.status_code == 200, tree_response.text
    paths = {entry.get("path") for entry in tree_response.json().get("entries", [])}
    assert f"{dir_path}/" not in paths


async def test_viewer_delete_recursively_removes_user_data_directory(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    actual_path = sandbox_workspace_dir(thread_id, user_id) / "nested-folder"
    nested_dir = actual_path / "child"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "notes.txt"
    nested_file.write_text("remove recursively", encoding="utf-8")
    dir_path = virtual_path_for_thread_file(thread_id, actual_path, user_id=user_id)

    delete_response = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": dir_path},
        headers=headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["success"] is True
    assert not actual_path.exists()
    assert not nested_file.exists()

    tree_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data/workspace"},
        headers=headers,
    )
    assert tree_response.status_code == 200, tree_response.text
    paths = {entry.get("path") for entry in tree_response.json().get("entries", [])}
    assert f"{dir_path}/" not in paths


async def test_viewer_delete_rejects_readonly_namespace_directory(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": "/home/gem/skills"},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "当前路径不支持删除"


@pytest.mark.parametrize(
    "protected_path",
    [
        "/home/gem/user-data/workspace",
        "/home/gem/user-data/uploads",
        "/home/gem/user-data/outputs",
    ],
)
async def test_viewer_delete_rejects_protected_user_data_root_directories(
    test_client, standard_user, protected_path: str
):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)

    response = await test_client.delete(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": protected_path},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "当前目录不允许删除"


async def test_viewer_create_directory_adds_workspace_folder(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)

    response = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={
            "thread_id": thread_id,
            "parent_path": "/home/gem/user-data/workspace",
            "name": "created-folder",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["entry"]["path"] == "/home/gem/user-data/workspace/created-folder/"
    assert (sandbox_workspace_dir(thread_id, user_id) / "created-folder").is_dir()

    tree_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/user-data/workspace"},
        headers=headers,
    )
    assert tree_response.status_code == 200, tree_response.text
    paths = {entry.get("path") for entry in tree_response.json().get("entries", [])}
    assert "/home/gem/user-data/workspace/created-folder/" in paths


async def test_viewer_upload_file_writes_to_workspace_subdirectory(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    target_dir = sandbox_workspace_dir(thread_id, user_id) / "upload-target"
    target_dir.mkdir()

    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/home/gem/user-data/workspace/upload-target"},
        files={"file": ("uploaded.txt", b"uploaded from viewer\n", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["entry"]["path"] == "/home/gem/user-data/workspace/upload-target/uploaded.txt"

    file_response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": "/home/gem/user-data/workspace/upload-target/uploaded.txt"},
        headers=headers,
    )
    assert file_response.status_code == 200, file_response.text
    assert file_response.json()["content"] == "uploaded from viewer\n"


async def test_viewer_create_directory_rejects_conflict(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    (sandbox_workspace_dir(thread_id, user_id) / "conflict").mkdir()

    response = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={
            "thread_id": thread_id,
            "parent_path": "/home/gem/user-data/workspace",
            "name": "conflict",
        },
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "同名文件或文件夹已存在"


async def test_viewer_upload_file_rejects_conflict_without_overwrite(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    ensure_thread_dirs(thread_id, user_id)
    existing_file = sandbox_workspace_dir(thread_id, user_id) / "existing.txt"
    existing_file.write_text("keep me\n", encoding="utf-8")

    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/home/gem/user-data/workspace"},
        files={"file": ("existing.txt", b"replace me\n", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "同名文件或文件夹已存在"
    assert existing_file.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize(
    "parent_path",
    [
        "/home/gem/skills",
        "/home/gem/user-data/uploads",
        "/home/gem/user-data/outputs",
    ],
)
async def test_viewer_write_rejects_non_workspace_paths(test_client, standard_user, parent_path: str):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    directory_response = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={"thread_id": thread_id, "parent_path": parent_path, "name": "blocked"},
        headers=headers,
    )
    assert directory_response.status_code == 400, directory_response.text
    assert directory_response.json()["detail"] == "当前路径不支持写入"

    upload_response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": parent_path},
        files={"file": ("blocked.txt", b"blocked", "text/plain")},
        headers=headers,
    )
    assert upload_response.status_code == 400, upload_response.text
    assert upload_response.json()["detail"] == "当前路径不支持写入"


@pytest.mark.parametrize("folder_name", ["", "../escape", "nested/folder"])
async def test_viewer_create_directory_rejects_invalid_names(test_client, standard_user, folder_name: str):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={
            "thread_id": thread_id,
            "parent_path": "/home/gem/user-data/workspace",
            "name": folder_name,
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text


async def test_viewer_tree_root_hides_kbs_namespace(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/home/gem/user-data/" in paths
    assert "/home/gem/kbs/" not in paths


async def test_viewer_rejects_kbs_namespace(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    tree_response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/home/gem/kbs"},
        headers=headers,
    )
    assert tree_response.status_code == 400, tree_response.text

    file_response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": "/home/gem/kbs/demo.md"},
        headers=headers,
    )
    assert file_response.status_code == 400, file_response.text

    download_response = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": "/home/gem/kbs/demo.md"},
        headers=headers,
    )
    assert download_response.status_code == 400, download_response.text
