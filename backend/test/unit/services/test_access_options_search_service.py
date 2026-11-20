"""access-options 服务层：命中窗口之外的已选用户两分支都必须经补查回填。"""

from types import SimpleNamespace

import pytest

import yuxi.modules.identity.services.users as svc


def _row(uid: str):
    return (SimpleNamespace(uid=uid, username=uid, role="user", department_id=None), None)


class _Repo:
    def __init__(self, hits):
        self.calls = []
        self.hits = hits

    async def list_with_department(self, *, skip, limit):
        self.calls.append(("page", limit))
        return self.hits

    async def search_access_options(self, *, search=None, include_uids=None, limit=1000):
        self.calls.append(("search", search, tuple(include_uids or ()), limit))
        return self.hits if search else []


@pytest.mark.parametrize("keyword", [None, "zhang"])
async def test_selected_outside_hit_window_is_backfilled(monkeypatch, keyword):
    repo = _Repo([_row("u1")])
    monkeypatch.setattr(svc, "UserRepository", lambda _db: repo)

    result = await svc.list_access_options(None, skip=0, limit=1, search=keyword, include_uids=[" u1 ", "", "u2"])

    backfills = [call for call in repo.calls if call[0] == "search" and call[1] is None]
    assert backfills == [("search", None, ("u2",), 1)]
    assert [item["uid"] for item in result] == ["u1"]


async def test_no_selected_skips_backfill_query(monkeypatch):
    repo = _Repo([_row("u1")])
    monkeypatch.setattr(svc, "UserRepository", lambda _db: repo)

    await svc.list_access_options(None, skip=0, limit=50, search="alpha", include_uids=[])

    assert not [call for call in repo.calls if call[0] == "search" and call[1] is None]
    assert repo.calls[0] == ("search", "alpha", (), 50)
