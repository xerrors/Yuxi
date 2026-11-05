from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
import yuxi.modules.agents.services.artifacts as svc
from yuxi.api.responses.files import render_file_result
from yuxi.modules.extensions.skills import edit as skill_edit
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace import runtime_files
from yuxi.modules.workspace.runtime_paths import workspace_scope_from_runtime_path
from yuxi.modules.workspace.services.bindings import AuthorizedWorkdir
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import ContentTooLargeError, DomainError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError


class _Workspace:
    def __init__(self, skill_root: Path):
        self._write_lock = threading.Lock()
        self.files = {
            "/projects/11111111-1111-4111-8111-111111111111/report.md": b"one\ntwo\n",
            "/notes.txt": b"private",
        }
        self.directories = {"/", "/exports", "/exports/reports"}
        self.skill_root = skill_root
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_bytes(b"skill")

    def download_authorized_file_to_path(self, path, target, max_bytes):
        content = self.files.get(path)
        if content is None:
            raise FileNotFoundError(path)
        assert len(content) <= max_bytes
        Path(target).write_bytes(content)
        return len(content)

    def stat_authorized_path(self, path, *, root):
        assert root == "/"
        if path in self.directories:
            return {"is_dir": True}
        if path in self.files:
            return {"is_dir": False}
        if any(path.startswith(f"{file_path}/") for file_path in self.files):
            raise NotADirectoryError(path)
        raise FileNotFoundError(path)

    def upload_authorized_file_from_path(self, path, source, *, overwrite=True, create_parents=True):
        parent = str(Path(path).parent)
        if parent not in self.directories:
            if not create_parents:
                raise FileNotFoundError(parent)
            self.directories.add(parent)
        content = Path(source).read_bytes()
        with self._write_lock:
            if not overwrite and path in self.files:
                raise FileExistsError(path)
            self.files[path] = content

    def expected_bytes(self, runtime_path: str) -> bytes:
        if runtime_path.startswith("/home/user/skills/reporter/"):
            return (self.skill_root / runtime_path.rsplit("/", 1)[-1]).read_bytes()
        return self.files[workspace_scope_from_runtime_path(runtime_path)]

    def add_runtime_file(self, runtime_path: str, content: bytes) -> None:
        self.files[workspace_scope_from_runtime_path(runtime_path)] = content


@pytest.fixture
def live_files(monkeypatch, tmp_path):
    (tmp_path / "shared").mkdir()
    backend = _Workspace(tmp_path / "shared/reporter")
    monkeypatch.setattr(skill_edit, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "projections"))
    backend.skill_root = get_user_skills_root_dir("user-1") / "reporter"
    backend.skill_root.mkdir(parents=True)
    (backend.skill_root / "SKILL.md").write_bytes(b"projected skill")
    monkeypatch.setattr(runtime_files, "Workspace", lambda _uid: backend)
    binding = AuthorizedWorkdir(
        session_record_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir("projects/11111111-1111-4111-8111-111111111111", backend),
        project_id="11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )

    async def resolve(**kwargs):
        assert kwargs["uid"] == "user-1"
        return binding

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    monkeypatch.setattr(
        svc,
        "UserRepository",
        lambda _db: type(
            "Repo",
            (),
            {"get_by_uid": lambda self, uid: _async_value(type("User", (), {"uid": uid, "is_deleted": False})())},
        )(),
    )
    monkeypatch.setattr(
        svc,
        "lock_accessible_shared_skill_for_file",
        lambda _db, _user, slug: _async_value(
            type("Skill", (), {"slug": "reporter", "dir_path": "shared/reporter", "source_type": "upload"})()
            if slug == "reporter"
            else None
        ),
    )
    return backend


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_artifact_reads_workspace_and_authorized_skill_projection(live_files):
    """Workspace 与 Skills 展示使用用户挂载中的真实字节。"""
    for path in (
        "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
        "/home/user/workspace/notes.txt",
        "/home/user/skills/reporter/SKILL.md",
    ):
        response = await svc.resolve_thread_artifact_view(thread_id="thread-1", current_uid="user-1", db=object(), path=path)
        assert Path(response.path).read_bytes() == live_files.expected_bytes(path)
        response = render_file_result(response)
        await response.background()


@pytest.mark.asyncio
async def test_artifact_response_uses_audio_signature_with_wrong_extension(live_files):
    """真实导出字节与 HTTP 响应类型使用内容签名。"""
    path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/audio.txt"
    content = b"RIFF\x10\x00\x00\x00WAVEfmt "
    live_files.add_runtime_file(path, content)
    prepared = await svc.resolve_thread_artifact_view(thread_id="thread-1", current_uid="user-1", db=object(), path=path)
    response = render_file_result(prepared)
    try:
        assert Path(prepared.path).read_bytes() == content
        assert response.headers["content-type"] == "audio/wav"
    finally:
        await response.background()
    assert not Path(prepared.path).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["download", "preview", "save"])
