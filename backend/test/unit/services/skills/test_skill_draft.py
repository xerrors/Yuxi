"""安装草稿只向确认入口提供经过校验的可安装快照。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from yuxi.services.skills import draft as skill_draft
from yuxi.services.skills.package import copy_skill_tree_no_symlinks
from yuxi.storage.postgres.models_business import User


def _write_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: list[dict]) -> str:
    """在临时运行目录写入草稿元数据和规范的条目目录。"""
    draft_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "skill_import_drafts" / draft_id
    root.mkdir(parents=True)
    for item in items:
        relative = item.get("source_dir")
        if isinstance(relative, str) and relative.startswith("items/") and ".." not in relative:
            (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "created_by": "owner",
                "source_type": "remote",
                "expires_at": time.time() + 300,
                "items": items,
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(skill_draft, "get_runtime_dir", lambda: tmp_path)
    return draft_id


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"slug": "demo", "source_dir": f"items/{'a' * 32}", "success": False}], "条目非法"),
        (
            [{"slug": "demo", "source_dir": f"items/{'a' * 32}"}, {"slug": "demo", "source_dir": f"items/{'b' * 32}"}],
            "重复",
        ),
        ([{"slug": "demo", "source_dir": "items/../../outside"}], "路径非法"),
        ([{"slug": " demo ", "source_dir": f"items/{'a' * 32}"}], "条目非法"),
    ],
)
def test_invalid_draft_item_never_reaches_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: list[dict], message: str
):
    """失败状态、重复身份和不可信路径在草稿边界拒绝。"""
    draft_id = _write_draft(tmp_path, monkeypatch, items)

    with pytest.raises(ValueError, match=message):
        skill_draft.load_and_select_draft_items(draft_id, None, User(uid="owner", role="user"))


def test_failed_preparation_cannot_be_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """失败报告不进入可确认条目集合。"""
    draft_id = _write_draft(tmp_path, monkeypatch, [{"slug": "demo", "source_dir": f"items/{'a' * 32}"}])
    metadata = tmp_path / "skill_import_drafts" / draft_id / "metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["failures"] = [{"slug": "broken", "error": "加载失败"}]
    metadata.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="不可安装"):
        skill_draft.load_and_select_draft_items(draft_id, ["broken"], User(uid="owner", role="user"))


def test_failed_only_draft_cannot_be_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全量准备失败时确认入口不能返回空的成功结果。"""
    draft_id = _write_draft(tmp_path, monkeypatch, [])

    with pytest.raises(ValueError, match="没有可安装"):
        skill_draft.load_and_select_draft_items(draft_id, None, User(uid="owner", role="user"))


def test_symlinked_source_root_cannot_be_staged(tmp_path: Path):
    """远程来源目录本身是符号链接时不能越界复制。"""
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="只允许普通文件和目录"):
        copy_skill_tree_no_symlinks(alias, tmp_path / "snapshot")


@pytest.mark.asyncio
async def test_partial_install_preserves_failed_and_unselected_snapshots(tmp_path, monkeypatch):
    """部分成功后重读磁盘，失败和未选条目仍可确认且成功项不再出现。"""
    from yuxi.services.skills import personal

    items = [
        {"slug": slug, "source_dir": f"items/{letter * 32}"}
        for slug, letter in [("alpha", "a"), ("beta", "b"), ("gamma", "c")]
    ]
    draft_id = _write_draft(tmp_path, monkeypatch, items)
    for item in items:
        directory = tmp_path / "skill_import_drafts" / draft_id / item["source_dir"]
        (directory / "SKILL.md").write_text(
            f"---\nname: {item['slug']}\ndescription: example\n---\nBody", encoding="utf-8"
        )
    from yuxi.workspace import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path / "users")
    operator = User(uid="owner", role="user")
    existing = paths.user_workspace_dir("owner") / "agents/skills/beta"
    existing.mkdir(parents=True)
    (existing / "sentinel.txt").write_text("existing", encoding="utf-8")

    results = await personal.confirm_personal_skill_install_draft(
        draft_id=draft_id, slugs=["alpha", "beta"], operator=operator
    )

    assert [item["success"] for item in results] == [True, False]
    root, metadata, remaining = skill_draft.load_and_select_draft_items(draft_id, None, operator)
    assert [item.slug for item in remaining] == ["beta", "gamma"]
    assert [item["slug"] for item in metadata["items"]] == ["beta", "gamma"]
    assert not (root / items[0]["source_dir"]).exists()
    assert all((item.source_dir / "SKILL.md").is_file() for item in remaining)
    assert (existing / "sentinel.txt").read_text(encoding="utf-8") == "existing"
    assert (existing.parent / "alpha/SKILL.md").is_file()
