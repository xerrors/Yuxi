from __future__ import annotations

import io

import pytest
from rich.console import Console

from yuxi_cli.client import CLIAuthSession, ClientError
from yuxi_cli.commands import CommandError, login_with_api_key, login_with_browser, logout
from yuxi_cli.config import ConfigStore, Remote


class FakeClient:
    def __init__(self, remote: Remote):
        self.remote = remote
        self.exchanges = 0
        self.deleted_api_key_id = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def discovery(self):
        return {
            "version": "0.7.1",
            "capabilities": {
                "cli": {
                    "browser_login": True,
                    "api_key_auth": True,
                }
            },
        }

    def me(self, api_key=None):
        assert api_key == "yxkey_existing" or self.remote.api_key
        return {
            "id": 1,
            "uid": "admin",
            "username": "Admin",
            "role": "superadmin",
        }

    def create_cli_session(self):
        return CLIAuthSession(
            device_code="yxcli_device",
            user_code="ABCD-EFGH",
            verification_uri="/auth/cli/authorize",
            expires_in=30,
            interval=1,
        )

    def authorize_url(self, session):
        return f"{self.remote.url}{session.authorize_path}"

    def exchange_cli_token(self, _device_code):
        self.exchanges += 1
        if self.exchanges == 1:
            raise ClientError("authorization_pending: 等待浏览器授权")
        return {
            "secret": "yxkey_browser",
            "api_key": {"id": 42},
            "user": {"id": 1, "uid": "admin", "username": "Admin", "role": "superadmin"},
        }

    def delete_api_key(self, api_key_id):
        self.deleted_api_key_id = api_key_id
        return {"success": True}


def _console():
    return Console(file=io.StringIO(), force_terminal=False)


def test_login_with_api_key_saves_remote_credentials(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")

    remote = login_with_api_key(store, None, "yxkey_existing", _console(), client_factory=FakeClient)

    loaded = store.load().get_remote("local")
    assert remote.api_key == "yxkey_existing"
    assert loaded.api_key == "yxkey_existing"


def test_login_with_browser_polls_until_token_and_saves_credentials(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    opened = []
    sleeps = []
    clock = {"value": 0}

    def monotonic():
        clock["value"] += 1
        return clock["value"]

    remote = login_with_browser(
        store,
        None,
        no_open=False,
        console=_console(),
        client_factory=FakeClient,
        open_browser=lambda url: opened.append(url) or True,
        sleep=lambda seconds: sleeps.append(seconds),
        monotonic=monotonic,
    )

    assert opened == ["http://localhost:5173/auth/cli/authorize?user_code=ABCD-EFGH"]
    assert sleeps == [1]
    assert remote.api_key == "yxkey_browser"
    assert remote.api_key_id == "42"
    assert store.load().get_remote("local").api_key == "yxkey_browser"


def test_login_rejects_unsupported_server_version(tmp_path):
    class OldServerClient(FakeClient):
        def discovery(self):
            return {
                "version": "0.7.0",
                "capabilities": {
                    "cli": {
                        "browser_login": True,
                        "api_key_auth": True,
                    }
                },
            }

    store = ConfigStore(tmp_path / "config.toml")

    with pytest.raises(CommandError, match="低于 CLI 要求 0.7.1"):
        login_with_api_key(store, None, "yxkey_existing", _console(), client_factory=OldServerClient)


def test_login_with_browser_retries_transient_errors(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    clock = {"value": 0}

    class FlakyClient(FakeClient):
        def exchange_cli_token(self, _device_code):
            self.exchanges += 1
            if self.exchanges == 1:
                raise ClientError("请求远程失败: 连接被重置")  # 网络层错误，status_code=None
            if self.exchanges == 2:
                raise ClientError("HTTP 503", status_code=503)  # 服务端瞬时错误
            return {
                "secret": "yxkey_browser",
                "api_key": {"id": 42},
                "user": {"id": 1, "uid": "admin", "username": "Admin", "role": "superadmin"},
            }

    remote = login_with_browser(
        store,
        None,
        no_open=True,
        console=_console(),
        client_factory=FlakyClient,
        open_browser=lambda url: True,
        sleep=lambda seconds: None,
        monotonic=lambda: clock.__setitem__("value", clock["value"] + 1) or clock["value"],
    )

    assert remote.api_key == "yxkey_browser"


def test_login_with_browser_aborts_on_terminal_error(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    clock = {"value": 0}

    class ExpiredClient(FakeClient):
        def exchange_cli_token(self, _device_code):
            raise ClientError("expired_token: 授权会话已过期", error_code="expired_token", status_code=410)

    with pytest.raises(ClientError, match="expired_token"):
        login_with_browser(
            store,
            None,
            no_open=True,
            console=_console(),
            client_factory=ExpiredClient,
            open_browser=lambda url: True,
            sleep=lambda seconds: None,
            monotonic=lambda: clock.__setitem__("value", clock["value"] + 1) or clock["value"],
        )


def test_logout_clears_local_credentials(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.get_remote("local")
    remote.api_key = "yxkey_browser"
    remote.api_key_id = "42"
    store.save(config)

    logout(store, None, local_only=True, console=_console(), client_factory=FakeClient)

    loaded = store.load().get_remote("local")
    assert loaded.api_key == ""
    assert loaded.api_key_id == ""
