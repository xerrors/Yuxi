from __future__ import annotations

import asyncio
import hashlib
import os
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.files as svc
from yuxi.modules.workspace import filesystem
from yuxi.modules.workspace import paths as workspace_paths
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.services.files import _write_workspace_upload
from yuxi.shared.errors import DomainError, InvalidInputError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError
from yuxi.shared.files import FileInput


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="db-id-1", uid="user-1")


class _Referenced:
    """按注入路径列表响应删除保护查询的 ProjectRepository 替身。"""

    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    async def list_referenced_workdir_paths_for_user(self, uid: str) -> list[str]:
        return list(self._paths)


def _workspace_root(user: SimpleNamespace) -> Path:
    svc._workspace_backend(user)
    return workspace_paths.user_workspace_dir(str(user.uid))


def test_workspace_root_creates_default_agent_context_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    previous_umask = os.umask(0o077)
    try:
        root = _workspace_root(_user())
    finally:
        os.umask(previous_umask)

    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert (root / "agents" / "AGENTS.md").read_text(encoding="utf-8") == ("# AGENTS\n\n以下是约束 Agent 行为的一些要求\n")
    assert (root / "agents" / "USER.md").read_text(encoding="utf-8") == ("# USER\n\n以下是有关用户的一些信息\n")
    assert (root / "agents" / "MEMORY.md").read_text(encoding="utf-8") == ("# MEMORY\n\n以下是 Agent 需要记住的一些信息\n")
    assert {path.name for path in (root / "agents").iterdir()} == {"AGENTS.md", "USER.md", "MEMORY.md"}
    assert (root / "agents").stat().st_mode & 0o777 == 0o700
    for filename in ("AGENTS.md", "USER.md", "MEMORY.md"):
        assert (root / "agents" / filename).stat().st_mode & 0o777 == 0o600


def test_external_uid_uses_stable_path_safe_workspace_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    uid = "oidc:898f3d04-140e-433b-a06e-1e50a2bd01b6"

    workspace_paths.ensure_user_workspace(uid)

    dirname = "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest()
    workspace = workspace_paths.user_workspace_dir(uid)
    assert workspace == tmp_path / "threads" / "shared" / dirname / "workspace"
    assert (workspace / "agents" / "AGENTS.md").is_file()


@pytest.mark.parametrize("uid", ["../outside", r"C:\\outside", "oidc:tenant/user"])
def test_external_uid_cannot_escape_threads_root(tmp_path: Path, monkeypatch, uid: str) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "saves" / "threads"))

    workspace = workspace_paths.user_workspace_dir(uid)

    assert workspace.parent.name == "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest()
    assert workspace.resolve().is_relative_to((tmp_path / "saves" / "threads").resolve())


def test_workspace_root_keeps_existing_agents_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    agents_dir.mkdir(parents=True)
    agents_file = agents_dir / "AGENTS.md"
    agents_file.write_text("保留已有内容", encoding="utf-8")

    root = _workspace_root(_user())

    assert root == tmp_path / "threads" / "shared" / "user-1" / "workspace"
    assert agents_file.read_text(encoding="utf-8") == "保留已有内容"


def test_workspace_root_rejects_symlink_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user_root = tmp_path / "threads" / "shared" / "user-1"
    outside_root = tmp_path / "outside"
    user_root.mkdir(parents=True)
    outside_root.mkdir()
    (user_root / "workspace").symlink_to(outside_root, target_is_directory=True)

    with pytest.raises(DomainError) as exc_info:
        svc._workspace_backend(_user())

    assert isinstance(exc_info.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_unsupported_for_unreadable_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / "bad.txt"
    target.write_bytes(b"\xff\xfe\x00")

    result = await svc.read_workspace_file_content(path="/bad.txt", current_user=user)

    assert result.content is None
    assert result.preview_type == "unsupported"
    assert result.supported is False


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_office_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / "demo.docx"
    target.write_bytes(b"office")

    result = await svc.read_workspace_file_content(path="/demo.docx", current_user=user)

    assert result.preview_type == "office"
    assert result.filename == "demo.docx"
    assert result.content == b"office"


@pytest.mark.asyncio
async def test_read_workspace_file_content_returns_xlsx_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / "sheet.xlsx"
    target.write_bytes(b"PK\x03\x04excel")

    result = await svc.read_workspace_file_content(path="/sheet.xlsx", current_user=user)

    assert result.content == b"PK\x03\x04excel"
    assert result.preview_type == "office"
    assert result.supported is True
    assert result.filename == "sheet.xlsx"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["large.txt", "large.docx"])
