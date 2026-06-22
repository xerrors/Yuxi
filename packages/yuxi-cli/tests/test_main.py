from typer.testing import CliRunner

from yuxi_cli import __version__
from yuxi_cli.config import ConfigStore
from yuxi_cli.main import app


def test_version_option_without_command():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_agent_eval_help_is_registered():
    result = CliRunner().invoke(app, ["agent", "eval", "--help"])

    assert result.exit_code == 0
    assert "--dataset-name" in result.output
    assert "--create-smoke-item" not in result.output
    assert "--auth-token" not in result.output


def test_kb_upload_help_is_registered():
    result = CliRunner().invoke(app, ["kb", "upload", "--help"])

    assert result.exit_code == 0
    assert "--kb-id" in result.output
    assert "--concurrency" in result.output
    assert "--force-upload-file" in result.output
    assert "1-300" in result.output


def test_remote_command_prints_version_and_remote_context_first(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.get_remote("local")
    remote.url = "https://example.com"
    store.save(config)

    def fake_remote_ping(store_arg, name, console):
        assert store_arg is store
        assert name is None
        console.print("pong")

    monkeypatch.setattr("yuxi_cli.main._store", lambda: store)
    monkeypatch.setattr("yuxi_cli.main.remote_ping", fake_remote_ping)

    result = CliRunner().invoke(app, ["remote", "ping"])

    assert result.exit_code == 0
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert lines[:3] == [f"Yuxi CLI {__version__}", "Remote: local https://example.com", "pong"]