async def test_cancelled_artifact_copy_waits_for_thread_and_reclaims_temp(live_files, monkeypatch, operation):
    """取消必须等实际复制结束，清理后不能再出现线程的晚写文件。"""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    targets = []
    original = live_files.download_authorized_file_to_path

    def paused_copy(path, target, max_bytes):
        """保留真实文件写入，只控制复制线程的释放时机。"""
        targets.append(Path(target))
        started.set()
        try:
            assert release.wait(5)
            return original(path, target, max_bytes)
        finally:
            finished.set()

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", paused_copy)
    kwargs = {
        "thread_id": "thread-1",
        "current_uid": "user-1",
        "db": object(),
        "path": "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
    }
    coroutine = (
        svc.save_thread_artifact_to_workspace_view(**kwargs)
        if operation == "save"
        else svc.resolve_thread_artifact_view(**kwargs, preview=operation == "preview")
    )
    task = asyncio.create_task(coroutine)
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=0.05)
        assert not done, "复制线程结束前不能传播取消或删除临时文件"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert targets and all(not target.exists() for target in targets)
        assert "/saved_artifacts/report.md" not in live_files.files
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
        for target in targets:
            target.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_artifact_missing_projection_never_reads_skill_source(live_files):
    """源文件仍存在时，缺失用户投影也必须返回不存在。"""
    (live_files.skill_root / "SKILL.md").unlink()
    with pytest.raises(ResourceNotFoundError):
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1", current_uid="user-1", db=object(), path="/home/user/skills/reporter/SKILL.md"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["link.md", "linked/SKILL.md"])
async def test_artifact_projection_rejects_symlinks(live_files, tmp_path, path):
    """文件和父目录链接均不能绕过用户投影边界。"""
    if path == "link.md":
        (live_files.skill_root / path).symlink_to(tmp_path / "shared/reporter/SKILL.md")
    else:
        (live_files.skill_root / "linked").symlink_to(tmp_path / "shared/reporter", target_is_directory=True)
    with pytest.raises(PermissionDeniedError):
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1", current_uid="user-1", db=object(), path=f"/home/user/skills/reporter/{path}"
        )


@pytest.mark.asyncio
async def test_artifact_projection_preview_enforces_byte_budget(live_files, monkeypatch):
    """投影文件超限返回预览预算结果，不改报缺失。"""
    monkeypatch.setattr(svc, "MAX_FILE_PREVIEW_BYTES", 1)
    result = await svc.resolve_thread_artifact_view(
        thread_id="thread-1", current_uid="user-1", db=object(), path="/home/user/skills/reporter/SKILL.md", preview=True
    )
    assert result == svc.preview_too_large().payload()


@pytest.mark.asyncio
async def test_save_artifact_copies_skill_projection_bytes(live_files):
    """保存 Skills 文件时同样使用用户已挂载的字节。"""
    await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1", current_uid="user-1", db=object(), path="/home/user/skills/reporter/SKILL.md"
    )
    assert live_files.files["/saved_artifacts/SKILL.md"] == b"projected skill"


@pytest.mark.asyncio
async def test_artifact_preview_converts_office_file(live_files, monkeypatch):
    path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.docx"
    live_files.add_runtime_file(path, b"docx bytes")
    captured = {}

    async def convert_office_to_pdf(filename, raw_content):
        captured.update(filename=filename, raw_content=raw_content)
        return b"%PDF-1.4 preview"

    monkeypatch.setattr(svc, "is_office_pdf_convertible", lambda _path: True)
    monkeypatch.setattr(svc, "convert_office_to_pdf", convert_office_to_pdf)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response.preview_type == "pdf"
    assert response.content == b"%PDF-1.4 preview"
    assert captured == {"filename": "report.docx", "raw_content": b"docx bytes"}


