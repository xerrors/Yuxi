from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.agents.backends.sandbox import paths as sandbox_paths
from yuxi.services import viewer_filesystem_service as svc
from yuxi.services import workspace_service


def test_resolve_local_user_data_path_blocks_upload_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = "thread-1"
    uid = "user-1"
    sandbox_paths.ensure_thread_dirs(thread_id, uid)

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    escape_link = sandbox_paths.sandbox_uploads_dir(thread_id) / "escape.txt"
    escape_link.symlink_to(outside_file)

    with pytest.raises(HTTPException) as exc_info:
        svc._resolve_local_user_data_path(thread_id, uid, "/home/gem/user-data/uploads/escape.txt")

    assert exc_info.value.status_code == 403


def test_list_local_entries_skips_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = "thread-1"
    uid = "user-1"
    sandbox_paths.ensure_thread_dirs(thread_id, uid)

    uploads_dir = sandbox_paths.sandbox_uploads_dir(thread_id)
    (uploads_dir / "safe.txt").write_text("safe", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    (uploads_dir / "escape.txt").symlink_to(outside_file)

    entries = svc._list_local_entries(thread_id, uid, uploads_dir)

    assert {entry["path"] for entry in entries} == {"/home/gem/user-data/uploads/safe.txt"}


@pytest.mark.asyncio
async def test_read_viewer_workspace_office_file_returns_pdf_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = "thread-1"
    uid = "user-1"
    user = SimpleNamespace(uid=uid)
    sandbox_paths.ensure_thread_dirs(thread_id, uid)
    target = sandbox_paths.sandbox_workspace_dir(thread_id, uid) / "slides.pptx"
    target.write_bytes(b"presentation")

    async def fake_resolve_viewer_state(**kwargs):
        return None, None, []

    async def fake_convert(filename: str, content: bytes) -> bytes:
        assert filename == "slides.pptx"
        assert content == b"presentation"
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "_resolve_viewer_state", fake_resolve_viewer_state)
    monkeypatch.setattr(workspace_service, "convert_office_to_pdf", fake_convert)

    response = await svc.read_viewer_file_content(
        thread_id=thread_id,
        path="/home/gem/user-data/workspace/slides.pptx",
        current_user=user,
        db=None,
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert response.media_type == "application/pdf"
    assert response.headers["x-yuxi-preview-type"] == "pdf"
    assert body == b"%PDF-1.4\npreview"