async def test_workspace_preview_rejects_large_source_before_content_preparation(tmp_path, monkeypatch, filename):
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    target = _workspace_root(user) / filename
    target.write_bytes(b"123456789")
    monkeypatch.setattr(svc, "MAX_FILE_PREVIEW_BYTES", 8)

    def fail_preparation(*_args, **_kwargs):
        raise AssertionError("over-budget source must not reach content preparation")

    monkeypatch.setattr(svc, "prepare_file_preview", fail_preparation)

    result = await svc.read_workspace_file_content(path=f"/{filename}", current_user=user)

    assert result == svc.preview_too_large().payload()
    assert target.read_bytes() == b"123456789"


@pytest.mark.asyncio
async def test_download_workspace_file_keeps_office_original_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / "slides.pptx"
    target.write_bytes(b"presentation")

    response = await svc.download_workspace_file(path="/slides.pptx", current_user=user)
    body = Path(response.path).read_bytes()
    Path(response.path).unlink()

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
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / f"note.{extension}"
    target.write_text(original, encoding="utf-8")

    result = await svc.write_workspace_file_content(path=f"/note.{extension}", content=content, current_user=user)

    assert result["success"] is True
    assert result["path"] == f"/note.{extension}"
    assert result["entry"]["path"] == f"/note.{extension}"
    assert target.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_write_workspace_file_content_rejects_unsupported_suffix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    target = root / "script.py"
    target.write_text("print('hello')", encoding="utf-8")

    with pytest.raises(DomainError) as exc_info:
        await svc.write_workspace_file_content(path="/script.py", content="print('bye')", current_user=user)

    assert isinstance(exc_info.value, InvalidOperationError)
    assert target.read_text(encoding="utf-8") == "print('hello')"


