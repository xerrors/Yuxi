import os
from io import BytesIO
from pathlib import Path

import pytest

from yuxi.modules.workspace import filesystem as workspace_filesystem_module
from yuxi.modules.workspace.workdir import Workdir


def test_open_existing_returns_workdir_capability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir_path = "projects/11111111-1111-4111-8111-111111111111"
    workspace_root = tmp_path / "workspace"
    (workspace_root / workdir_path).mkdir(parents=True)
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)

    workdir = Workdir.open_existing("user-1", workdir_path)

    assert workdir.relative_path == workdir_path
    assert workdir.root_path == f"/{workdir_path}"


def test_open_existing_rejects_missing_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)

    with pytest.raises(FileNotFoundError):
        Workdir.open_existing("user-1", "projects/11111111-1111-4111-8111-111111111111")


@pytest.mark.skipif(os.geteuid() == 0, reason="目录权限反例需要非 root 进程")
@pytest.mark.parametrize("mode", [0o000, 0o400])
def test_open_existing_requires_directory_permissions(tmp_path, monkeypatch, mode):
    """叶目录存在但无法打开或查找时，拒绝返回可用 Workdir。"""
    workspace_root = tmp_path / "workspace"
    path = workspace_root / "projects/demo"
    path.mkdir(parents=True)
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)
    path.chmod(mode)
    try:
        with pytest.raises(PermissionError):
            Workdir.open_existing("user-1", "projects/demo")
    finally:
        path.chmod(0o700)


def test_open_existing_rejects_regular_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir_path = "projects/11111111-1111-4111-8111-111111111111"
    workspace_root = tmp_path / "workspace"
    (workspace_root / "projects").mkdir(parents=True)
    (workspace_root / workdir_path).write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)

    with pytest.raises(ValueError, match="existing directory"):
        Workdir.open_existing("user-1", workdir_path)


def test_open_existing_rejects_symlinked_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir_path = "projects/11111111-1111-4111-8111-111111111111"
    workspace_root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace_root / "projects").mkdir(parents=True)
    (workspace_root / workdir_path).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: workspace_root)

    with pytest.raises(PermissionError, match="symlink"):
        Workdir.open_existing("user-1", workdir_path)


@pytest.mark.asyncio
async def test_workdir_copy_does_not_recreate_removed_root(workdir, tmp_path):
    """已经打开的 Workdir 在复制文件或目录之前被删除时，拒绝写入。"""
    root = tmp_path / "workspace" / workdir.relative_path
    root.rmdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_bytes(b"example")

    with pytest.raises(FileNotFoundError):
        workdir.copy_file_from_path("/nested/file.txt", str(source / "file.txt"))
    with pytest.raises(FileNotFoundError):
        workdir.copy_file_from_stream("/nested/file.txt", BytesIO(b"example"), max_bytes=10)
    with pytest.raises(FileNotFoundError):
        await workdir.acopy_directory_from_path(source, "/nested/output")
    assert not root.exists()


