from __future__ import annotations

import asyncio
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.viewer as svc
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.services.bindings import AuthorizedWorkdir
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import DomainError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError, StateConflictError
from yuxi.shared.files import FileInput


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

    def upload_authorized_file_from_stream(self, path, source, *, max_bytes, overwrite=True):
        content = source.read()
        if len(content) > max_bytes:
            raise FileTransferLimitError("file exceeds limit")
        if not overwrite and path in self.files:
            raise FileExistsError(path)
        self.uploads.append(path)
        self.files[path] = content
        return {"is_dir": False, "size": len(self.files[path]), "modified_at": 0}


@pytest.fixture
def realtime_viewer(monkeypatch):
    backend = _Backend()
    binding = AuthorizedWorkdir(
        session_record_id=1,
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
    result = await svc.list_viewer_filesystem_tree(thread_id="thread-1", path="/", current_user=SimpleNamespace(uid="user-1"), db=object())
    assert [item["name"] for item in result["entries"]] == ["outputs", "report.txt"]
    assert result["entries"][0]["path"] == "/outputs/"
    assert result["entries"][1]["path"] == "/report.txt"


@pytest.mark.asyncio
async def test_viewer_rejects_other_project_and_user_data(realtime_viewer):
    for path in ("/home/user/workspace/projects/other/file.txt", "/home/user/workspace/a.txt"):
        with pytest.raises(DomainError) as exc:
            await svc.read_viewer_file_content(thread_id="thread-1", path=path, current_user=SimpleNamespace(uid="user-1"), db=object())
        assert isinstance(exc.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_viewer_reads_live_file_without_revision(realtime_viewer):
    result = await svc.read_viewer_file_content(
        thread_id="thread-1",
        path="/report.txt",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )
    assert result.content == "hello\nworld\n"
    assert result.preview_type == "text"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["large.txt", "large.docx"])
async def test_viewer_preview_rejects_large_source_before_content_preparation(realtime_viewer, monkeypatch, filename):
    path = f"/projects/11111111-1111-4111-8111-111111111111/{filename}"
    realtime_viewer.files[path] = b"123456789"
    monkeypatch.setattr(svc, "MAX_FILE_PREVIEW_BYTES", 8)

    def fail_preparation(*_args, **_kwargs):
        raise AssertionError("over-budget source must not reach content preparation")

    monkeypatch.setattr(svc, "prepare_file_preview", fail_preparation)

    result = await svc.read_viewer_file_content(
        thread_id="thread-1", path=f"/{filename}", current_user=SimpleNamespace(uid="user-1"), db=object()
    )

    assert result == svc.preview_too_large().payload()
    assert realtime_viewer.files[path] == b"123456789"


@pytest.mark.asyncio
async def test_viewer_download_uses_shared_media_type_fallback(realtime_viewer, monkeypatch):
    """Viewer 下载复用共享 fallback 并完整保存临时文件字节。"""
    from yuxi.shared import files

    monkeypatch.setattr(files.mimetypes, "guess_type", lambda _path: (None, None))
    realtime_viewer.files["/projects/11111111-1111-4111-8111-111111111111/report.MD"] = b"# hello"
    result = await svc.download_viewer_file(
        thread_id="thread-1", path="/report.MD", current_user=SimpleNamespace(uid="user-1"), db=object()
    )
    try:
        assert result.media_type == "text/markdown"
        assert result.filename == "report.MD"
        assert Path(result.path).read_bytes() == b"# hello"
    finally:
        Path(result.path).unlink()


@pytest.mark.asyncio
async def test_cancelled_viewer_download_removes_temporary_file(realtime_viewer, monkeypatch, tmp_path):
    """取消请求发生在文件复制中时，复制退出后应回收响应临时文件。"""
    started = asyncio.Event()
    finished = asyncio.Event()
    release_copy = threading.Event()
    loop = asyncio.get_running_loop()
    copy_file = realtime_viewer.download_authorized_file_to_path

    def copy_when_released(*args):
        """等待请求取消，再让线程执行文件复制。"""
        loop.call_soon_threadsafe(started.set)
        try:
            assert release_copy.wait(timeout=5)
            return copy_file(*args)
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(svc.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(realtime_viewer, "download_authorized_file_to_path", copy_when_released)
    request = asyncio.create_task(
        svc.download_viewer_file(thread_id="thread-1", path="/report.txt", current_user=SimpleNamespace(uid="user-1"), db=object())
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        request.cancel()
    finally:
        release_copy.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.wait_for(finished.wait(), timeout=5)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_viewer_rejects_other_workdir_runtime_identity(realtime_viewer):
    with pytest.raises(DomainError) as exc:
        await svc.read_viewer_file_content(
            thread_id="thread-1",
            path="/home/user/workspace/projects/other/report.txt",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert isinstance(exc.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_viewer_missing_live_file_returns_not_found(realtime_viewer):
    with pytest.raises(DomainError) as exc:
        await svc.read_viewer_file_content(
            thread_id="thread-1",
            path="/missing.txt",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_viewer_create_and_delete_use_same_live_backend(realtime_viewer):
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
    assert realtime_viewer.files == {}


@pytest.mark.asyncio
async def test_viewer_refuses_conversation_archive_and_keeps_the_file(realtime_viewer):
    """Viewer 删除压缩留档失败且文件仍在；tool_media 可以删除。"""
    archive = "/projects/11111111-1111-4111-8111-111111111111/outputs/conversation_history/summary.md"
    media = "/projects/11111111-1111-4111-8111-111111111111/outputs/tool_media/shot.png"
    realtime_viewer.files[archive] = b"summary"
    realtime_viewer.files[media] = b"png"
    user = SimpleNamespace(uid="user-1")
    with pytest.raises(InvalidOperationError) as error:
        await svc.delete_viewer_file(
            thread_id="thread-1",
            path="/outputs/conversation_history/summary.md",
            current_user=user,
            db=object(),
        )
    assert error.value.code == "conversation_archive_protected"
    assert realtime_viewer.files[archive] == b"summary"
    with pytest.raises(InvalidOperationError) as error:
        await svc.delete_viewer_file(thread_id="thread-1", path="/outputs/large_tool_results", current_user=user, db=object())
    assert error.value.code == "conversation_archive_protected"
    deleted = await svc.delete_viewer_file(thread_id="thread-1", path="/outputs/tool_media/shot.png", current_user=user, db=object())
    assert deleted["success"] is True
    assert media not in realtime_viewer.files
    assert archive in realtime_viewer.files


@pytest.mark.asyncio
async def test_viewer_search_walks_current_workdir(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111/outputs"] = [
        {"name": "final-report.md", "is_dir": False, "size": 10, "modified_at": 3}
    ]
    result = await svc.search_viewer_files(thread_id="thread-1", query="report", current_user=SimpleNamespace(uid="user-1"), db=object())
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
        [FileInput(filename="report.txt", source=BytesIO(b"replacement"))],
        [
            FileInput(filename="duplicate.txt", source=BytesIO(b"first")),
            FileInput(filename="duplicate.txt", source=BytesIO(b"second")),
        ],
    ],
)
async def test_viewer_upload_rejects_existing_and_batch_duplicate_names_without_writing(
    realtime_viewer,
    files,
):
    original = dict(realtime_viewer.files)

    with pytest.raises(DomainError) as exc:
        await svc.upload_viewer_files(
            thread_id="thread-1",
            parent_path="/",
            files=files,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert isinstance(exc.value, StateConflictError)
    assert realtime_viewer.uploads == []
    assert realtime_viewer.files == original


@pytest.mark.asyncio
async def test_viewer_upload_maps_final_no_clobber_conflict_to_409(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111"] = []

    with pytest.raises(DomainError) as exc:
        await svc.upload_viewer_files(
            thread_id="thread-1",
            parent_path="/",
            files=[FileInput(filename="report.txt", source=BytesIO(b"replacement"))],
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert isinstance(exc.value, StateConflictError)


@pytest.mark.asyncio
async def test_viewer_upload_returns_scope_path_and_artifact_url(realtime_viewer):
    realtime_viewer.directories["/projects/11111111-1111-4111-8111-111111111111"] = []

    result = await svc.upload_viewer_files(
        thread_id="thread-1",
        parent_path="/",
        files=[FileInput(filename="new.txt", source=BytesIO(b"content"))],
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
                "/api/v1/agents/sessions/thread-1/artifacts/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/new.txt"
            ),
        }
    ]
    assert realtime_viewer.files["/projects/11111111-1111-4111-8111-111111111111/report.txt"] == b"hello\nworld\n"