@pytest.mark.asyncio
async def test_write_workspace_file_content_rejects_directory_and_missing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    workspace_paths.ensure_user_workspace("user-1")

    with pytest.raises(DomainError) as directory_error:
        await svc.write_workspace_file_content(path="/agents/", content="x", current_user=user)
    with pytest.raises(DomainError) as missing_error:
        await svc.write_workspace_file_content(path="/missing.md", content="x", current_user=user)

    assert isinstance(directory_error.value, InvalidOperationError)
    assert isinstance(missing_error.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_write_workspace_file_content_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    with pytest.raises(DomainError) as exc_info:
        await svc.write_workspace_file_content(
            path="/../outside.md",
            content="x",
            current_user=_user(),
        )

    assert isinstance(exc_info.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_upload_workspace_files_writes_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    uploads = [
        FileInput(filename="demo.txt", source=BytesIO(b"hello")),
        FileInput(filename="notes.md", source=BytesIO(b"# notes")),
    ]

    previous_umask = os.umask(0o077)
    try:
        result = await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)
    finally:
        os.umask(previous_umask)

    assert result["success"] is True
    assert [entry["path"] for entry in result["entries"]] == ["/demo.txt", "/notes.md"]
    assert result["entries"][0]["size"] == 5
    assert (root / "demo.txt").read_bytes() == b"hello"
    assert (root / "notes.md").read_bytes() == b"# notes"
    assert (root / "demo.txt").stat().st_mode & 0o777 == 0o600
    assert (root / "notes.md").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_create_workspace_directory_uses_owner_only_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    previous_umask = os.umask(0o077)
    try:
        result = await svc.create_workspace_directory(
            parent_path="/",
            name="project",
            current_user=_user(),
        )
    finally:
        os.umask(previous_umask)

    target = tmp_path / "threads/shared/user-1/workspace/project"
    assert result["entry"]["path"] == "/project/"
    assert target.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_oversized_file_and_cleans_partial_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    monkeypatch.setattr(svc, "MAX_WORKSPACE_UPLOAD_SIZE_BYTES", 5)
    user = _user()
    root = _workspace_root(user)
    uploads = [
        FileInput(filename="small.txt", source=BytesIO(b"12345")),
        FileInput(filename="large.txt", source=BytesIO(b"123456")),
    ]

    with pytest.raises(DomainError) as exc_info:
        await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert isinstance(exc_info.value, InvalidOperationError)
    assert "100 MB" in exc_info.value.message
    assert not (root / "small.txt").exists()
    assert not (root / "large.txt").exists()


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_more_than_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    uploads = [FileInput(filename=f"demo-{index}.txt", source=BytesIO(b"hello")) for index in range(svc.MAX_WORKSPACE_UPLOAD_FILES + 1)]

    with pytest.raises(DomainError) as exc_info:
        await svc.upload_workspace_files(parent_path="/", files=uploads, current_user=user)

    assert isinstance(exc_info.value, InvalidOperationError)
    assert f"一次最多上传 {svc.MAX_WORKSPACE_UPLOAD_FILES} 个文件" in exc_info.value.message


@pytest.mark.asyncio
async def test_upload_workspace_files_preserves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    """文件夹上传按相对路径创建目录并保留文件内容。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    uploads = [
        FileInput(filename="a.txt", source=BytesIO(b"a")),
        FileInput(filename="b.txt", source=BytesIO(b"b")),
        FileInput(filename="c.txt", source=BytesIO(b"c")),
    ]

    result = await svc.upload_workspace_files(
        parent_path="/",
        files=uploads,
        relative_paths=["root/a.txt", "root/sub/b.txt", "root/sub/c.txt"],
        current_user=user,
    )

    assert [entry["path"] for entry in result["entries"]] == ["/root/a.txt", "/root/sub/b.txt", "/root/sub/c.txt"]
    assert (root / "root/a.txt").read_bytes() == b"a"
    assert (root / "root/sub/b.txt").read_bytes() == b"b"
    assert (root / "root/sub/c.txt").read_bytes() == b"c"


@pytest.mark.asyncio
async def test_folder_upload_preserves_leading_space_in_root_name(tmp_path: Path, monkeypatch) -> None:
    """目录名前导空白属于名称，不能静默上传到另一个目录。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    result = await svc.upload_workspace_files(
        parent_path="/",
        files=[FileInput(filename="a.txt", source=BytesIO(b"a"))],
        relative_paths=[" root/sub/a.txt"],
        current_user=user,
    )

    assert result["entries"][0]["path"] == "/ root/sub/a.txt"
    assert (root / " root/sub/a.txt").read_bytes() == b"a"
    assert not (root / "root").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative_path", ["root/a.txt ", "root /a.txt", "root/sub\t/a.txt"])
async def test_folder_upload_rejects_trailing_whitespace_before_writing(tmp_path: Path, monkeypatch, relative_path: str) -> None:
    """通用 scope 不能可靠寻址尾随空白名称，上传应显式拒绝而非改名。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(InvalidInputError, match="路径组件不能以空白字符结尾"):
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="a.txt", source=BytesIO(b"a"))],
            relative_paths=[relative_path],
            current_user=user,
        )

    assert not (root / "root").exists()
    assert not (root / "root ").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.txt",
        "/absolute.txt",
        r"root\evil.txt",
        "root//empty-segment.txt",
        "root/./dot.txt",
        "root/../parent.txt",
        "root/",
        "",
        "C:/drive.txt",
        "root/\x00nul.txt",
    ],
)
async def test_upload_workspace_files_rejects_invalid_relative_paths(tmp_path: Path, monkeypatch, relative_path: str) -> None:
    """越界、绝对路径和非法路径组件在写入前被拒绝。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(DomainError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="x.txt", source=BytesIO(b"x"))],
            relative_paths=[relative_path],
            current_user=user,
        )

    assert isinstance(exc_info.value, InvalidInputError)
    assert not (root / "x.txt").exists()
    assert not (root.parent / "outside.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative_path",
    [
        "/".join(["a"] * (svc.MAX_WORKSPACE_UPLOAD_PATH_DEPTH + 1)) + "/f.txt",
        "a" * (svc.MAX_WORKSPACE_UPLOAD_NAME_BYTES + 1) + "/f.txt",
        "/".join(["b" * 64] * 63) + "/f.txt",
    ],
)
async def test_upload_workspace_files_rejects_overlong_or_too_deep_paths(tmp_path: Path, monkeypatch, relative_path: str) -> None:
    """深度和路径长度在写入前受限，避免创建无法回收的目录树。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(InvalidInputError):
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="f.txt", source=BytesIO(b"x"))],
            relative_paths=[relative_path],
            current_user=user,
        )

    assert not (root / "a").exists()
    assert not (root / ("b" * 64)).exists()


@pytest.mark.asyncio
async def test_upload_workspace_files_enforces_full_path_depth_boundary(tmp_path: Path, monkeypatch) -> None:
    """parent_path 的累积深度也计入上限，边界内可写且可删除。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    parent = "/".join(["p"] * 63)
    (root / parent).mkdir(parents=True)

    accepted = await svc.upload_workspace_files(
        parent_path=f"/{parent}",
        files=[FileInput(filename="ok.txt", source=BytesIO(b"ok"))],
        relative_paths=["ok.txt"],
        current_user=user,
    )
    assert accepted["entries"][0]["path"] == f"/{parent}/ok.txt"
    assert (root / parent / "ok.txt").read_bytes() == b"ok"

    with pytest.raises(InvalidInputError) as relative_error:
        await svc.upload_workspace_files(
            parent_path=f"/{parent}",
            files=[FileInput(filename="bad.txt", source=BytesIO(b"bad"))],
            relative_paths=["sub/bad.txt"],
            current_user=user,
        )
    assert "层级过深" in relative_error.value.message
    assert not (root / parent / "sub/bad.txt").exists()

    deeper = f"{parent}/p"
    (root / deeper).mkdir()
    with pytest.raises(InvalidInputError) as parent_error:
        await svc.upload_workspace_files(
            parent_path=f"/{deeper}",
            files=[FileInput(filename="x.txt", source=BytesIO(b"x"))],
            relative_paths=["x.txt"],
            current_user=user,
        )
    assert "目标目录层级过深" in parent_error.value.message
    assert not (root / deeper / "x.txt").exists()

    monkeypatch.setattr(svc, "ProjectRepository", lambda _db: _Referenced([]))
    await svc.delete_workspace_path(path="/p", current_user=user, db=SimpleNamespace())
    assert not (root / "p").exists()


