from __future__ import annotations

from pathlib import Path

import pytest

from yuxi.agents.backends import skills_backend


def _prepare_skills_dir(root: Path) -> None:
    (root / "alpha").mkdir(parents=True, exist_ok=True)
    (root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\n# alpha\n",
        encoding="utf-8",
    )
    (root / "beta").mkdir(parents=True, exist_ok=True)
    (root / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: beta\n---\n# beta\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("selected_slugs", [None, ["alpha"]])
def test_selected_skills_backend_readonly_and_visible_only_selected(tmp_path, monkeypatch, selected_slugs):
    _prepare_skills_dir(tmp_path)
    monkeypatch.setattr(skills_backend, "get_skills_root_dir", lambda: tmp_path)

    backend = skills_backend.SelectedSkillsReadonlyBackend(selected_slugs=selected_slugs)

    root_result = backend.ls("/")
    paths = sorted(entry.get("path") for entry in (root_result.entries or []))
    if selected_slugs:
        assert root_result.error is None
        assert paths == ["/alpha/"]

        ok_read = backend.read("/alpha/SKILL.md")
        assert ok_read.file_data is not None
        assert "alpha" in ok_read.file_data["content"]
    else:
        assert paths == []
        assert backend.read("/alpha/SKILL.md").error == "Access denied: file is outside selected skills."

    denied_read = backend.read("/beta/SKILL.md")
    assert denied_read.error and "Access denied" in denied_read.error

    write_result = backend.write("/alpha/new.md", "x")
    assert write_result.error and "read-only" in write_result.error

    edit_result = backend.edit("/alpha/SKILL.md", "alpha", "changed")
    assert edit_result.error and "read-only" in edit_result.error

    upload_result = backend.upload_files([("/alpha/a.txt", b"a")])
    assert len(upload_result) == 1
    assert upload_result[0].error == "permission_denied"
