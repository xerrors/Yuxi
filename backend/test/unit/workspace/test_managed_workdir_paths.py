from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from yuxi.modules.workspace import paths


@pytest.mark.parametrize(
    "value",
    [
        "agents/tasks/2026-09-02/new-chat-a1b2c3",
        "agents/tasks/2026-09-02/review-api-design-v2-a1b2c3-2",
        "agents/tasks/2026-09-02/abcdefghijklmnopqrstuvwxyzabcdefghijklmn-a1b2c3-100000",
    ],
)
def test_normalize_managed_workdir_path_accepts_task_names(value: str):
    assert paths.normalize_managed_workdir_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "agents/tasks/2026-02-30/new-chat-a1b2c3",
        "agents/tasks/20260902/new-chat-a1b2c3",
        "agents/tasks/2026-09-02/new-chat-A1B2C3",
        "agents/tasks/2026-09-02/new-chat-a1b2c3-0",
        "agents/tasks/2026-09-02/new-chat-a1b2c3-01",
        "agents/tasks/2026-09-02/review-api-design-v2-now-a1b2c3",
        "agents/tasks/2026-09-02/abcdefghijklmnopqrstuvwxyzabcdefghijklmno-a1b2c3",
        "agents/tasks/2026-09-02/../new-chat-a1b2c3",
        "tasks/20260902/a1b2c3",
        "projects/20260902/a1b2c3",
        "projects/2026-09-02_14-35-08_a1b2c3d4",
    ],
)
def test_normalize_managed_workdir_path_rejects_noncanonical_names(value: str):
    with pytest.raises(ValueError, match="supported managed directory layout"):
        paths.normalize_managed_workdir_path(value)


@pytest.mark.parametrize(
    ("naming_text", "expected_name"),
    [
        (None, "new-chat-a1b2c3"),
        ("整理本周会议纪要", "new-chat-a1b2c3"),
        ("Review API 接口", "new-chat-a1b2c3"),
        ("Review API design notes 𠮷", "new-chat-a1b2c3"),
        ("Review 〇", "new-chat-a1b2c3"),
        ("Draft quarterly report", "draft-quarterly-report-a1b2c3"),
        ("/Review_API/v2\\Design Plan", "review-api-v2-design-a1b2c3"),
        ("…🤔 K Ａ ７", "new-chat-a1b2c3"),
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop", "abcdefghijklmnopqrstuvwxyzabcdefghijklmn-a1b2c3"),
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm report", "abcdefghijklmnopqrstuvwxyzabcdefghijklm-a1b2c3"),
    ],
)
def test_allocate_default_user_workdir_path_uses_shanghai_date_and_message_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    naming_text: str | None,
    expected_name: str,
):
    """上海零点分配次日目录，文件名前缀只来自规定的首条文本。"""
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)

    result = paths.allocate_default_user_workdir_path(
        "user-1",
        "A1B2C3D4-E5F6-4789-8123-456789ABCDEF",
        naming_text=naming_text,
        allocated_at=datetime.fromisoformat("2026-09-01T16:00:00+00:00"),
    )

    assert result == f"agents/tasks/2026-09-02/{expected_name}"


def test_allocate_default_user_workdir_path_appends_first_available_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """逐个跳过已存在的目录、断开的符号链接和普通文件。"""
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    paths.ensure_user_workspace("user-1")
    task_date = paths.user_workspace_dir("user-1") / "agents" / "tasks" / "2026-09-02"
    task_date.mkdir(parents=True)
    stem = "review-api-a1b2c3"
    (task_date / stem).mkdir()
    (task_date / f"{stem}-1").symlink_to("missing")
    (task_date / f"{stem}-2").write_text("occupied", encoding="utf-8")

    result = paths.allocate_default_user_workdir_path(
        "user-1",
        UUID("a1b2c3d4-e5f6-4789-8123-456789abcdef"),
        naming_text="Review API",
        allocated_at=datetime.fromisoformat("2026-09-02T14:35:08+08:00"),
    )

    assert result == "agents/tasks/2026-09-02/review-api-a1b2c3-3"
    assert (task_date / f"{stem}-1").readlink() == Path("missing")
    assert (task_date / f"{stem}-2").read_text(encoding="utf-8") == "occupied"


def test_allocate_default_user_workdir_path_checks_unmaterialized_bindings(tmp_path: Path, monkeypatch):
    """已绑定但尚未落盘的目录同样不能复用。"""
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    result = paths.allocate_default_user_workdir_path(
        "user-1",
        "a1b2c3d4-e5f6-4789-8123-456789abcdef",
        allocated_at=datetime.fromisoformat("2026-09-01T16:35:08+00:00"),
        reserved_paths={"agents/tasks/2026-09-02/new-chat-a1b2c3"},
    )
    assert result == "agents/tasks/2026-09-02/new-chat-a1b2c3-1"
    assert not paths.user_workspace_dir("user-1").exists()


@pytest.mark.parametrize("operation", ["allocate", "materialize"])
@pytest.mark.parametrize("parent_path", ["agents", "agents/tasks", "agents/tasks/2026-09-02"])
@pytest.mark.parametrize("entry_kind", ["symlink", "file"])
def test_managed_workdir_rejects_blocked_parent_directory(tmp_path: Path, monkeypatch, operation: str, parent_path: str, entry_kind: str):
    """父路径遇到符号链接或文件时显式失败，保留原有条目。"""
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    blocked = paths.user_workspace_dir("user-1") / parent_path
    blocked.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if entry_kind == "symlink":
        blocked.symlink_to(outside, target_is_directory=True)
    else:
        blocked.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="UserWorkspace 路径包含符号链接或非目录组件"):
        if operation == "allocate":
            paths.allocate_default_user_workdir_path(
                "user-1",
                "a1b2c3d4-e5f6-4789-8123-456789abcdef",
                allocated_at=datetime.fromisoformat("2026-09-02T14:35:08+08:00"),
            )
        else:
            paths.create_managed_workdir("user-1", "agents/tasks/2026-09-02/new-chat-a1b2c3")

    if entry_kind == "symlink":
        assert blocked.readlink() == outside
        assert list(outside.iterdir()) == []
    else:
        assert blocked.read_text(encoding="utf-8") == "keep"


def test_managed_workdir_creation_is_exclusive_and_preserves_bytes(tmp_path: Path, monkeypatch):
    """后来出现的目录不能被创建请求接管。"""
    relative_path = "agents/tasks/2026-09-02/new-chat-a1b2c3"
    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    paths.create_managed_workdir("user-1", relative_path)
    target = paths.user_workdir_host_dir("user-1", relative_path)
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        paths.create_managed_workdir("user-1", relative_path)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