@pytest.mark.asyncio
async def test_upload_workspace_files_accepts_component_and_path_boundaries(tmp_path: Path, monkeypatch) -> None:
    """255 字节组件和多 KB 路径仍可写入，避免上限意外收紧。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    long_name = "a" * svc.MAX_WORKSPACE_UPLOAD_NAME_BYTES
    long_path = "/".join(["c" * svc.MAX_WORKSPACE_UPLOAD_NAME_BYTES] * 8) + "/f.txt"

    await svc.upload_workspace_files(
        parent_path="/",
        files=[FileInput(filename="f.txt", source=BytesIO(b"x"))],
        relative_paths=[f"{long_name}/f.txt"],
        current_user=user,
    )
    await svc.upload_workspace_files(
        parent_path="/",
        files=[FileInput(filename="f.txt", source=BytesIO(b"x"))],
        relative_paths=[long_path],
        current_user=user,
    )

    assert (root / long_name / "f.txt").read_bytes() == b"x"
    assert (root / long_path).read_bytes() == b"x"


def test_normalize_upload_relative_path_rejects_overlong_component() -> None:
    """单段长度 guard 独立于预检生效。"""
    with pytest.raises(InvalidInputError) as exc_info:
        svc._normalize_upload_relative_path("a" * (svc.MAX_WORKSPACE_UPLOAD_NAME_BYTES + 1) + "/f.txt")

    assert "相对路径组件过长" in exc_info.value.message


def test_normalize_upload_relative_path_locks_exact_byte_limit() -> None:
    """相对路径总字节上限的接受/拒绝边界固定为 4096/4097。"""
    accepted = "/".join(["a" * 64] * 62 + ["b" * 66])
    assert len(accepted.encode("utf-8")) == svc.MAX_WORKSPACE_UPLOAD_PATH_BYTES
    assert svc._normalize_upload_relative_path(accepted) == accepted

    with pytest.raises(InvalidInputError) as exc_info:
        svc._normalize_upload_relative_path(accepted + "b")

    assert "相对路径过长" in exc_info.value.message


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_overlong_flat_filename(tmp_path: Path, monkeypatch) -> None:
    """普通上传的超长文件名在预检阶段映射为业务错误而不是 500。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(InvalidInputError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="a" * 300 + ".txt", source=BytesIO(b"x"))],
            current_user=user,
        )

    assert "文件路径过长" in exc_info.value.message
    assert not any(path.name.startswith("a" * 300) for path in root.iterdir())


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_duplicate_targets(tmp_path: Path, monkeypatch) -> None:
    """不同上传文件不能落到同一个目标路径。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(InvalidOperationError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[
                FileInput(filename="a.txt", source=BytesIO(b"first")),
                FileInput(filename="a.txt", source=BytesIO(b"second")),
            ],
            relative_paths=["root/a.txt", "root/a.txt"],
            current_user=user,
        )

    assert "上传目标重复" in exc_info.value.message
    assert not (root / "root/a.txt").exists()


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_relative_path_count_mismatch(tmp_path: Path, monkeypatch) -> None:
    """相对路径必须与文件列表逐项对应。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    with pytest.raises(InvalidInputError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="a.txt", source=BytesIO(b"a")), FileInput(filename="b.txt", source=BytesIO(b"b"))],
            relative_paths=["root/a.txt"],
            current_user=_user(),
        )

    assert "相对路径数量" in exc_info.value.message


