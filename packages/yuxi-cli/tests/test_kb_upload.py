from __future__ import annotations

import io
import threading
import time
from collections import Counter
from concurrent.futures import Future
from pathlib import Path

import pytest
from rich.console import Console

import yuxi_cli.kb_upload as kb_upload_module
from yuxi_cli.client import ClientError
from yuxi_cli.config import ConfigStore, Remote
from yuxi_cli.kb_upload import (
    ALREADY_EXISTS_MESSAGE,
    ALREADY_UPLOADED_MESSAGE,
    DEFAULT_CONCURRENCY,
    ExtensionOption,
    KbUploadOptions,
    KbUploadError,
    KbUploadSummary,
    LocalFile,
    MAX_CONCURRENCY,
    SkippedFile,
    _database_choices,
    _format_unsupported_summary,
    _extension_choices,
    _extension_option_label,
    _print_selection_summary,
    run_kb_upload,
    upload_files,
)


class FakeKbClient:
    uploaded: list[str] = []
    add_payload: dict | None = None
    add_payloads: list[dict] = []
    events: list[tuple[str, str | list[str]]] = []
    exists_checks: list[str] = []
    existing_files: set[str] = set()
    active_uploads = 0
    max_active_uploads = 0
    lock = threading.Lock()

    def __init__(self, remote: Remote):
        self.remote = remote

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    @classmethod
    def reset(cls) -> None:
        cls.uploaded = []
        cls.add_payload = None
        cls.add_payloads = []
        cls.events = []
        cls.exists_checks = []
        cls.existing_files = set()
        cls.active_uploads = 0
        cls.max_active_uploads = 0

    def discovery(self):
        return {
            "version": "0.7.1",
            "capabilities": {"cli": {"kb_upload": True}},
        }

    def get_database(self, kb_id: str):
        return {"kb_id": kb_id, "name": "Test KB", "kb_type": "milvus"}

    def list_databases(self):
        return {
            "databases": [
                {"kb_id": "kb_1", "name": "Milvus KB", "kb_type": "milvus"},
                {"kb_id": "dify_1", "name": "Dify KB", "kb_type": "dify"},
            ]
        }

    def get_knowledge_base_types(self):
        return {
            "kb_types": {
                "milvus": {"supports_documents": True},
                "dify": {"supports_documents": False},
            }
        }

    def get_supported_file_types(self):
        return {
            "file_types": [
                ".bmp",
                ".csv",
                ".docx",
                ".html",
                ".htm",
                ".jpeg",
                ".jpg",
                ".json",
                ".md",
                ".pdf",
                ".png",
                ".pptx",
                ".tif",
                ".tiff",
                ".txt",
                ".xls",
                ".xlsx",
            ]
        }

    def knowledge_document_exists(self, kb_id: str, filename: str) -> bool:
        with self.lock:
            type(self).exists_checks.append(filename)
            type(self).events.append(("exists", filename))
        return filename in type(self).existing_files

    def upload_knowledge_file(self, kb_id: str, path: Path):
        with self.lock:
            type(self).active_uploads += 1
            type(self).max_active_uploads = max(type(self).max_active_uploads, type(self).active_uploads)
        try:
            time.sleep(0.02)
            with self.lock:
                type(self).uploaded.append(path.name)
                type(self).events.append(("upload", path.name))
            return {
                "file_path": f"minio://knowledgebases/{kb_id}/upload/{path.name}",
                "content_hash": f"hash-{path.name}",
                "size": path.stat().st_size,
            }
        finally:
            with self.lock:
                type(self).active_uploads -= 1

    def add_uploaded_documents(self, kb_id: str, items: list[str], params: dict):
        payload = {"kb_id": kb_id, "items": list(items), "params": params}
        item_names = [item.rsplit("/", 1)[-1] for item in items]
        with self.lock:
            type(self).add_payloads.append(payload)
            type(self).events.append(("add", item_names))
            if type(self).add_payload is None:
                type(self).add_payload = {
                    "kb_id": kb_id,
                    "items": list(items),
                    "params": {
                        "content_type": params.get("content_type"),
                        "content_hashes": dict(params.get("content_hashes") or {}),
                        "file_sizes": dict(params.get("file_sizes") or {}),
                        "source_paths": dict(params.get("source_paths") or {}),
                    },
                }
            else:
                type(self).add_payload["items"].extend(items)
                type(self).add_payload["params"]["content_hashes"].update(params.get("content_hashes") or {})
                type(self).add_payload["params"]["file_sizes"].update(params.get("file_sizes") or {})
                type(self).add_payload["params"]["source_paths"].update(params.get("source_paths") or {})
        return {"status": "success", "added": len(items), "failed": 0, "items": [], "failed_items": []}


