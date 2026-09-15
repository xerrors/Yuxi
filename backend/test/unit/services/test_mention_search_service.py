from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import yuxi.services.mention_search_service as mention_service


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    root = tmp_path / "shared" / "user-1" / "workspace"
    root.mkdir(parents=True)
    return root


@pytest.mark.asyncio
async def test_workspace_mention_search_reads_current_files_without_cache(workspace: Path) -> None:
    first = workspace / "main.py"
    first.write_text("main", encoding="utf-8")

    initial = await mention_service._search_workspace("user-1", "main")
    assert [item["name"] for item in initial] == ["main.py"]

    first.unlink()
    second = workspace / "main-v2.py"
    second.write_text("main", encoding="utf-8")
    refreshed = await mention_service._search_workspace("user-1", "main")

    assert [item["name"] for item in refreshed] == ["main-v2.py"]


@pytest.mark.asyncio
async def test_workspace_mention_search_ranks_directories_and_ignores_hidden_trees(workspace: Path) -> None:
    test_dir = workspace / "test"
    test_dir.mkdir()
    (test_dir / "test_auth.py").write_text("auth", encoding="utf-8")
    (test_dir / "conftest.py").write_text("conf", encoding="utf-8")
    hidden = workspace / ".git"
    hidden.mkdir()
    (hidden / "test-secret").write_text("secret", encoding="utf-8")

    results = await mention_service._search_workspace("user-1", "test")

    assert [item["name"] for item in results] == ["test", "test_auth.py", "conftest.py"]
    assert results[0] == {
        "name": "test",
        "path": "/home/gem/user-data/test/",
        "is_dir": True,
        "source": "workspace",
    }


@pytest.mark.asyncio
async def test_empty_query_does_not_create_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mention_service,
        "Workspace",
        lambda _uid: pytest.fail("空查询不得扫描 Workspace"),
    )

    assert (
        await mention_service.search_mentions(
            thread_id=None,
            query="",
            sources=None,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
        == []
    )


@pytest.mark.asyncio
async def test_search_mentions_uses_workdir_access_and_workspace_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repository:
        def __init__(self, db):
            assert db == "db"

        async def get_conversation_by_thread_id(self, thread_id):
            assert thread_id == "thread-1"
            return SimpleNamespace(uid="user-1", status="active")

    class Workdir:
        relative_path = "projects/11111111-1111-4111-8111-111111111111"

        def search(self, query, **_kwargs):
            assert query == "out"
            return [
                {
                    "name": "outputs",
                    "path": "/outputs",
                    "is_dir": True,
                    "size": 0,
                    "modified_at": 1,
                }
            ]

    async def resolve(**kwargs):
        assert kwargs == {"thread_id": "thread-1", "uid": "user-1", "db": "db"}
        return SimpleNamespace(workdir=Workdir())

    async def workspace_search(uid, query):
        assert (uid, query) == ("user-1", "out")
        return []

    monkeypatch.setattr(mention_service, "ConversationRepository", Repository)
    monkeypatch.setattr(mention_service, "resolve_authorized_workdir", resolve)
    monkeypatch.setattr(mention_service, "_search_workspace", workspace_search)

    result = await mention_service.search_mentions(
        thread_id="thread-1",
        query="out",
        sources=None,
        current_user=SimpleNamespace(uid="user-1"),
        db="db",
    )

    assert result == [
        {
            "name": "outputs",
            "path": ("/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/outputs/"),
            "is_dir": True,
            "source": "thread",
        }
    ]


@pytest.fixture(autouse=True)
def isolate_personal_file_transaction_boundary(monkeypatch):
    """纯文件扫描fixture不实现PG事务；真实锁及pending在PG HTTP验收。"""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mention_service, "lock_user_files", AsyncMock())
    monkeypatch.setattr(mention_service, "require_no_pending_file_operations", AsyncMock())


@pytest.mark.asyncio
async def test_pending_delete_refuses_mention_candidates(monkeypatch):
    from fastapi import HTTPException
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        mention_service, "require_no_pending_file_operations", AsyncMock(side_effect=HTTPException(409, "pending"))
    )
    scan = AsyncMock()
    monkeypatch.setattr(mention_service, "_search_workspace", scan)
    with pytest.raises(HTTPException) as error:
        await mention_service.search_mentions(
            thread_id=None, query="file", sources="workspace", current_user=SimpleNamespace(uid="u"), db=object()
        )
    assert error.value.status_code == 409
    scan.assert_not_awaited()
