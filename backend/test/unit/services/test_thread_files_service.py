from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from yuxi.services import thread_files_service as svc


class _Conversation:
    uid = "user-1"


async def _fake_require_user_conversation(_repo, _thread_id: str, _current_uid: str):
    return _Conversation()


@pytest.mark.asyncio
async def test_read_thread_file_content_runs_file_read_in_worker_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("first\nsecond\nthird", encoding="utf-8")
    threaded_calls = []

    async def _fake_to_thread(func, *args, **kwargs):
        threaded_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(svc, "require_user_conversation", _fake_require_user_conversation)
    monkeypatch.setattr(svc, "ConversationRepository", lambda _db: object())
    monkeypatch.setattr(svc, "resolve_virtual_path", lambda _thread_id, _path, *, uid: file_path)
    monkeypatch.setattr(svc.asyncio, "to_thread", _fake_to_thread)

    result = await svc.read_thread_file_content_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=None,
        path="/home/gem/user-data/workspace/notes.txt",
        offset=1,
        limit=1,
    )

    assert result["content"] == ["second"]
    assert threaded_calls == [file_path.read_text]


@pytest.mark.asyncio
async def test_list_thread_files_runs_directory_scan_in_worker_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    directory = tmp_path / "outputs"
    directory.mkdir()
    (directory / "result.txt").write_text("result", encoding="utf-8")
    threaded_calls = []

    async def _fake_to_thread(func, *args, **kwargs):
        threaded_calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(svc, "require_user_conversation", _fake_require_user_conversation)
    monkeypatch.setattr(svc, "ConversationRepository", lambda _db: object())
    monkeypatch.setattr(svc, "ensure_thread_dirs", lambda _thread_id, _uid: None)
    monkeypatch.setattr(svc, "resolve_virtual_path", lambda _thread_id, _path, *, uid: directory)
    monkeypatch.setattr(
        svc,
        "virtual_path_for_thread_file",
        lambda _thread_id, path, *, uid: f"/home/gem/user-data/outputs/{path.name}",
    )
    monkeypatch.setattr(svc.asyncio, "to_thread", _fake_to_thread)

    result = await svc.list_thread_files_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=None,
        path="/home/gem/user-data/outputs",
    )

    assert [item["name"] for item in result["files"]] == ["result.txt"]
    assert threaded_calls == [svc._list_directory_entries]


@pytest.mark.asyncio
async def test_resolve_thread_artifact_view_blocks_symlink_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    thread_root = tmp_path / "threads" / "thread-1" / "user-data"
    uploads_dir = thread_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    (uploads_dir / "escape.txt").symlink_to(outside_file)

    monkeypatch.setattr(svc, "require_user_conversation", _fake_require_user_conversation)
    monkeypatch.setattr(svc, "ensure_thread_dirs", lambda _thread_id, _uid: None)
    monkeypatch.setattr(
        svc,
        "sandbox_workspace_dir",
        lambda _thread_id, _uid: tmp_path / "shared" / _uid / "workspace",
    )
    monkeypatch.setattr(svc, "sandbox_uploads_dir", lambda _thread_id: uploads_dir)
    monkeypatch.setattr(svc, "sandbox_outputs_dir", lambda _thread_id: thread_root / "outputs")
    monkeypatch.setattr(svc, "resolve_virtual_path", lambda _thread_id, _path, *, uid: uploads_dir / "escape.txt")
    monkeypatch.setattr(svc, "ConversationRepository", lambda _db: object())

    with pytest.raises(HTTPException, match="access denied"):
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=None,
            path="/home/gem/user-data/uploads/escape.txt",
        )
