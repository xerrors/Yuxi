"""projects 展示与写入口径：只精确隐藏会话 implicit Project，用户目录与文件可见（issue #1184）。"""

from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.files as svc
from yuxi.shared.errors import InvalidOperationError

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _ProjectRepository:
    def __init__(self, _db):
        pass

    async def list_selectable_workdir_paths_for_user(self, uid: str) -> list[str]:
        assert uid == "user-1"
        return ["projects/client", "projects/group/nested", "outside"]

    async def list_implicit_workdir_paths_for_user(self, uid: str) -> list[str]:
        assert uid == "user-1"
        return ["projects/anonymous", "projects/20261231/dead00"]


async def test_project_tree_hides_only_implicit_subtrees_and_keeps_user_entries(monkeypatch):
    """implicit 根、其内文件与纯 implicit 日期层隐藏；用户自建目录与既有散文件可见。"""
    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    entries = [
        {"path": "/projects/", "is_dir": True},
        {"path": "/projects/anonymous/", "is_dir": True},
        {"path": "/projects/anonymous/report.txt", "is_dir": False},
        {"path": "/projects/client/", "is_dir": True},
        {"path": "/projects/client/report.txt", "is_dir": False},
        {"path": "/projects/group/", "is_dir": True},
        {"path": "/projects/group/nested/", "is_dir": True},
        {"path": "/projects/user-dir/", "is_dir": True},
        {"path": "/projects/orphan.txt", "is_dir": False},
        {"path": "/projects/20261231/", "is_dir": True},
        {"path": "/notes/", "is_dir": True},
    ]

    filtered = await svc._filter_project_tree_entries(entries, uid="user-1", db=SimpleNamespace())

    assert [entry["path"] for entry in filtered] == [
        "/projects/",
        "/projects/client/",
        "/projects/client/report.txt",
        "/projects/group/",
        "/projects/group/nested/",
        "/projects/user-dir/",
        "/projects/orphan.txt",
        "/notes/",
    ]


async def test_date_layer_containing_selected_project_stays_visible(monkeypatch):
    """同层同时存在 selectable 与 implicit 时，日期层作为 selectable 祖先保持可见。"""

    class _MixedRepository(_ProjectRepository):
        async def list_selectable_workdir_paths_for_user(self, uid: str) -> list[str]:
            return ["projects/20261231/live01"]

    monkeypatch.setattr(svc, "ProjectRepository", _MixedRepository)
    entries = [{"path": "/projects/20261231/", "is_dir": True}]

    filtered = await svc._filter_project_tree_entries(entries, uid="user-1", db=SimpleNamespace())

    assert [entry["path"] for entry in filtered] == ["/projects/20261231/"]


async def test_project_tree_filter_skips_database_without_projects_entries(monkeypatch):
    """列表不含 projects 条目时展示投影零触库。"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("不应查询 Project 路径")

    monkeypatch.setattr(svc, "_selectable_project_root_paths", _boom)
    monkeypatch.setattr(svc, "_implicit_project_root_paths", _boom)
    entries = [{"path": "/notes/", "is_dir": True}]

    assert await svc._filter_project_tree_entries(entries, uid="user-1", db=SimpleNamespace()) == entries


async def test_selected_projects_root_exposes_its_complete_subtree(monkeypatch):
    class _RootProjectRepository:
        def __init__(self, _db):
            pass

        async def list_selectable_workdir_paths_for_user(self, _uid: str) -> list[str]:
            return ["projects"]

        async def list_implicit_workdir_paths_for_user(self, _uid: str) -> list[str]:
            return []

    monkeypatch.setattr(svc, "ProjectRepository", _RootProjectRepository)
    entries = [
        {"path": "/projects/anonymous/", "is_dir": True},
        {"path": "/projects/anonymous/report.txt", "is_dir": False},
    ]

    assert (
        await svc._filter_project_tree_entries(
            entries,
            uid="user-1",
            db=SimpleNamespace(),
        )
        == entries
    )


async def test_project_picker_tree_keeps_unbound_project_directories(monkeypatch):
    """Project 选目录用途绕过展示投影，但仍复用 Workspace 文件边界。"""

    entries = [{"path": "/projects/unbound/", "name": "unbound", "is_dir": True}]
    monkeypatch.setattr(svc, "_workspace_backend", lambda _user: SimpleNamespace())
    monkeypatch.setattr(svc, "_list_workspace_directory", lambda *_args, **_kwargs: entries)

    result = await svc.list_workspace_tree(
        path="/projects",
        include_unbound_project_dirs=True,
        current_user=SimpleNamespace(uid="user-1"),
        db=SimpleNamespace(),
    )

    assert result == {"entries": entries}


async def test_projects_write_targets_reject_only_implicit_subtrees(monkeypatch):
    """写守卫与展示口径一一对应：仅 implicit 子树写入被 fail-closed 拒绝。"""

    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)

    # 隐藏日期层之下的任何路径（含用户目录）在 UI 中不可下探，写入同样被拒
    hidden_targets = (
        "projects/anonymous/report.txt",
        "projects/20261231/dead00/uploads/a.md",
        "projects/20261231/x.txt",
        "projects/20261231/live01/a.txt",
    )
    for hidden_target in hidden_targets:
        with pytest.raises(InvalidOperationError) as exc_info:
            await svc.assert_projects_write_targets_visible([hidden_target], uid="user-1", db=SimpleNamespace())
        assert exc_info.value.code == "workspace_hidden_target"

    await svc.assert_projects_write_targets_visible(
        [
            "projects/orphan.txt",
            "projects/user-dir/a.txt",
            "projects",
            "projects/client/x.md",
            "projects/group/orphan.txt",
            "notes/b.txt",
            "/",
        ],
        uid="user-1",
        db=SimpleNamespace(),
    )


async def test_write_guard_allows_files_bound_inside_implicit_subtree(monkeypatch):
    """linked 绑定进 implicit 子树后展示优先级使目录可见，写守卫必须同样放行（口径一一对应）。"""

    class _BoundInsideImplicit(_ProjectRepository):
        async def list_selectable_workdir_paths_for_user(self, uid: str) -> list[str]:
            return ["projects/20261231/dead00"]

    monkeypatch.setattr(svc, "ProjectRepository", _BoundInsideImplicit)
    await svc.assert_projects_write_targets_visible(["projects/20261231/dead00/uploads/a.md"], uid="user-1", db=SimpleNamespace())


async def test_projects_write_targets_skip_database_when_no_projects_targets(monkeypatch):
    """projects 树外的批量上传不触库：谓词必须在查询前短路。"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("不应查询 implicit Project")

    monkeypatch.setattr(svc, "_implicit_project_root_paths", _boom)
    await svc.assert_projects_write_targets_visible(["docs/a.txt", "docs/sub/b.md"], uid="user-1", db=SimpleNamespace())
