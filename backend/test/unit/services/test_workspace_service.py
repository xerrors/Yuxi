from __future__ import annotations

import datetime as dt
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from yuxi.agents.backends.sandbox import paths as workspace_paths
from yuxi.services import workspace_service as svc


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="db-id-1", uid="user-1")


def test_workspace_root_creates_default_agent_context_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    root = svc._workspace_root(_user())

    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert (root / "agents" / "AGENTS.md").read_text(encoding="utf-8") == (
        "# AGENTS\n\n以下是约束 Agent 行为的一些要求\n"
    )
    assert (root / "agents" / "USER.md").read_text(encoding="utf-8") == ("# USER\n\n以下是有关用户的一些信息\n")
    assert (root / "agents" / "MEMORY.md").read_text(encoding="utf-8") == (
        "# MEMORY\n\n以下是 Agent 需要记住的一些信息\n"
    )
    assert {path.name for path in (root / "agents").iterdir()} == {"AGENTS.md", "USER.md", "MEMORY.md"}


def test_external_uid_uses_stable_path_safe_workspace_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    uid = "oidc:898f3d04-140e-433b-a06e-1e50a2bd01b6"

    workspace_paths.ensure_thread_dirs("thread-1", uid)

    dirname = "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest()
    workspace = workspace_paths.sandbox_workspace_dir("thread-1", uid)
    assert workspace == tmp_path / "threads" / "shared" / dirname / "workspace"
    assert (workspace / "agents" / "AGENTS.md").is_file()


@pytest.mark.parametrize("uid", ["../outside", r"C:\\outside", "oidc:tenant/user"])
def test_external_uid_cannot_escape_threads_root(tmp_path: Path, monkeypatch, uid: str) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path / "saves"))

    workspace = workspace_paths.sandbox_workspace_dir("thread-1", uid)

    assert workspace.parent.name == "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest()
    assert workspace.resolve().is_relative_to((tmp_path / "saves" / "threads").resolve())


def test_workspace_root_keeps_existing_agents_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    agents_dir.mkdir(parents=True)
    agents_file = agents_dir / "AGENTS.md"
    agents_file.write_text("保留已有内容", encoding="utf-8")

    root = svc._workspace_root(_user())

    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert agents_file.read_text(encoding="utf-8") == "保留已有内容"


def test_workspace_root_rejects_symlink_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path / "saves"))

    with pytest.raises(ValueError):
        workspace_paths.ensure_workspace_default_files(tmp_path / "outside-workspace")


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bad.txt", b"\xff\xfe\x00"),
        ("sheet.xlsx", b"PK\x03\x04excel"),
    ],
)
@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_unsupported_for_unreadable_files(
    tmp_path: Path,
    monkeypatch,
    filename: str,
    content: bytes,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / filename
    target.write_bytes(content)

    result = await svc.read_workspace_file_content(path=f"/{filename}", current_user=user)

    assert result["content"] is None
    assert result["preview_type"] == "unsupported"
    assert result["supported"] is False


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_pdf_preview_for_office_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / "sheet.xlsx"
    target.write_bytes(b"PK\x03\x04excel")

    result = await svc.read_workspace_file_content(path="/sheet.xlsx", current_user=user)

    assert result["content"] is None
    assert result["preview_type"] == "unsupported"
    assert result["supported"] is False


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("demo.docx", b"office"),
        ("slides.pptx", b"presentation"),
    ],
)
@pytest.mark.asyncio
async def test_preview_workspace_file_caches_office_pdf_conversion(
    tmp_path: Path,
    monkeypatch,
    filename: str,
    content: bytes,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / filename
    target.write_bytes(content)

    convert_calls = 0

    async def fake_convert(name: str, _raw: bytes) -> bytes:
        nonlocal convert_calls
        assert name == filename
        convert_calls += 1
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "convert_office_to_pdf", fake_convert)

    async def read_pdf() -> bytes:
        response = await svc.read_workspace_file_content(path=f"/{filename}", current_user=user)
        assert response.media_type == "application/pdf"
        assert response.headers["x-yuxi-preview-type"] == "pdf"
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        return body

    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert convert_calls == 1

    target.write_bytes(content + b"-v2")
    assert await read_pdf() == b"%PDF-1.4\npreview"
    assert convert_calls == 2


@pytest.mark.asyncio
async def test_download_workspace_file_keeps_office_original_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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