@pytest.mark.asyncio
async def test_artifact_preview_reports_oversized_file_without_rendering(live_files, monkeypatch):
    path = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.docx"

    def reject_large_file(_path, _target, max_bytes):
        assert max_bytes == svc.MAX_FILE_PREVIEW_BYTES
        raise FileTransferLimitError("file exceeds transfer limit")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response == svc.preview_too_large().payload()


@pytest.mark.asyncio
@pytest.mark.parametrize("file_name", ["报告.txt", 'quoted"name.txt', "line\nbreak.txt"])
async def test_artifact_download_encodes_untrusted_posix_filename(live_files, file_name):
    path = f"/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/{file_name}"
    live_files.add_runtime_file(path, b"safe")

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        download=True,
    )

    response = render_file_result(response)
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition and "\n" not in disposition
    assert file_name not in disposition
    await response.background()


@pytest.mark.asyncio
async def test_artifact_rejects_other_project(live_files):
    with pytest.raises(DomainError) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/other/secret.txt",
        )
    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_artifact_rejects_workdir_viewer_scope(live_files):
    with pytest.raises(DomainError) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/outputs/report.md",
        )

    assert isinstance(exc.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_artifact_rechecks_current_skill_authorization(live_files, monkeypatch):
    monkeypatch.setattr(svc, "lock_accessible_shared_skill_for_file", lambda _db, _user, _slug: _async_value(None))

    with pytest.raises(DomainError) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/skills/reporter/SKILL.md",
        )

    assert isinstance(exc.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_artifact_transfer_limit_is_not_reported_as_missing(live_files, monkeypatch):
    def reject_large_file(*_args, **_kwargs):
        raise FileTransferLimitError("file exceeds transfer limit")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)
    with pytest.raises(DomainError) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
        )
    assert isinstance(exc.value, ContentTooLargeError)


@pytest.mark.asyncio
async def test_save_artifact_copies_live_bytes_to_user_data(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
    )
    assert result["saved_path"] == "/home/user/workspace/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_save_artifact_uses_selected_workspace_destination(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
        destination_path="/exports/reports",
    )
    assert result["saved_path"] == "/home/user/workspace/exports/reports/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    ["../exports", "/exports/../reports", "/home/user/workspace/exports", "/home/user/skills/reporter"],
)
async def test_save_artifact_rejects_unsafe_destination(live_files, destination):
    with pytest.raises(DomainError) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path=destination,
        )
    assert isinstance(exc.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_save_artifact_accepts_explicit_default_destination(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
        destination_path="/saved_artifacts",
    )
    assert result["saved_path"] == "/home/user/workspace/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_save_artifact_rejects_missing_destination(live_files):
    with pytest.raises(DomainError) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/missing",
        )
    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_save_artifact_does_not_recreate_selected_directory_after_validation(live_files, monkeypatch):
    original_stat = live_files.stat_authorized_path

    def remove_after_stat(path, *, root):
        result = original_stat(path, root=root)
        live_files.directories.remove(path)
        return result

    monkeypatch.setattr(live_files, "stat_authorized_path", remove_after_stat)
    with pytest.raises(DomainError) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/exports/reports",
        )
    assert isinstance(exc.value, ResourceNotFoundError)
    assert "/exports/reports" not in live_files.directories


@pytest.mark.asyncio
async def test_save_artifact_rejects_file_in_destination_path(live_files):
    with pytest.raises(DomainError) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/notes.txt/child",
        )
    assert isinstance(exc.value, InvalidOperationError)


@pytest.mark.asyncio
async def test_concurrent_artifact_saves_use_distinct_atomic_names(live_files):
    second_source = "/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/outputs/report.md"
    live_files.add_runtime_file(second_source, b"second")

    first, second = await asyncio.gather(
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111/report.md",
        ),
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path=second_source,
        ),
    )

    assert {first["saved_path"], second["saved_path"]} == {
        "/home/user/workspace/saved_artifacts/report.md",
        "/home/user/workspace/saved_artifacts/report (1).md",
    }
    assert set(live_files.expected_bytes(path) for path in (first["saved_path"], second["saved_path"])) == {
        b"one\ntwo\n",
        b"second",
    }
