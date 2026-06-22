from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from yuxi_cli import __version__
from yuxi_cli.agent_eval import AgentEvalError, AgentEvalOptions, run_langfuse_agent_experiment
from yuxi_cli.client import ClientError
from yuxi_cli.commands import (
    CommandError,
    login_with_api_key,
    login_with_browser,
    logout as logout_command,
    remote_add,
    remote_list,
    remote_ping,
    remote_use,
    select_login_mode,
    status as status_command,
    whoami as whoami_command,
)
from yuxi_cli.config import ConfigError, ConfigStore
from yuxi_cli.kb_upload import DEFAULT_CONCURRENCY, MAX_CONCURRENCY, KbUploadError, KbUploadOptions, run_kb_upload

console = Console()
app = typer.Typer(help="Yuxi command line client.", invoke_without_command=True)
remote_app = typer.Typer(help="Manage Yuxi remotes.")
agent_app = typer.Typer(help="Run and manage Yuxi agents.")
kb_app = typer.Typer(help="Upload and manage knowledge base files.")
app.add_typer(remote_app, name="remote")
app.add_typer(agent_app, name="agent")
app.add_typer(kb_app, name="kb")


def _store() -> ConfigStore:
    return ConfigStore()


def _print_remote_context(store: ConfigStore, remote_name: str | None) -> None:
    remote = store.load().get_remote(remote_name)
    console.print(f"Yuxi CLI {__version__}")
    console.print(f"Remote: {remote.name} {remote.url}")


def _handle_error(exc: Exception) -> None:
    console.print(f"[red]错误:[/red] {exc}")
    raise typer.Exit(1) from exc


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show version and exit.", is_eager=True),
):
    if version:
        console.print(__version__)
        raise typer.Exit()


@remote_app.command("add")
def add_remote(name: str, url: str):
    try:
        remote = remote_add(_store(), name, url)
        console.print(f"已保存 remote {remote.name}: {remote.url}")
    except ConfigError as exc:
        _handle_error(exc)


@remote_app.command("use")
def use_remote(name: str):
    try:
        remote = remote_use(_store(), name)
        console.print(f"当前 remote: {remote.name}")
    except ConfigError as exc:
        _handle_error(exc)


@remote_app.command("list")
def list_remotes():
    try:
        remote_list(_store(), console)
    except ConfigError as exc:
        _handle_error(exc)


@remote_app.command("ping")
def ping_remote(name: str | None = typer.Argument(None)):
    store = _store()
    try:
        _print_remote_context(store, name)
        remote_ping(store, name, console)
    except (ConfigError, ClientError) as exc:
        _handle_error(exc)


@app.command()
def login(
    remote: str | None = typer.Option(None, "--remote", help="Remote name."),
    browser: bool = typer.Option(False, "--browser", help="Use browser login."),
    api_key: str | None = typer.Option(None, "--api-key", help="Import an existing API Key."),
    no_open: bool = typer.Option(False, "--no-open", help="Print browser URL without opening it."),
):
    if browser and api_key:
        _handle_error(CommandError("--browser 和 --api-key 不能同时使用"))
    store = _store()
    try:
        _print_remote_context(store, remote)
        if api_key:
            login_with_api_key(store, remote, api_key, console)
            return
        if browser:
            login_with_browser(store, remote, no_open, console)
            return

        mode = select_login_mode(console)
        if mode == "api_key":
            typed_key = typer.prompt("API Key", hide_input=True)
            login_with_api_key(store, remote, typed_key, console)
        else:
            login_with_browser(store, remote, no_open, console)
    except (ConfigError, ClientError, CommandError) as exc:
        _handle_error(exc)


@app.command()
def whoami(remote: str | None = typer.Option(None, "--remote", help="Remote name.")):
    store = _store()
    try:
        _print_remote_context(store, remote)
        whoami_command(store, remote, console)
    except (ConfigError, ClientError, CommandError) as exc:
        _handle_error(exc)


@app.command()
def status(remote: str | None = typer.Option(None, "--remote", help="Remote name.")):
    store = _store()
    try:
        _print_remote_context(store, remote)
        status_command(store, remote, console)
    except (ConfigError, ClientError) as exc:
        _handle_error(exc)


@app.command()
def logout(
    remote: str | None = typer.Option(None, "--remote", help="Remote name."),
    local_only: bool = typer.Option(False, "--local-only", help="Only remove local credentials."),
):
    store = _store()
    try:
        _print_remote_context(store, remote)
        logout_command(store, remote, local_only, console)
    except (ConfigError, ClientError) as exc:
        _handle_error(exc)


@kb_app.command("upload")
def upload_knowledge_base_files(
    path: Path = typer.Argument(..., help="File or directory to upload."),
    kb_id: str | None = typer.Option(None, "--kb-id", help="Knowledge base ID. Prompt when omitted."),
    remote: str | None = typer.Option(None, "--remote", help="Remote name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    concurrency: int = typer.Option(
        DEFAULT_CONCURRENCY,
        "--concurrency",
        help=f"Concurrent upload count, 1-{MAX_CONCURRENCY}.",
    ),
    include_ext: str | None = typer.Option(None, "--include-ext", help="Comma separated extension allowlist."),
    exclude_ext: str | None = typer.Option(None, "--exclude-ext", help="Comma separated extension denylist."),
    force_upload_file: bool = typer.Option(
        False,
        "--force-upload-file",
        help="Skip remote filename existence check before upload.",
    ),
):
    options = KbUploadOptions(
        path=path,
        kb_id=kb_id,
        yes=yes,
        concurrency=concurrency,
        include_ext=include_ext,
        exclude_ext=exclude_ext,
        force_upload_file=force_upload_file,
    )
    store = _store()
    try:
        _print_remote_context(store, remote)
        run_kb_upload(store, remote, options, console)
    except (ConfigError, ClientError, KbUploadError) as exc:
        _handle_error(exc)


@agent_app.command("eval")
def eval_agent(
    dataset_name: str = typer.Option(..., "--dataset-name", help="Langfuse dataset name."),
    agent_slug: str = typer.Option(..., "--agent-slug", help="Yuxi agent slug."),
    experiment_name: str | None = typer.Option(None, "--experiment-name", help="Langfuse experiment name."),
    remote: str | None = typer.Option(None, "--remote", help="Remote name."),
    max_concurrency: int = typer.Option(1, "--max-concurrency", help="Langfuse experiment max concurrency."),
    timeout_seconds: float = typer.Option(900, "--timeout-seconds", help="Per item Yuxi API timeout."),
):
    options = AgentEvalOptions(
        dataset_name=dataset_name,
        agent_slug=agent_slug,
        experiment_name=experiment_name,
        max_concurrency=max_concurrency,
        timeout_seconds=timeout_seconds,
    )
    store = _store()
    try:
        _print_remote_context(store, remote)
        run_langfuse_agent_experiment(store, remote, options, console)
    except (ConfigError, ClientError, AgentEvalError) as exc:
        _handle_error(exc)
