from __future__ import annotations

from pathlib import Path

from yuxi.services import conversation_service as cs


def test_build_attachment_storage_path_uses_thread_local_uploads_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    virtual_path, host_path = cs._build_attachment_storage_path(
        user_id="u-1",
        thread_id="t-1",
        file_name="demo.txt",
    )

    assert virtual_path == "/mnt/user-data/uploads/attachments/demo.md"
    assert host_path == tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "attachments" / "demo.md"
