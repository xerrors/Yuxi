from __future__ import annotations

import sys
import time
import webbrowser
from collections.abc import Callable

from rich.console import Console
from rich.table import Table

from yuxi_cli.client import ClientError, YuxiClient
from yuxi_cli.config import ConfigStore, Remote
from yuxi_cli.discovery import ServerCompatibilityError, ensure_server_compatible

PENDING_ERRORS = ("authorization_pending", "slow_down")


class CommandError(Exception):
    pass


def remote_add(store: ConfigStore, name: str, url: str) -> Remote:
    config = store.load()
    remote = config.set_remote(name, url)
    store.save(config)
    return remote


def remote_use(store: ConfigStore, name: str) -> Remote:
    config = store.load()
    remote = config.use_remote(name)
    store.save(config)
    return remote


def remote_list(store: ConfigStore, console: Console) -> None:
    config = store.load()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Current", width=7)
    table.add_column("Name")
    table.add_column("URL")
    table.add_column("Auth")
    for name, remote in config.remotes.items():
        table.add_row("*" if name == config.current else "", name, remote.url, "API Key" if remote.has_api_key else "-")
    console.print(table)


def remote_ping(store: ConfigStore, name: str | None, console: Console, client_factory=YuxiClient) -> None:
    config = store.load()
    remote = config.get_remote(name)
    with client_factory(remote) as client:
        data = client.health()
    console.print(f"{remote.name}: {data.get('status', 'ok')} {data.get('version', '')}".rstrip())


def login_with_api_key(
    store: ConfigStore,
    remote_name: str | None,
    api_key: str,
    console: Console,
    client_factory=YuxiClient,
) -> Remote:
    if not api_key.startswith("yxkey_"):
        raise CommandError("API Key 格式无效，应以 yxkey_ 开头")

    config = store.load()
    remote = config.get_remote(remote_name)
    with client_factory(remote) as client:
        _ensure_server_compatible(client, "cli.api_key_auth")
        client.me(api_key=api_key)  # 校验 Key 是否可用

    remote.api_key = api_key
    remote.api_key_id = ""
    store.save(config)
    console.print(f"已保存 {remote.name} 的 API Key。")
    return remote


def login_with_browser(
    store: ConfigStore,
    remote_name: str | None,
    no_open: bool,
    console: Console,
    *,
    client_factory=YuxiClient,
    open_browser: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Remote:
    config = store.load()
    remote = config.get_remote(remote_name)

    with client_factory(remote) as client:
        _ensure_server_compatible(client, "cli.browser_login")
        session = client.create_cli_session()
        authorize_url = client.authorize_url(session)
        console.print(f"授权码: {session.user_code}")
        console.print(f"浏览器授权地址: {authorize_url}")
        if not no_open:
            open_browser(authorize_url)

        deadline = monotonic() + session.expires_in
        while monotonic() < deadline:
            try:
                data = client.exchange_cli_token(session.device_code)
                api_key = data.get("secret") or ""
                api_key_meta = data.get("api_key") or {}
                if not api_key:
                    raise CommandError("远程未返回 API Key secret")

                remote.api_key = api_key
                remote.api_key_id = str(api_key_meta.get("id") or "")
                store.save(config)
                console.print(f"已完成 {remote.name} 的浏览器登录。")
                return remote
            except ClientError as exc:
                if not _should_keep_polling(exc):
                    raise
            sleep(max(1, session.interval))

    raise CommandError("浏览器授权超时")


def _should_keep_polling(exc: ClientError) -> bool:
    """轮询期间是否应继续重试：等待授权、限流，或瞬时网络/服务端错误。"""
    if exc.error_code in PENDING_ERRORS or str(exc).startswith(PENDING_ERRORS):
        return True
    # status_code 为 None 表示网络层错误；5xx 为服务端瞬时错误，均可重试。
    return exc.status_code is None or exc.status_code >= 500


def whoami(store: ConfigStore, remote_name: str | None, console: Console, client_factory=YuxiClient) -> None:
    config = store.load()
    remote = config.get_remote(remote_name)
    if not remote.api_key:
        raise CommandError(f"remote 尚未登录: {remote.name}")
    with client_factory(remote) as client:
        user = client.me()
    console.print(f"{user.get('username')} ({user.get('uid')}) - {user.get('role')}")


def status(store: ConfigStore, remote_name: str | None, console: Console, client_factory=YuxiClient) -> None:
    config = store.load()
    remote = config.get_remote(remote_name)
    with client_factory(remote) as client:
        health = client.health()
        auth = "未登录"
        if remote.api_key:
            try:
                user = client.me()
                auth = f"{user.get('username')} ({user.get('uid')})"
            except ClientError:
                auth = "API Key 无效"

    table = Table(show_header=False)
    table.add_row("Remote", remote.name)
    table.add_row("URL", remote.url)
    table.add_row("Health", f"{health.get('status', 'ok')} {health.get('version', '')}".rstrip())
    table.add_row("Auth", auth)
    console.print(table)


def logout(
    store: ConfigStore,
    remote_name: str | None,
    local_only: bool,
    console: Console,
    client_factory=YuxiClient,
) -> Remote:
    config = store.load()
    remote = config.get_remote(remote_name)
    if remote.api_key and remote.api_key_id and not local_only:
        with client_factory(remote) as client:
            client.delete_api_key(remote.api_key_id)

    remote.api_key = ""
    remote.api_key_id = ""
    store.save(config)
    console.print(f"已退出 {remote.name}。")
    return remote


def select_login_mode(console: Console) -> str:
    console.print("选择登录方式:")
    console.print("> 1. 浏览器登录（推荐）")
    console.print("  2. API Key")
    if not sys.stdin.isatty():
        return "browser"
    value = input("直接回车使用浏览器登录，输入 2 使用 API Key: ").strip()
    return "api_key" if value == "2" else "browser"


def _ensure_server_compatible(client: YuxiClient, required_capability: str) -> None:
    try:
        discovery = client.discovery()
    except ClientError as exc:
        raise CommandError(f"无法读取服务端 discovery，请确认远程是 Yuxi 0.7.1 或更高版本: {exc}") from exc
    try:
        ensure_server_compatible(discovery, required_capability)
    except ServerCompatibilityError as exc:
        raise CommandError(str(exc)) from exc