def _console():
    return Console(file=io.StringIO(), force_terminal=False)


def _store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.get_remote("local")
    remote.api_key = "yxkey_test"
    store.save(config)
    return store


def test_kb_upload_default_include_excludes_structured_and_presentation_files(tmp_path):
    FakeKbClient.reset()
    for name in [
        "a.md",
        "b.txt",
        "c.docx",
        "d.html",
        "e.htm",
        "f.json",
        "g.csv",
        "h.xls",
        "i.xlsx",
        "j.pptx",
        "k.pdf",
    ]:
        (tmp_path / name).write_text("demo", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    assert sorted(FakeKbClient.uploaded) == ["a.md", "b.txt", "c.docx", "d.html", "e.htm"]
    assert FakeKbClient.add_payload is not None
    assert len(FakeKbClient.add_payload["items"]) == 5


def test_kb_upload_default_concurrency_is_10():
    assert KbUploadOptions(path=Path(".")).concurrency == DEFAULT_CONCURRENCY == 10


def test_kb_upload_allows_concurrency_300(tmp_path):
    FakeKbClient.reset()
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=MAX_CONCURRENCY),
        _console(),
        client_factory=FakeKbClient,
    )

    assert FakeKbClient.uploaded == ["note.md"]


def test_kb_upload_rejects_concurrency_above_300(tmp_path):
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")

    with pytest.raises(KbUploadError, match="1 到 300"):
        run_kb_upload(
            _store(tmp_path),
            None,
            KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=MAX_CONCURRENCY + 1),
            _console(),
            client_factory=FakeKbClient,
        )


def test_kb_upload_preserves_relative_source_paths(tmp_path):
    FakeKbClient.reset()
    docs_dir = tmp_path / "docs" / "guide"
    docs_dir.mkdir(parents=True)
    (docs_dir / "intro.md").write_text("intro", encoding="utf-8")
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    assert FakeKbClient.add_payload is not None
    source_paths = FakeKbClient.add_payload["params"]["source_paths"]
    assert source_paths == {
        "minio://knowledgebases/kb_1/upload/intro.md": "docs/guide/intro.md",
        "minio://knowledgebases/kb_1/upload/root.txt": "root.txt",
    }


def test_kb_upload_without_kb_id_selects_only_uploadable_database(tmp_path):
    FakeKbClient.reset()
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    assert FakeKbClient.add_payload is not None
    assert FakeKbClient.add_payload["kb_id"] == "kb_1"
    assert FakeKbClient.add_payload["items"] == ["minio://knowledgebases/kb_1/upload/note.md"]


def test_kb_upload_include_ext_allows_non_default_supported_types(tmp_path):
    FakeKbClient.reset()
    (tmp_path / "data.xlsx").write_text("demo", encoding="utf-8")
    (tmp_path / "slides.pptx").write_text("demo", encoding="utf-8")
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2, include_ext="xlsx,pptx"),
        _console(),
        client_factory=FakeKbClient,
    )

    assert sorted(FakeKbClient.uploaded) == ["data.xlsx", "slides.pptx"]


def test_kb_upload_limits_upload_concurrency(tmp_path):
    FakeKbClient.reset()
    for index in range(6):
        (tmp_path / f"{index}.md").write_text("demo", encoding="utf-8")

    run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    assert FakeKbClient.max_active_uploads <= 2
    assert FakeKbClient.max_active_uploads > 1


def test_upload_files_limits_pending_submissions(monkeypatch, tmp_path):
    FakeKbClient.reset()
    files = []
    for index in range(5):
        path = tmp_path / f"{index}.md"
        path.write_text("demo", encoding="utf-8")
        files.append(LocalFile(path, path.name, ".md", path.stat().st_size))

    state = {"wait_started": False, "submitted_before_wait": 0}

    class TrackingExecutor:
        def __init__(self, max_workers: int):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def submit(self, fn, item):
            if not state["wait_started"]:
                state["submitted_before_wait"] += 1
            future = Future()
            future.set_result(fn(item))
            return future

    def tracking_wait(pending, *, return_when):
        assert return_when == kb_upload_module.FIRST_COMPLETED
        state["wait_started"] = True
        completed = {next(iter(pending))}
        return completed, set(pending) - completed

    monkeypatch.setattr(kb_upload_module, "ThreadPoolExecutor", TrackingExecutor)
    monkeypatch.setattr(kb_upload_module, "wait", tracking_wait)

    uploaded, failed, add_response = upload_files(
        Remote(name="local", url="http://localhost", api_key="yxkey_test"),
        FakeKbClient,
        "kb_1",
        files,
        concurrency=2,
        console=_console(),
    )

    assert len(uploaded) == 5
    assert failed == []
    assert add_response is not None
    assert state["submitted_before_wait"] == 2


