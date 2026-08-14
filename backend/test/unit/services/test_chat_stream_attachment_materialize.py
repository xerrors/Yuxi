from __future__ import annotations

from yuxi.services import attachment_service as cs


def test_thread_attachment_objects_are_scoped_by_thread_and_file_id() -> None:
    original, parsed = cs._make_thread_attachment_objects("t-1", "f-1", "demo.txt")

    assert original == "threads/t-1/attachments/f-1/original/demo.txt"
    assert parsed == "threads/t-1/attachments/f-1/parsed/demo.md"
