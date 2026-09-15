from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

import yuxi.services.viewer_filesystem_service as svc
from yuxi.workspace.errors import FileTransferLimitError
from yuxi.workspace.workdir import Workdir
from yuxi.services.workdir_service import AuthorizedWorkdir


class _Backend:
    def __init__(self):
        self.uploads = []
        self.files = {
            "/projects/11111111-1111-4111-8111-111111111111/report.txt": b"hello\nworld\n",
        }
        self.directories = {
            "/projects/11111111-1111-4111-8111-111111111111": [
                {"name": "outputs", "is_dir": True, "size": 0, "modified_at": 1},
                {"name": "report.txt", "is_dir": False, "size": 12, "modified_at": 2},
            ],
            "/projects/11111111-1111-4111-8111-111111111111/outputs": [],
        }

    def list_authorized_directory(self, path, *, root):
        assert root == "/projects/11111111-1111-4111-8111-111111111111"
        if path not in self.directories:
            raise FileNotFoundError(path)
        return self.directories[path]

    def download_authorized_file_to_path(self, path, target, max_bytes):
        content = self.files.get(path)
        if content is None:
            raise FileNotFoundError(path)
        if len(content) > max_bytes:
            raise FileTransferLimitError("file exceeds limit")
        Path(target).write_bytes(content)
        return len(content)

    def read_authorized_file(self, path, max_bytes):
        content = self.files.get(path)
        if content is None:
            raise FileNotFoundError(path)
        if len(content) > max_bytes:
            raise FileTransferLimitError("file exceeds limit")
        return content

    def search_authorized_tree(self, root, query, **_kwargs):
        results = []
        pending = [root]
        while pending:
            directory = pending.pop(0)
            for item in self.directories.get(directory, []):
                path = f"{directory.rstrip('/')}/{item['name']}"
                if item["is_dir"]:
                    pending.append(path)
                if query.lower() in item["name"].lower() or query.lower() in path.lower():
                    results.append({"path": path, **item})
        return results

    def create_authorized_directory(self, parent, name, *, root):
        assert root == "/projects/11111111-1111-4111-8111-111111111111"
        return {"is_dir": True, "size": 0, "modified_at": 1}

    def delete_authorized_path(self, path, *, root):
        assert root == "/projects/11111111-1111-4111-8111-111111111111"
        if self.files.pop(path, None) is None:
            raise FileNotFoundError(path)

    def upload_authorized_file_from_path(self, path, source, *, overwrite=True):
        if not overwrite and path in self.files:
            raise FileExistsError(path)
        self.uploads.append(path)
        self.files[path] = Path(source).read_bytes()
        return {"is_dir": False, "size": len(self.files[path]), "modified_at": 0}


@pytest.fixture
def realtime_viewer(monkeypatch):
    backend = _Backend()
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir("projects/11111111-1111-4111-8111-111111111111", backend),
        project_id="11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )

    async def resolve(**kwargs):
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["uid"] == "user-1"
        return binding

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    return backend


@pytest.mark.asyncio
async def test_viewer_root_is_realtime_project_workdir(realtime_viewer):
    result = await svc.list_viewer_filesystem_tree(
        thread_id="thread-1", path="/", current_user=SimpleNamespace(uid="user-1"), db=object()
    )
    assert [item["name"] for item in result["entries"]] == ["outputs", "report.txt"]
    assert result["entries"][0]["path"] == "/outputs/"
    assert result["entries"][1]["path"] == "/report.txt"