def test_kb_upload_treats_duplicate_content_as_already_uploaded(tmp_path):
    class DuplicateContentClient(FakeKbClient):
        def upload_knowledge_file(self, kb_id: str, path: Path):
            raise ClientError(ALREADY_UPLOADED_MESSAGE, status_code=400)

    FakeKbClient.reset()
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)

    summary = run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        console,
        client_factory=DuplicateContentClient,
    )

    assert summary.uploaded == []
    assert summary.already_uploaded_count == 1
    assert summary.real_upload_failed_count == 0
    assert summary.add_failed_count == 0
    output = buffer.getvalue()
    assert "已上传过: 1" in output
    assert "上传失败: 0" in output


def test_kb_upload_skips_existing_relative_path_before_upload(tmp_path):
    FakeKbClient.reset()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "existing.md").write_text("existing", encoding="utf-8")
    (docs_dir / "new.md").write_text("new", encoding="utf-8")
    FakeKbClient.existing_files = {"docs/existing.md"}

    summary = run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    assert sorted(FakeKbClient.exists_checks) == ["docs/existing.md", "docs/new.md"]
    assert FakeKbClient.uploaded == ["new.md"]
    assert FakeKbClient.add_payload is not None
    assert FakeKbClient.add_payload["params"]["source_paths"] == {
        "minio://knowledgebases/kb_1/upload/new.md": "docs/new.md"
    }
    assert summary.already_uploaded_count == 1
    assert summary.upload_failed[0].error == ALREADY_EXISTS_MESSAGE
    assert ("upload", "existing.md") not in FakeKbClient.events


def test_kb_upload_falls_back_to_upload_when_exists_check_fails(tmp_path):
    class ExistsCheckFailedClient(FakeKbClient):
        def knowledge_document_exists(self, kb_id: str, filename: str) -> bool:
            with self.lock:
                type(self).exists_checks.append(filename)
                type(self).events.append(("exists-error", filename))
            raise ClientError("exists endpoint unavailable", status_code=404)

    FakeKbClient.reset()
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")

    summary = run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=ExistsCheckFailedClient,
    )

    assert FakeKbClient.exists_checks == ["note.md"]
    assert FakeKbClient.uploaded == ["note.md"]
    assert summary.already_uploaded_count == 0
    assert summary.add_response is not None
    assert summary.add_response["added"] == 1


def test_kb_upload_force_upload_file_skips_exists_check(tmp_path):
    FakeKbClient.reset()
    (tmp_path / "note.md").write_text("demo", encoding="utf-8")
    FakeKbClient.existing_files = {"note.md"}

    summary = run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2, force_upload_file=True),
        _console(),
        client_factory=FakeKbClient,
    )

    assert FakeKbClient.exists_checks == []
    assert FakeKbClient.uploaded == ["note.md"]
    assert summary.already_uploaded_count == 0
    assert summary.add_response is not None
    assert summary.add_response["added"] == 1


def test_kb_upload_adds_each_document_after_its_upload(tmp_path):
    FakeKbClient.reset()
    for index in range(5):
        (tmp_path / f"{index}.md").write_text("demo", encoding="utf-8")

    summary = run_kb_upload(
        _store(tmp_path),
        None,
        KbUploadOptions(path=tmp_path, kb_id="kb_1", yes=True, concurrency=2),
        _console(),
        client_factory=FakeKbClient,
    )

    added_names = [[item.rsplit("/", 1)[-1] for item in payload["items"]] for payload in FakeKbClient.add_payloads]
    assert all(len(items) == 1 for items in added_names)
    assert sorted(item for items in added_names for item in items) == [f"{index}.md" for index in range(5)]
    for index in range(5):
        name = f"{index}.md"
        upload_index = FakeKbClient.events.index(("upload", name))
        add_index = FakeKbClient.events.index(("add", [name]))
        assert upload_index < add_index
    assert summary.add_response is not None
    assert summary.add_response["added"] == 5