@pytest.mark.asyncio
async def test_upload_workspace_files_merges_existing_directory_and_rejects_existing_file(tmp_path: Path, monkeypatch) -> None:
    """已有目录可复用，但任一目标已存在时写入前整批拒绝。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    (root / "root/sub").mkdir(parents=True)
    (root / "root/a.txt").write_bytes(b"original")

    merged = await svc.upload_workspace_files(
        parent_path="/",
        files=[FileInput(filename="a.txt", source=BytesIO(b"merged"))],
        relative_paths=["root/sub/a.txt"],
        current_user=user,
    )
    assert merged["entries"][0]["path"] == "/root/sub/a.txt"
    assert (root / "root/sub/a.txt").read_bytes() == b"merged"

    with pytest.raises(InvalidOperationError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[
                FileInput(filename="new.txt", source=BytesIO(b"new")),
                FileInput(filename="a.txt", source=BytesIO(b"replacement")),
            ],
            relative_paths=["root/new.txt", "root/a.txt"],
            current_user=user,
        )

    assert "已存在" in exc_info.value.message
    assert not (root / "root/new.txt").exists()
    assert (root / "root/a.txt").read_bytes() == b"original"


@pytest.mark.asyncio
async def test_upload_workspace_files_rejects_file_directory_conflict(tmp_path: Path, monkeypatch) -> None:
    """需要目录的位置已有普通文件时返回业务错误而不是 500。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)
    (root / "root").mkdir()
    (root / "root/sub").write_bytes(b"file")

    with pytest.raises(InvalidOperationError) as exc_info:
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="child.txt", source=BytesIO(b"child"))],
            relative_paths=["root/sub/child.txt"],
            current_user=user,
        )

    assert "目标路径不可用" in exc_info.value.message
    assert (root / "root/sub").is_file()
    assert not (root / "root/sub/child.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("paths", [["root/node", "root/node/child.txt"], ["root/node/child.txt", "root/node"]])
async def test_folder_upload_rejects_ancestor_targets_before_writing(tmp_path: Path, monkeypatch, paths) -> None:
    """同批文件不能同时成为另一个文件的父目录，顺序不影响拒绝结果。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    user = _user()
    root = _workspace_root(user)

    with pytest.raises(InvalidOperationError, match="文件与文件夹路径冲突"):
        await svc.upload_workspace_files(
            parent_path="/",
            files=[FileInput(filename="file.txt", source=BytesIO(b"x")) for _path in paths],
            relative_paths=paths,
            current_user=user,
        )

    assert not (root / "root").exists()


@pytest.mark.asyncio
async def test_search_workspace_files_matches_filenames(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    root = _workspace_root(_user())
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "meeting-record.md").write_text("记录", encoding="utf-8")
    (root / "agents" / "MEMORY.md").write_text("记忆", encoding="utf-8")  # 已有默认文件

    response = await svc.search_workspace_files(query="memory", current_user=_user())

    names = [entry["name"] for entry in response["entries"]]
    assert names == ["MEMORY.md"]
    assert response["entries"][0]["path"] == "/agents/MEMORY.md"
    assert response["entries"][0]["virtual_path"] == "/home/user/workspace/agents/MEMORY.md"

    empty = await svc.search_workspace_files(query="   ", current_user=_user())
    assert empty["entries"] == []


def test_workspace_entry_preserves_current_virtual_path_contract() -> None:
    metadata = {"is_dir": True, "size": 0, "modified_at": 0}

    root = svc._entry_from_metadata("/", metadata)
    directory = svc._entry_from_metadata("/notes", metadata)

    assert root["virtual_path"] == "/home/user/workspace"
    assert directory["path"] == "/notes/"
    assert directory["virtual_path"] == "/home/user/workspace/notes/"


@pytest.mark.asyncio
async def test_cancelled_upload_waits_for_borrowed_stream_before_returning(tmp_path, monkeypatch):
    """请求取消不能让输入流在写入线程仍消费时被关闭。"""
    monkeypatch.setattr(filesystem, "user_workspace_dir", lambda _uid: tmp_path)
    started = Event()
    release = Event()

    class BlockingSource(BytesIO):
        """在首次读取时暂停线程，模拟慢磁盘。"""

        def read(self, size=-1):
            """让测试在 I/O 中途取消服务调用。"""
            started.set()
            assert release.wait(5)
            assert not self.closed
            return super().read(size)

    source = BlockingSource(b"content")
    task = asyncio.create_task(_write_workspace_upload(FileInput("file.txt", source), Workspace("user-1"), "/file.txt"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        source.close()
        assert (tmp_path / "file.txt").read_bytes() == b"content"
        assert not list(tmp_path.glob(".yuxi-write-*"))
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        source.close()