@pytest.mark.parametrize(
    ("extension", "original", "content"),
    [
        ("md", "旧内容", "# 新内容"),
        ("txt", "old", "new"),
    ],
)
@pytest.mark.asyncio
async def test_write_workspace_file_content_updates_file(
    tmp_path: Path,
    monkeypatch,
    extension: str,
    original: str,
    content: str,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    root = svc._workspace_root(user)
    target = root / f"note.{extension}"
    target.write_text(original, encoding="utf-8")

    result = await svc.write_workspace_file_content(
        path=f"/note.{extension}", content=content, current_user=user
    )

    assert result["success"] is True
    assert result["path"] == f"/note.{extension}"
    assert result["entry"]["path"] == f"/note.{extension}"
    assert target.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_write_workspace_file_content_rejects_unsupported_suffix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    workspace_paths.ensure_thread_dirs("current-thread", "user-1")

    with pytest.raises(HTTPException) as directory_error:
        await svc.write_workspace_file_content(path="/agents/", content="x", current_user=user)
    with pytest.raises(HTTPException) as missing_error:
        await svc.write_workspace_file_content(path="/missing.md", content="x", current_user=user)

    assert directory_error.value.status_code == 400
    assert missing_error.value.status_code == 404


@pytest.mark.asyncio
async def test_write_workspace_file_content_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        await svc.write_workspace_file_content(
            path="/../outside.md",
            content="x",
            current_user=_user(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_workspace_files_writes_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    uploads = [
        UploadFile(filename=f"demo-{index}.txt", file=BytesIO(b"hello"))
        for index in range(svc.MAX_WORKSPACE_UPLOAD_FILES + 1)
    ]

    with pytest.raises(HTTPException) as exc_info:
        await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert exc_info.value.status_code == 400
    assert f"一次最多上传 {svc.MAX_WORKSPACE_UPLOAD_FILES} 个文件" in exc_info.value.detail


def _make_thread_files(tmp_path: Path, thread_id: str) -> Path:
    """构造一个历史对话的 uploads/outputs 目录并写入示例文件。"""
    user_data = tmp_path / "threads" / thread_id / "user-data"
    uploads = user_data / "uploads"
    outputs = user_data / "outputs"
    uploads.mkdir(parents=True)
    outputs.mkdir(parents=True)
    (uploads / "note.md").write_text("# 历史上传", encoding="utf-8")
    (outputs / "result.txt").write_text("历史产物", encoding="utf-8")
    return user_data


@pytest.mark.asyncio
async def test_list_workspace_tree_exposes_virtual_chat_files_without_creating_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    thread_id = "thread-2026-08-09"
    _make_thread_files(tmp_path, thread_id)
    thread_titles = {thread_id: "2026-08-09-对话"}

    agents_result = await svc.list_workspace_tree(path="/agents", current_user=user, thread_titles=thread_titles)
    result = await svc.list_workspace_tree(path="/agents/chats", current_user=user, thread_titles=thread_titles)

    assert next(entry for entry in agents_result["entries"] if entry["name"] == "chats")["readonly"] is True
    thread_entry = next(entry for entry in result["entries"] if entry["name"] == thread_id)
    assert thread_entry["title"] == "2026-08-09-对话"
    assert thread_entry["is_dir"] is True
    assert thread_entry["readonly"] is True

    upload_result = await svc.list_workspace_tree(
        path=f"/agents/chats/{thread_id}/uploads", current_user=user, thread_titles=thread_titles
    )
    names = {entry["name"] for entry in upload_result["entries"]}
    assert "note.md" in names
    workspace_agents = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    assert not (workspace_agents / "chats").exists()
    assert not (workspace_agents / thread_id).exists()


@pytest.mark.asyncio
async def test_list_workspace_tree_without_conversation_scope_hides_virtual_chats(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    result = await svc.list_workspace_tree(path="/agents", current_user=_user())

    assert "chats" not in {entry["name"] for entry in result["entries"]}


@pytest.mark.asyncio
async def test_list_workspace_tree_rejects_existing_physical_chats_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    chats_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents" / "chats"
    chats_dir.mkdir(parents=True)

    with pytest.raises(HTTPException) as exc_info:
        await svc.list_workspace_tree(path="/agents", current_user=_user(), thread_titles={})

    assert exc_info.value.status_code == 409
    assert chats_dir.is_dir()


@pytest.mark.asyncio
async def test_list_workspace_tree_recursively_returns_chat_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    thread_id = "thread-recursive"
    _make_thread_files(tmp_path, thread_id)

    result = await svc.list_workspace_tree(
        path="/agents/chats",
        recursive=True,
        files_only=True,
        current_user=_user(),
        thread_titles={thread_id: "递归对话"},
    )

    assert {entry["path"] for entry in result["entries"]} == {
        f"/agents/chats/{thread_id}/uploads/note.md",
        f"/agents/chats/{thread_id}/outputs/result.txt",
    }

    agents_result = await svc.list_workspace_tree(
        path="/agents",
        recursive=True,
        files_only=True,
        current_user=_user(),
        thread_titles={thread_id: "递归对话"},
    )
    agent_paths = {entry["path"] for entry in agents_result["entries"]}
    assert {
        f"/agents/chats/{thread_id}/uploads/note.md",
        f"/agents/chats/{thread_id}/outputs/result.txt",
    }.issubset(agent_paths)
    assert "/agents/chats/" not in agent_paths


@pytest.mark.asyncio
async def test_build_owned_thread_titles_uses_all_active_conversations(monkeypatch) -> None:
    calls = []

    class FakeRepository:
        def __init__(self, db):
            calls.append(db)

        async def list_active_conversations_for_user(self, uid):
            calls.append(uid)
            return [
                SimpleNamespace(
                    thread_id="unpinned-thread",
                    title="普通对话",
                    created_at=dt.datetime(2026, 8, 9, 10, 0),
                ),
                SimpleNamespace(
                    thread_id="pinned-thread",
                    title="置顶对话",
                    created_at=dt.datetime(2026, 8, 10, 10, 0),
                ),
                SimpleNamespace(
                    thread_id="untitled-thread",
                    title="",
                    created_at=dt.datetime(2026, 8, 8, 10, 0),
                ),
                SimpleNamespace(
                    thread_id="invalid.thread",
                    title="非法对话",
                    created_at=dt.datetime(2026, 8, 7, 10, 0),
                ),
            ]

    monkeypatch.setattr(svc, "ConversationRepository", FakeRepository)

    result = await svc.build_owned_thread_titles(object(), "user-1")

    assert result == {
        "unpinned-thread": "2026-08-09-普通对话",
        "pinned-thread": "2026-08-10-置顶对话",
        "untitled-thread": "2026-08-08-未命名对话",
    }
    assert calls[1] == "user-1"


@pytest.mark.asyncio
async def test_list_workspace_tree_sorts_chat_directories_by_dated_title(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    for thread_id in ("thread-old", "thread-new-b", "thread-new-a"):
        _make_thread_files(tmp_path, thread_id)

    result = await svc.list_workspace_tree(
        path="/agents/chats",
        current_user=_user(),
        thread_titles={
            "thread-old": "2026-08-09-Z 对话",
            "thread-new-b": "2026-08-10-B 对话",
            "thread-new-a": "2026-08-10-A 对话",
        },
    )

    assert [entry["title"] for entry in result["entries"]] == [
        "2026-08-10-B 对话",
        "2026-08-10-A 对话",
        "2026-08-09-Z 对话",
    ]


@pytest.mark.asyncio
async def test_list_workspace_tree_hides_empty_chat_namespaces_and_threads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    uploads_only = "thread-uploads-only"
    outputs_only = "thread-outputs-only"
    empty_thread = "thread-empty"
    intermediate_only = "thread-intermediate-only"

    workspace_paths.ensure_thread_dirs(uploads_only, "user-1")
    workspace_paths.ensure_thread_dirs(outputs_only, "user-1")
    workspace_paths.ensure_thread_dirs(empty_thread, "user-1")
    workspace_paths.ensure_thread_dirs(intermediate_only, "user-1")
    (workspace_paths.sandbox_uploads_dir(uploads_only) / "upload.txt").write_text("upload", encoding="utf-8")
    (workspace_paths.sandbox_outputs_dir(outputs_only) / "output.txt").write_text("output", encoding="utf-8")
    intermediate_outputs = workspace_paths.sandbox_outputs_dir(intermediate_only)
    for dirname in ("large_tool_results", "large-tool-results", "large_tool_history", "conversation_history"):
        directory = intermediate_outputs / dirname
        directory.mkdir()
        (directory / "internal.txt").write_text("internal", encoding="utf-8")
    thread_titles = {
        uploads_only: "2026-08-10-仅上传",
        outputs_only: "2026-08-09-仅输出",
        empty_thread: "2026-08-08-空对话",
        intermediate_only: "2026-08-07-仅中间产物",
    }

    root_result = await svc.list_workspace_tree(
        path="/agents/chats",
        current_user=_user(),
        thread_titles=thread_titles,
    )
    assert [entry["name"] for entry in root_result["entries"]] == [uploads_only, outputs_only]

    uploads_result = await svc.list_workspace_tree(
        path=f"/agents/chats/{uploads_only}",
        current_user=_user(),
        thread_titles=thread_titles,
    )
    assert [entry["name"] for entry in uploads_result["entries"]] == ["uploads"]

    outputs_result = await svc.list_workspace_tree(
        path=f"/agents/chats/{outputs_only}",
        current_user=_user(),
        thread_titles=thread_titles,
    )
    assert [entry["name"] for entry in outputs_result["entries"]] == ["outputs"]


@pytest.mark.asyncio
async def test_list_workspace_tree_filters_intermediate_output_directories(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    thread_id = "thread-filtered-outputs"
    workspace_paths.ensure_thread_dirs(thread_id, "user-1")
    outputs = workspace_paths.sandbox_outputs_dir(thread_id)
    (outputs / "report.md").write_text("report", encoding="utf-8")
    for dirname in ("large_tool_results", "large-tool-results", "large_tool_history", "conversation_history"):
        directory = outputs / dirname
        directory.mkdir()
        (directory / "internal.txt").write_text("internal", encoding="utf-8")
    thread_titles = {thread_id: "2026-08-10-过滤中间产物"}

    result = await svc.list_workspace_tree(
        path=f"/agents/chats/{thread_id}/outputs",
        recursive=True,
        current_user=_user(),
        thread_titles=thread_titles,
    )

    assert [entry["path"] for entry in result["entries"]] == [f"/agents/chats/{thread_id}/outputs/report.md"]

    with pytest.raises(HTTPException) as exc_info:
        await svc.read_workspace_file_content(
            path=f"/agents/chats/{thread_id}/outputs/large_tool_results/internal.txt",
            current_user=_user(),
            thread_titles=thread_titles,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_workspace_tree_lists_outputs_with_relative_save_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAVE_DIR", "saves")
    thread_id = "thread-relative-save-dir"
    workspace_paths.ensure_thread_dirs(thread_id, "user-1")
    outputs = workspace_paths.sandbox_outputs_dir(thread_id)
    (outputs / "result.txt").write_text("result", encoding="utf-8")

    result = await svc.list_workspace_tree(
        path=f"/agents/chats/{thread_id}/outputs",
        current_user=_user(),
        thread_titles={thread_id: "2026-08-10-相对目录"},
    )

    assert [entry["name"] for entry in result["entries"]] == ["result.txt"]


@pytest.mark.asyncio
async def test_read_and_download_file_inside_thread_link(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    thread_id = "thread-read"
    _make_thread_files(tmp_path, thread_id)
    thread_titles = {thread_id: "历史对话"}
    result = await svc.read_workspace_file_content(
        path=f"/agents/chats/{thread_id}/uploads/note.md", current_user=user, thread_titles=thread_titles
    )

    assert result["content"] == "# 历史上传"


@pytest.mark.parametrize(
    "operation",
    [
        "write",
        "delete",
        "create_directory",
        "upload",
    ],
)
@pytest.mark.asyncio
async def test_write_operations_inside_thread_link_rejected(tmp_path: Path, monkeypatch, operation: str) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    thread_id = "thread-readonly"
    _make_thread_files(tmp_path, thread_id)
    if operation == "write":
        call = svc.write_workspace_file_content(
            path=f"/agents/chats/{thread_id}/uploads/note.md",
            content="x",
            current_user=user,
        )
    elif operation == "delete":
        call = svc.delete_workspace_path(path=f"/agents/chats/{thread_id}/uploads/note.md", current_user=user)
    elif operation == "create_directory":
        call = svc.create_workspace_directory(
            parent_path=f"/agents/chats/{thread_id}/uploads",
            name="new-dir",
            current_user=user,
        )
    else:
        call = svc.upload_workspace_files(
            parent_path=f"/agents/chats/{thread_id}/outputs",
            files=[UploadFile(filename="hack.txt", file=BytesIO(b"x"))],
            current_user=user,
        )

    with pytest.raises(HTTPException) as exc_info:
        await call

    assert exc_info.value.status_code == 403
    assert "只读" in exc_info.value.detail
    assert (tmp_path / "threads" / thread_id / "user-data" / "uploads" / "note.md").read_text(
        encoding="utf-8"
    ) == "# 历史上传"


@pytest.mark.asyncio
async def test_virtual_chat_not_owned_by_user_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    thread_id = "thread-other-user"
    _make_thread_files(tmp_path, thread_id)
    with pytest.raises(HTTPException) as exc_info:
        await svc.read_workspace_file_content(
            path=f"/agents/chats/{thread_id}/uploads/note.md", current_user=user, thread_titles={}
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"


@pytest.mark.asyncio
async def test_physical_symlink_under_virtual_chats_cannot_redirect_access(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    user = _user()
    thread_id = "thread-sneaky"
    thread_titles = {thread_id: "历史对话"}
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    chats_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents" / "chats"
    chats_dir.parent.mkdir(parents=True)
    chats_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as exc_info:
        await svc.read_workspace_file_content(
            path=f"/agents/chats/{thread_id}/uploads/secret.txt", current_user=user, thread_titles=thread_titles
        )

    assert exc_info.value.status_code == 409