def test_upload_files_uses_log_progress_for_non_tty_console(tmp_path):
    FakeKbClient.reset()
    files = []
    for index in range(3):
        path = tmp_path / f"{index}.md"
        path.write_text("demo", encoding="utf-8")
        files.append(LocalFile(path, path.name, ".md", path.stat().st_size))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)

    uploaded, failed, add_response = upload_files(
        Remote(name="local", url="http://localhost", api_key="yxkey_test"),
        FakeKbClient,
        "kb_1",
        files,
        concurrency=2,
        console=console,
    )

    output = buffer.getvalue()
    assert len(uploaded) == 3
    assert failed == []
    assert add_response is not None
    assert add_response["added"] == 3
    assert "处理进度: 3/3" in output
    assert "✓" not in output


def test_upload_files_uses_progress_bar_for_tty_console(tmp_path):
    FakeKbClient.reset()
    files = []
    for index in range(3):
        path = tmp_path / f"{index}.md"
        path.write_text("demo", encoding="utf-8")
        files.append(LocalFile(path, path.name, ".md", path.stat().st_size))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None, width=100)

    uploaded, failed, add_response = upload_files(
        Remote(name="local", url="http://localhost", api_key="yxkey_test"),
        FakeKbClient,
        "kb_1",
        files,
        concurrency=2,
        console=console,
    )

    output = buffer.getvalue()
    assert len(uploaded) == 3
    assert failed == []
    assert add_response is not None
    assert add_response["added"] == 3
    assert "处理进度" in output
    assert "处理进度:" not in output
    assert "0.md" not in output


def test_database_choices_use_labels_without_numbering():
    choices = _database_choices(
        [
            {"kb_id": "kb_1", "name": "Alpha", "kb_type": "milvus"},
            {"kb_id": "kb_2", "name": "Beta", "kb_type": "milvus"},
        ]
    )

    assert choices[0].title == "Alpha  [milvus]  kb_1"
    assert choices[0].value == 0
    assert choices[1].title == "Beta  [milvus]  kb_2"
    assert choices[1].value == 1
    assert "1" not in str(choices[0].title).split("Alpha", 1)[0]


def test_extension_choices_show_counts_and_default_selection():
    choices = _extension_choices(
        [
            ExtensionOption(".html", 101),
            ExtensionOption(".md", 165),
            ExtensionOption(".txt", 2),
            ExtensionOption(".json", 9),
        ],
        selected_extensions={".html", ".md", ".txt"},
    )

    assert [choice.title for choice in choices] == ["html (101)", "md (165)", "txt (2)", "json (9)"]
    assert [choice.value for choice in choices] == [".html", ".md", ".txt", ".json"]
    assert [choice.checked for choice in choices] == [True, True, True, False]
    assert _extension_option_label(ExtensionOption(".tar.gz", 3)) == "tar.gz (3)"


def test_unsupported_summary_truncates_extensions_without_per_extension_counts():
    summary = _format_unsupported_summary(
        Counter(
            {
                ".py": 100,
                ".json": 90,
                ".js": 80,
                ".ts": 70,
                ".map": 60,
                ".css": 50,
                ".yaml": 40,
                ".lock": 30,
                ".mjs": 20,
                ".cjs": 10,
            }
        )
    )

    assert summary == "不支持: 550 (.py, .json, .js, .ts, .map, .css, .yaml, .lock, 等 2 类)"
    assert ".py 100" not in summary
    assert ".json 90" not in summary


def test_selection_summary_shows_compact_selected_type_summary(tmp_path):
    selected = [LocalFile(tmp_path / "a.md", "a.md", ".md", 4)]
    skipped = [
        SkippedFile(tmp_path / "b.json", "b.json", "not-included"),
        SkippedFile(tmp_path / "c.py", "c.py", "unsupported"),
    ]
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)

    _print_selection_summary(KbUploadSummary(scanned=3, selected=selected, skipped=skipped), console)

    output = buffer.getvalue()
    assert "  扫描文件: 3" in output
    assert "  将上传: 1 (.md)" in output
    assert "  未选择: 1" in output
    assert "  不支持: 1 (.py)" in output
    assert "文件类型:" not in output
    assert "[x]" not in output
    assert "[ ]" not in output
