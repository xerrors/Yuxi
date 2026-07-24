from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from yuxi_cli.config import ConfigStore
from yuxi_cli.discovery import MIN_SERVER_VERSION
from yuxi_cli.kb import (
    KbError,
    run_kb_files,
    run_kb_find,
    run_kb_list,
    run_kb_open,
    run_kb_query,
)
from yuxi_cli.main import app


class FakeKbClient:
    """Records calls and returns canned external API payloads."""

    omit_caps: set[str] = set()
    calls: list[tuple[str, tuple, dict]] = []

    def __init__(self, remote):
        self.remote = remote

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    @classmethod
    def reset(cls) -> None:
        cls.omit_caps = set()
        cls.calls = []

    @classmethod
    def _record(cls, name, *args, **kwargs):
        cls.calls.append((name, args, kwargs))

    def discovery(self):
        caps = {
            "kb_list": True,
            "kb_files": True,
            "kb_query": True,
            "kb_open": True,
            "kb_find": True,
            "kb_upload": True,
        }
        for key in self.omit_caps:
            caps.pop(key, None)
        return {"version": MIN_SERVER_VERSION, "capabilities": {"cli": caps}}

    def list_external_databases(self):
        self._record("list_external_databases")
        return {
            "databases": [
                {"kb_id": "kb_1", "name": "Docs", "kb_type": "milvus", "supports_documents": True},
                {"kb_id": "dify_1", "name": "Dify", "kb_type": "dify", "supports_documents": False},
            ]
        }

    def list_external_files(self, kb_id, **kwargs):
        self._record("list_external_files", kb_id, **kwargs)
        return {
            "files": [
                {"file_id": "f1", "filename": "report.md", "file_type": "md", "status": "indexed", "file_size": 2048, "is_folder": False}
            ],
            "total": 1,
            "offset": kwargs.get("offset", 0),
            "limit": kwargs.get("limit", 100),
            "has_more": False,
        }

    def retrieve_external(self, kb_id, **kwargs):
        self._record("retrieve_external", kb_id, **kwargs)
        return {"kb_id": kb_id, "results": [{"content": "hello world", "file_id": "f1", "metadata": {"score": 0.9}}]}

    def open_external_file(self, kb_id, file_id, **kwargs):
        self._record("open_external_file", kb_id, file_id, **kwargs)
        return {"start_line": 1, "end_line": 2, "total_lines": 2, "content": "     1\tline one\n     2\tline two"}

    def find_external_file(self, kb_id, file_id, **kwargs):
        self._record("find_external_file", kb_id, file_id, **kwargs)
        return {"total_matches": 1, "windows": [{"start_line": 1, "end_line": 1, "matched_lines": [1], "content": "     1\tfoo"}]}


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=120, highlight=False)


def _store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.get_remote("local")
    remote.api_key = "yxkey_test"
    store.save(config)
    return store


def _output(console: Console) -> str:
    return console.file.getvalue()


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeKbClient.reset()
    yield
    FakeKbClient.reset()


def test_kb_list_renders_table(tmp_path):
    console = _console()
    run_kb_list(_store(tmp_path), None, console, client_factory=FakeKbClient)
    out = _output(console)
    assert "Docs" in out
    assert "kb_1" in out
    assert "Dify" in out


def test_kb_list_json_outputs_raw_json(tmp_path):
    console = _console()
    run_kb_list(_store(tmp_path), None, console, as_json=True, client_factory=FakeKbClient)
    out = _output(console)
    assert '"databases"' in out
    assert '"kb_1"' in out


def test_kb_files_renders_table_and_propagates_query(tmp_path):
    console = _console()
    run_kb_files(
        _store(tmp_path),
        None,
        "kb_1",
        console,
        query="report",
        offset=0,
        limit=50,
        status="all",
        client_factory=FakeKbClient,
    )
    out = _output(console)
    assert "report.md" in out
    assert "f1" in out
    name, args, kwargs = FakeKbClient.calls[-1]
    assert name == "list_external_files"
    assert args[0] == "kb_1"
    assert kwargs["query"] == "report"
    assert kwargs["limit"] == 50


