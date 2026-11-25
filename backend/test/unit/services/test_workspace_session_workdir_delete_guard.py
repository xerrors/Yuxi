"""整目录删除保护：仍被会话引用的 Workdir 及其祖先拒绝递归删除。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.files as svc
from yuxi.shared.errors import InvalidOperationError


class _Referenced:
    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    async def list_referenced_workdir_paths_for_user(self, uid: str) -> list[str]:
        return list(self._paths)


def _patch_repo(monkeypatch, paths: list[str], *, deletable: bool = False) -> None:
    monkeypatch.setattr(svc, "ProjectRepository", lambda _db: _Referenced(paths))
    if deletable:
        monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace(delete_authorized_path=lambda *_a, **_k: None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "referenced"),
    [
        ("/projects/20261124/sess-a", ["projects/20261124/sess-a"]),
        ("/projects/20261124", ["projects/20261124/sess-a"]),
        ("/projects", ["projects/20261124/sess-a"]),
    ],
)
async def test_delete_rejects_referenced_workdir_and_ancestors(monkeypatch, target: str, referenced: list[str]) -> None:
    """目标即 Workdir 或任一祖先目录时整次拒绝，文件不落入删除调用。"""
    _patch_repo(monkeypatch, referenced)
    user = SimpleNamespace(id="db-id-1", uid="user-1")
    monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace(delete_authorized_path=lambda *_a, **_k: None))
    with pytest.raises(InvalidOperationError) as error:
        await svc.delete_workspace_path(path=target, current_user=user, db=SimpleNamespace())
    assert error.value.code == "workspace_session_workdir_protected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "referenced"),
    [
        ("/notes", ["projects/20261124/sess-a"]),
        ("/projects/20261124/other", ["projects/20261124/sess-a"]),
        ("/projects/20261124/sess-a/sub", ["projects/20261124/sess-a"]),
        ("/notes/report.txt", []),
        ("/projects/20261125", []),
    ],
)
async def test_delete_allows_unrelated_targets(monkeypatch, target: str, referenced: list[str]) -> None:
    """无关路径、Workdir 内子目录与无引用残留目录放行。"""
    _patch_repo(monkeypatch, referenced, deletable=True)
    user = SimpleNamespace(id="db-id-1", uid="user-1")
    result = await svc.delete_workspace_path(path=target, current_user=user, db=SimpleNamespace())
    assert result["success"] is True


@pytest.mark.asyncio
async def test_delete_file_inside_workdir_is_not_blocked(monkeypatch) -> None:
    """删除 Workdir 内单个文件不属于整目录删除，路径不会命中祖先判定。"""
    _patch_repo(monkeypatch, ["projects/20261124/sess-a"], deletable=True)
    user = SimpleNamespace(id="db-id-1", uid="user-1")
    result = await svc.delete_workspace_path(path="/projects/20261124/sess-a/out.txt", current_user=user, db=SimpleNamespace())
    assert result["success"] is True


@pytest.mark.asyncio
async def test_personal_workspace_refuses_conversation_archive_but_deletes_tool_media(monkeypatch) -> None:
    """压缩留档目录及其内容拒绝删除；同级 tool_media 仍可删除。"""
    calls = []

    def delete_authorized_path(path, *, root):
        del root
        calls.append(path)

    _patch_repo(monkeypatch, ["projects/20261124/sess-a"])
    monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace(delete_authorized_path=delete_authorized_path))
    user = SimpleNamespace(id="db-id-1", uid="user-1")
    with pytest.raises(InvalidOperationError) as error:
        await svc.delete_workspace_path(
            path="/projects/20261124/sess-a/outputs/conversation_history/summary.md",
            current_user=user,
            db=SimpleNamespace(),
        )
    assert error.value.code == "conversation_archive_protected"
    assert calls == []
    result = await svc.delete_workspace_path(
        path="/projects/20261124/sess-a/outputs/tool_media/shot.png",
        current_user=user,
        db=SimpleNamespace(),
    )
    assert result["success"] is True
    assert calls == ["/projects/20261124/sess-a/outputs/tool_media/shot.png"]