@pytest.mark.asyncio
async def test_viewer_rejects_other_project_and_user_data(realtime_viewer):
    for path in ("/home/gem/user-data/projects/other/file.txt", "/home/gem/user-data/a.txt"):
        with pytest.raises(HTTPException) as exc:
            await svc.read_viewer_file_content(
                thread_id="thread-1", path=path, current_user=SimpleNamespace(uid="user-1"), db=object()
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_reads_live_file_without_revision(realtime_viewer):
    result = await svc.read_viewer_file_content(
        thread_id="thread-1",
        path="/report.txt",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert result["content"] == "hello\nworld\n"
    assert result["preview_type"] == "text"


@pytest.mark.asyncio
async def test_viewer_rejects_other_workdir_runtime_identity(realtime_viewer):
    with pytest.raises(HTTPException) as exc:
        await svc.read_viewer_file_content(
            thread_id="thread-1",
            path="/home/gem/user-data/projects/other/report.txt",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_missing_live_file_returns_not_found(realtime_viewer):
    with pytest.raises(HTTPException) as exc:
        await svc.read_viewer_file_content(
            thread_id="thread-1",
            path="/missing.txt",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_viewer_delete_delegates_to_recoverable_lifecycle(realtime_viewer, monkeypatch):
    from unittest.mock import AsyncMock

    recycle = AsyncMock(return_value={"id": "trash-1", "state": "trashed"})
    monkeypatch.setattr(svc, "trash_personal_paths", recycle)
    created = await svc.create_viewer_directory(
        thread_id="thread-1",
        parent_path="/",
        name="drafts",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert created["entry"]["path"] == "/drafts/"
    deleted = await svc.delete_viewer_file(
        thread_id="thread-1",
        path="/report.txt",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert deleted["success"] is True
    assert deleted["trash"]["id"] == "trash-1"
    assert recycle.await_args.kwargs["paths"] == ["/projects/11111111-1111-4111-8111-111111111111/report.txt"]
    assert recycle.await_args.kwargs["uid"] == "user-1"
    assert realtime_viewer.files  # Viewer不再调用旧永久删除executor。


@pytest.mark.asyncio
async def test_viewer_search_walks_current_workdir(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111/outputs"] = [
        {"name": "final-report.md", "is_dir": False, "size": 10, "modified_at": 3}
    ]
    result = await svc.search_viewer_files(
        thread_id="thread-1", query="report", current_user=SimpleNamespace(uid="user-1"), db=object()
    )
    assert [item["name"] for item in result["entries"]] == ["report.txt", "final-report.md"]

    directory_result = await svc.search_viewer_files(
        thread_id="thread-1",
        query="output",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert directory_result["entries"][0]["name"] == "outputs"
    assert directory_result["entries"][0]["is_dir"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        [UploadFile(filename="report.txt", file=BytesIO(b"replacement"))],
        [
            UploadFile(filename="duplicate.txt", file=BytesIO(b"first")),
            UploadFile(filename="duplicate.txt", file=BytesIO(b"second")),
        ],
    ],
)
async def test_viewer_upload_rejects_existing_and_batch_duplicate_names_without_writing(
    realtime_viewer,
    files,
):
    original = dict(realtime_viewer.files)

    with pytest.raises(HTTPException) as exc:
        await svc.upload_viewer_files(
            thread_id="thread-1",
            parent_path="/",
            files=files,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 409
    assert realtime_viewer.uploads == []
    assert realtime_viewer.files == original


@pytest.mark.asyncio
async def test_viewer_upload_maps_final_no_clobber_conflict_to_409(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111"] = []

    with pytest.raises(HTTPException) as exc:
        await svc.upload_viewer_files(
            thread_id="thread-1",
            parent_path="/",
            files=[UploadFile(filename="report.txt", file=BytesIO(b"replacement"))],
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_viewer_upload_returns_scope_path_and_artifact_url(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111"] = []

    result = await svc.upload_viewer_files(
        thread_id="thread-1",
        parent_path="/",
        files=[UploadFile(filename="new.txt", file=BytesIO(b"content"))],
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result["entries"] == [
        {
            "path": "/new.txt",
            "name": "new.txt",
            "is_dir": False,
            "size": 7,
            "modified_at": "1970-01-01T00:00:00+00:00",
            "artifact_url": (
                "/api/chat/thread/thread-1/artifacts/home/gem/user-data/"
                "projects/11111111-1111-4111-8111-111111111111/new.txt"
            ),
        }
    ]
    assert realtime_viewer.files["/projects/11111111-1111-4111-8111-111111111111/report.txt"] == b"hello\nworld\n"


@pytest.fixture(autouse=True)
def isolate_personal_file_transaction_boundary(monkeypatch):
    """本模块使用fake/SQLite事务；PG锁与中断隔离在真实PG lifecycle集成验证。"""
    from unittest.mock import AsyncMock
    from yuxi.services import viewer_filesystem_service as owner

    monkeypatch.setattr(owner, "lock_user_files", AsyncMock())
    monkeypatch.setattr(owner, "require_no_pending_file_operations", AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "download", "delete"])
async def test_viewer_pending_lifecycle_refuses_filesystem_access(monkeypatch, operation):
    from unittest.mock import AsyncMock

    pending = AsyncMock(side_effect=HTTPException(409, "pending fixture"))
    resolve = AsyncMock()
    monkeypatch.setattr(svc, "require_no_pending_file_operations", pending)
    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    method = {
        "read": svc.read_viewer_file_content,
        "download": svc.download_viewer_file,
        "delete": svc.delete_viewer_file,
    }[operation]
    with pytest.raises(HTTPException) as error:
        await method(thread_id="t", path="/x", current_user=SimpleNamespace(uid="u"), db=object())
    assert error.value.status_code == 409
    resolve.assert_not_awaited()