@pytest.mark.asyncio
async def test_directory_copy_preserves_all_files_and_empty_directories(workdir, tmp_path):
    """复制整个目录，内容与空目录都可以独立回读。"""
    source = tmp_path / "parsed"
    (source / "images/nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "document.md").write_text("![图](images/nested/chart.png)")
    (source / "images/nested/chart.png").write_bytes(b"image")
    await workdir.acopy_directory_from_path(source, "/outputs/parsed")
    assert workdir.read_file("/outputs/parsed/document.md", 1024) == "![图](images/nested/chart.png)".encode()
    assert workdir.read_file("/outputs/parsed/images/nested/chart.png", 1024) == b"image"
    assert workdir.stat("/outputs/parsed/empty")["is_dir"]


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", [False, True])
async def test_directory_copy_rejects_source_symlinks_and_reclaims_output(workdir, tmp_path, directory):
    """不能把源链接指向的目录或文件复制进工作区。"""
    source = tmp_path / "parsed"
    source.mkdir()
    (source / "document.md").write_text("text")
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
    else:
        outside.write_text("secret")
    (source / "z-link").symlink_to(outside, target_is_directory=directory)
    with pytest.raises(PermissionError, match="symlink"):
        await workdir.acopy_directory_from_path(source, "/outputs/parsed")
    with pytest.raises(FileNotFoundError):
        workdir.stat("/outputs/parsed")
    assert (outside / "secret.txt" if directory else outside).read_text() == "secret"


@pytest.mark.asyncio
async def test_directory_copy_rejects_symlinked_destination_parent(workdir, tmp_path):
    """目标路径中的链接不能把写入引到工作区外。"""
    source = tmp_path / "parsed"
    source.mkdir()
    (source / "document.md").write_text("text")
    outside = tmp_path / "outside"
    outside.mkdir()
    workdir_root = tmp_path / "workspace" / workdir.relative_path
    (workdir_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        await workdir.acopy_directory_from_path(source, "/escape/parsed")
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_directory_copy_never_removes_existing_destination(workdir, tmp_path, monkeypatch, cancel):
    """失败或取消发生在目标已存在时，保留其他操作拥有的文件。"""
    import asyncio
    import threading
    from yuxi.modules.workspace.filesystem import Workspace

    source = tmp_path / "parsed"
    source.mkdir()
    (source / "document.md").write_text("new")
    existing = tmp_path / "workspace" / workdir.relative_path / "outputs/parsed"
    existing.mkdir(parents=True)
    (existing / "document.md").write_text("existing")
    started, release = threading.Event(), threading.Event()
    real_copy = Workspace.copy_authorized_directory_from_path

    def copy(self, *args, **kwargs):
        started.set()
        assert release.wait(5)
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(Workspace, "copy_authorized_directory_from_path", copy)
    task = asyncio.create_task(workdir.acopy_directory_from_path(source, "/outputs/parsed"))
    assert await asyncio.to_thread(started.wait, 5)
    if cancel:
        task.cancel()
        await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError if cancel else FileExistsError):
        await task
    assert (existing / "document.md").read_text() == "existing"


@pytest.mark.asyncio
async def test_cancelled_directory_copy_waits_then_reclaims_complete_output(workdir, tmp_path, monkeypatch):
    """同步复制结束后再回收，取消后没有迟到文件。"""
    import asyncio
    import threading

    source = tmp_path / "parsed"
    source.mkdir()
    (source / "document.md").write_text("text")
    started, release = threading.Event(), threading.Event()
    real_copy = workspace_filesystem_module.copy_directory_fd

    def copy(source_fd, target_fd):
        started.set()
        assert release.wait(5)
        real_copy(source_fd, target_fd)

    monkeypatch.setattr(workspace_filesystem_module, "copy_directory_fd", copy)
    task = asyncio.create_task(workdir.acopy_directory_from_path(source, "/outputs/parsed"))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(FileNotFoundError):
        workdir.stat("/outputs/parsed")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """创建隔离的真实 Workdir 文件边界。"""
    root = tmp_path / "workspace"
    relative = "projects/test-copy"
    (root / relative).mkdir(parents=True)
    monkeypatch.setattr(workspace_filesystem_module, "user_workspace_dir", lambda _uid: root)
    return Workdir(relative, workspace_filesystem_module.Workspace("user-1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failure", "empty", "occupied", "cancel"])
async def test_directory_copy_preserves_replacement_directory(workdir, tmp_path, monkeypatch, outcome):
    """复制期间出现同名目录或发布后被替换，不能覆盖或回收其他产物。"""
    import asyncio
    import threading

    source = tmp_path / "parsed"
    source.mkdir()
    (source / "document.md").write_text("text")
    target = tmp_path / "workspace" / workdir.relative_path / "outputs/parsed"
    moved = target.with_name("moved")
    started, release = threading.Event(), threading.Event()
    real_copy = workspace_filesystem_module.copy_directory_fd

    def replace_target():
        target.rename(moved)
        target.mkdir()
        (target / "existing.txt").write_text("keep")

    if outcome == "cancel":
        real_operation = workspace_filesystem_module.Workspace.copy_authorized_directory_from_path

        def operation(self, *args, **kwargs):
            identity = real_operation(self, *args, **kwargs)
            replace_target()
            started.set()
            assert release.wait(5)
            return identity

        monkeypatch.setattr(workspace_filesystem_module.Workspace, "copy_authorized_directory_from_path", operation)
        task = asyncio.create_task(workdir.acopy_directory_from_path(source, "/outputs/parsed"))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:

        def copy(source_fd, target_fd):
            real_copy(source_fd, target_fd)
            assert not target.exists()
            target.mkdir()
            if outcome != "empty":
                (target / "existing.txt").write_text("keep")
            if outcome == "failure":
                raise OSError("copy failed")

        monkeypatch.setattr(workspace_filesystem_module, "copy_directory_fd", copy)
        with pytest.raises(OSError, match="copy failed|File exists"):
            await workdir.acopy_directory_from_path(source, "/outputs/parsed")
    if outcome == "empty":
        assert list(target.iterdir()) == []
    else:
        assert (target / "existing.txt").read_text() == "keep"
    if outcome == "cancel":
        assert (moved / "document.md").read_text() == "text"
    assert not list((tmp_path / "workspace").parent.glob(".yuxi-copy-*"))