def test_kb_query_renders_chunks(tmp_path):
    console = _console()
    run_kb_query(
        _store(tmp_path),
        None,
        "kb_1",
        "what is yuxi",
        console,
        top_k=5,
        search_mode="hybrid",
        client_factory=FakeKbClient,
    )
    out = _output(console)
    assert "hello world" in out
    assert "file=f1" in out
    assert "score=0.9" in out
    name, args, kwargs = FakeKbClient.calls[-1]
    assert kwargs["options"] == {"final_top_k": 5, "search_mode": "hybrid"}


def test_kb_query_json_outputs_raw_json(tmp_path):
    console = _console()
    run_kb_query(_store(tmp_path), None, "kb_1", "q", console, as_json=True, client_factory=FakeKbClient)
    out = _output(console)
    assert '"results"' in out
    assert '"hello world"' in out


def test_kb_open_renders_window(tmp_path):
    console = _console()
    run_kb_open(_store(tmp_path), None, "kb_1", "f1", console, offset=0, limit=2, client_factory=FakeKbClient)
    out = _output(console)
    assert "行 1-2" in out
    assert "line one" in out


def test_kb_find_renders_windows(tmp_path):
    console = _console()
    run_kb_find(
        _store(tmp_path),
        None,
        "kb_1",
        "f1",
        ["foo"],
        console,
        use_regex=True,
        case_sensitive=True,
        max_windows=3,
        window_size=40,
        client_factory=FakeKbClient,
    )
    out = _output(console)
    assert "窗口 1" in out
    assert "foo" in out
    name, args, kwargs = FakeKbClient.calls[-1]
    assert kwargs["use_regex"] is True
    assert kwargs["case_sensitive"] is True


def test_kb_query_raises_when_capability_missing(tmp_path):
    FakeKbClient.omit_caps = {"kb_query"}
    with pytest.raises(KbError, match="kb_query"):
        run_kb_query(_store(tmp_path), None, "kb_1", "q", _console(), client_factory=FakeKbClient)


def test_kb_query_raises_when_server_version_too_old(tmp_path):
    class OldClient(FakeKbClient):
        def discovery(self):
            return {"version": "0.7.0", "capabilities": {"cli": {"kb_query": True}}}

    with pytest.raises(KbError, match="低于 CLI 要求"):
        run_kb_query(_store(tmp_path), None, "kb_1", "q", _console(), client_factory=OldClient)


def test_kb_list_requires_login(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")  # no api_key saved
    with pytest.raises(KbError, match="尚未登录"):
        run_kb_list(store, None, _console(), client_factory=FakeKbClient)


def test_kb_find_requires_pattern(tmp_path):
    with pytest.raises(KbError, match="--pattern"):
        run_kb_find(_store(tmp_path), None, "kb_1", "f1", [], _console(), client_factory=FakeKbClient)


# --- CLI command registration & help ---


def test_kb_subcommands_are_registered():
    result = CliRunner().invoke(app, ["kb", "--help"])
    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 0
    for command in ("list", "files", "query", "open", "find", "upload"):
        assert command in output
    for removed_command in ("parse-pending", "index-pending"):
        assert removed_command not in output


def test_kb_query_help_lists_key_options():
    result = CliRunner().invoke(app, ["kb", "query", "--help"])
    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 0
    assert "--kb-id" in output
    assert "--top-k" in output
    assert "--search-mode" in output
    assert "--json" in output


def test_kb_find_help_lists_repeatable_pattern():
    result = CliRunner().invoke(app, ["kb", "find", "--help"])
    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 0
    assert "--pattern" in output
    assert "--regex" in output
    assert "--case-sensitive" in output
