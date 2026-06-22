from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from yuxi.agents.backends.sandbox import paths as workspace_paths
from yuxi.services import workspace_service as svc


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="db-id-1", uid="user-1")


def test_workspace_root_creates_default_agents_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))

    root = svc._workspace_root(_user())

    agents_file = root / "agents" / "AGENTS.md"
    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert agents_file.is_file()
    assert agents_file.read_text(encoding="utf-8") == ""


def test_ensure_thread_dirs_creates_default_agents_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))

    workspace_paths.ensure_thread_dirs("thread-1", "user-1")

    agents_file = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents" / "AGENTS.md"
    assert agents_file.is_file()
    assert agents_file.read_text(encoding="utf-8") == ""


def test_workspace_root_keeps_existing_agents_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    agents_dir.mkdir(parents=True)
    agents_file = agents_dir / "AGENTS.md"
    agents_file.write_text("保留已有内容", encoding="utf-8")

    root = svc._workspace_root(_user())

    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert agents_file.read_text(encoding="utf-8") == "保留已有内容"


def test_workspace_root_rejects_symlink_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user_root = tmp_path / "threads" / "shared" / "user-1"
    outside_root = tmp_path / "outside"
    user_root.mkdir(parents=True)
    outside_root.mkdir()
    (user_root / "workspace").symlink_to(outside_root, target_is_directory=True)

    with pytest.raises(HTTPException) as exc_info:
        svc._workspace_root(_user())

    assert exc_info.value.status_code == 403


def test_ensure_workspace_default_files_rejects_path_outside_threads_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path / "saves"))

    with pytest.raises(ValueError):
        workspace_paths.ensure_workspace_default_files(tmp_path / "outside-workspace")


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_unsupported_for_non_utf8_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "bad.txt"
    target.write_bytes(b"\xff\xfe\x00")

    result = await svc.read_workspace_file_content(path="/bad.txt", current_user=user)

    assert result["content"] is None
    assert result["preview_type"] == "unsupported"
    assert result["supported"] is False


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_pdf_preview_for_office_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "demo.docx"
    target.write_bytes(b"office")

    async def fake_convert(filename: str, content: bytes) -> bytes:
        assert filename == "demo.docx"
        assert content == b"office"
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "convert_office_to_pdf", fake_convert)

    result = await svc.read_workspace_file_content(path="/demo.docx", current_user=user)
    body = b""
    async for chunk in result.body_iterator:
        body += chunk

    assert result.media_type == "application/pdf"
    assert result.headers["x-yuxi-preview-type"] == "pdf"
    assert body == b"%PDF-1.4\npreview"


@pytest.mark.asyncio
async def test_read_workspace_file_content_rejects_xlsx_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "sheet.xlsx"
    target.write_bytes(b"PK\x03\x04excel")

    result = await svc.read_workspace_file_content(path="/sheet.xlsx", current_user=user)

    assert result["content"] is None
    assert result["preview_type"] == "unsupported"
    assert result["supported"] is False


@pytest.mark.asyncio
async def test_preview_workspace_file_converts_office_file_to_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "slides.pptx"
    target.write_bytes(b"presentation")

    async def fake_convert(filename: str, content: bytes) -> bytes:
        assert filename == "slides.pptx"
        assert content == b"presentation"
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "convert_office_to_pdf", fake_convert)

    response = await svc.read_workspace_file_content(path="/slides.pptx", current_user=user)
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert response.media_type == "application/pdf"
    assert body == b"%PDF-1.4\npreview"


@pytest.mark.asyncio
async def test_preview_workspace_file_caches_office_pdf_conversion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "slides.pptx"
    target.write_bytes(b"presentation")

    convert_calls = 0

    async def fake_convert(filename: str, content: bytes) -> bytes:
        nonlocal convert_calls
        convert_calls += 1
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "convert_office_to_pdf", fake_convert)

    async def read_pdf() -> bytes:
        response = await svc.read_workspace_file_content(path="/slides.pptx", current_user=user)
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        return body

    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert convert_calls == 1

    target.write_bytes(b"presentation-v2")
    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert convert_calls == 2


@pytest.mark.asyncio
async def test_download_workspace_file_keeps_office_original_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "slides.pptx"
    target.write_bytes(b"presentation")

    response = await svc.download_workspace_file(path="/slides.pptx", current_user=user)
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert response.media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert body == b"presentation"


@pytest.mark.asyncio
async def test_write_workspace_file_content_updates_markdown_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "note.md"
    target.write_text("旧内容", encoding="utf-8")

    result = await svc.write_workspace_file_content(path="/note.md", content="# 新内容", current_user=user)

    assert result["success"] is True
    assert result["path"] == "/note.md"
    assert result["entry"]["path"] == "/note.md"
    assert target.read_text(encoding="utf-8") == "# 新内容"


@pytest.mark.asyncio
async def test_write_workspace_file_content_updates_txt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "note.txt"
    target.write_text("old", encoding="utf-8")

    await svc.write_workspace_file_content(path="/note.txt", content="new", current_user=user)

    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_workspace_file_content_rejects_unsupported_suffix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "script.py"
    target.write_text("print('hello')", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        await svc.write_workspace_file_content(path="/script.py", content="print('bye')", current_user=user)

    assert exc_info.value.status_code == 400
    assert target.read_text(encoding="utf-8") == "print('hello')"


@pytest.mark.asyncio
async def test_write_workspace_file_content_rejects_directory_and_missing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    svc._workspace_root(user)

    with pytest.raises(HTTPException) as directory_error:
        await svc.write_workspace_file_content(path="/agents/", content="x", current_user=user)
    with pytest.raises(HTTPException) as missing_error:
        await svc.write_workspace_file_content(path="/missing.md", content="x", current_user=user)

    assert directory_error.value.status_code == 400
    assert missing_error.value.status_code == 404


@pytest.mark.asyncio
async def test_write_workspace_file_content_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        await svc.write_workspace_file_content(
            path="/../outside.md",
            content="x",
            current_user=_user(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_workspace_files_writes_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    uploads = [
        UploadFile(filename="demo.txt", file=BytesIO(b"hello")),
        UploadFile(filename="notes.md", file=BytesIO(b"# notes")),
    ]

    result = await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert result["success"] is True
    assert [entry["path"] for entry in result["entries"]] == ["/demo.txt", "/notes.md"]
    assert result["entries"][0]["size"] == 5
    assert (root / "demo.txt").read_bytes() == b"hello"
    assert (root / "notes.md").read_bytes() == b"# notes"


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_oversized_file_and_cleans_partial_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    monkeypatch.setattr(svc, "MAX_WORKSPACE_UPLOAD_SIZE_BYTES", 5)
    user = _user()
    root = svc._workspace_root(user)
    uploads = [
        UploadFile(filename="small.txt", file=BytesIO(b"12345")),
        UploadFile(filename="large.txt", file=BytesIO(b"123456")),
    ]

    with pytest.raises(HTTPException) as exc_info:
        await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail
    assert not (root / "small.txt").exists()
    assert not (root / "large.txt").exists()


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_more_than_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    user = _user()
    uploads = [
        UploadFile(filename=f"demo-{index}.txt", file=BytesIO(b"hello"))
        for index in range(svc.MAX_WORKSPACE_UPLOAD_FILES + 1)
    ]

    with pytest.raises(HTTPException) as exc_info:
        await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert exc_info.value.status_code == 400
    assert f"一次最多上传 {svc.MAX_WORKSPACE_UPLOAD_FILES} 个文件" in exc_info.value.detail
