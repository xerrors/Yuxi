from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest
from rich.console import Console

from yuxi_cli.agent import AgentError, run_agent_list, run_agent_show
from yuxi_cli.client import ClientError
from yuxi_cli.config import ConfigStore
from yuxi_cli.discovery import MIN_SERVER_VERSION


class FakeAgentClient:
    """记录 Agent 查询并返回固定的可见配置。"""

    omit_caps: ClassVar[set[str]] = set()
    calls: ClassVar[list[tuple[str, tuple]]] = []

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

    def discovery(self):
        capabilities = {"agent_list": True, "agent_show": True}
        for name in self.omit_caps:
            capabilities.pop(name, None)
        return {"version": MIN_SERVER_VERSION, "capabilities": {"cli": capabilities}}

    def list_agents(self):
        self.calls.append(("list_agents", ()))
        return {
            "agents": [
                {
                    "name": "默认助手",
                    "slug": "default-chatbot",
                    "description": "通用 Agent",
                    "is_default": True,
                },
                {
                    "name": "Research [red]Agent[/red]",
                    "slug": "research-agent",
                    "description": "深度研究",
                    "is_default": False,
                },
            ]
        }

    def get_agent(self, agent_slug):
        self.calls.append(("get_agent", (agent_slug,)))
        return {
            "agent": {
                "name": "研究助手",
                "slug": agent_slug,
                "description": "调研与核验",
                "backend_id": "ChatbotAgent",
                "is_default": False,
                "config_json": {
                    "context": {
                        "model": "openai:gpt-5",
                        "skills": ["deep-research"],
                        "tools": ["web_search", "read_file"],
                        "mcps": None,
                        "subagents": [],
                        "system_prompt": "先核验证据。\n再给结论。",
                        "max_execution_steps": 100,
                    }
                },
            }
        }


def _console(*, force_terminal: bool = False) -> Console:
    return Console(
        file=io.StringIO(),
        force_terminal=force_terminal,
        width=140,
        highlight=False,
    )


def _store(tmp_path: Path, *, authenticated: bool = True) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.toml")
    if authenticated:
        config = store.load()
        config.get_remote("local").api_key = "yxkey_test"
        store.save(config)
    return store


def _output(console: Console) -> str:
    return console.file.getvalue()


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAgentClient.reset()
    yield
    FakeAgentClient.reset()


def test_agent_list_renders_visible_agents_and_default_marker(tmp_path):
    console = _console()

    run_agent_list(_store(tmp_path), None, console, client_factory=FakeAgentClient)

    output = _output(console)
    assert "default-chatbot" in output
    assert "通用 Agent" in output
    assert "*" in output
    assert "Research [red]Agent[/red]" in output
    assert FakeAgentClient.calls == [("list_agents", ())]


def test_agent_list_json_outputs_server_payload(tmp_path):
    console = _console(force_terminal=True)

    run_agent_list(
        _store(tmp_path), None, console, as_json=True, client_factory=FakeAgentClient
    )

    output = _output(console)
    assert "\x1b" not in output
    assert json.loads(output)["agents"][0]["is_default"] is True


def test_agent_list_human_output_removes_terminal_control_sequences(tmp_path):
    class UnsafeClient(FakeAgentClient):
        def list_agents(self):
            return {
                "agents": [
                    {
                        "name": "\x1b]8;;https://example.test\x07Agent\x1b]8;;\x07",
                        "slug": "unsafe",
                        "description": "\x1b[31mred\x1b[0m",
                    }
                ]
            }

    console = _console()
    run_agent_list(_store(tmp_path), None, console, client_factory=UnsafeClient)

    output = _output(console)
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "Agent" in output


def test_agent_list_reports_empty_result(tmp_path):
    class EmptyClient(FakeAgentClient):
        def list_agents(self):
            return {"agents": []}

    console = _console()
    run_agent_list(_store(tmp_path), None, console, client_factory=EmptyClient)

    assert "没有可调用的 Agent" in _output(console)


def test_agent_show_renders_key_and_remaining_configuration(tmp_path):
    console = _console()

    run_agent_show(
        _store(tmp_path),
        None,
        "research-agent",
        console,
        client_factory=FakeAgentClient,
    )

    output = _output(console)
    assert "openai:gpt-5" in output
    assert "deep-research" in output
    assert "web_search, read_file" in output
    mcp_line = next(line for line in output.splitlines() if "MCP servers" in line)
    subagents_line = next(line for line in output.splitlines() if "Subagents" in line)
    assert "无" in mcp_line
    assert "默认（全部可用）" in subagents_line
    assert "先核验证据。\n再给结论。" in output
    assert '"max_execution_steps": 100' in output
    assert FakeAgentClient.calls == [("get_agent", ("research-agent",))]


def test_agent_show_json_outputs_server_payload(tmp_path):
    console = _console(force_terminal=True)

    run_agent_show(
        _store(tmp_path),
        None,
        "research-agent",
        console,
        as_json=True,
        client_factory=FakeAgentClient,
    )

    output = _output(console)
    assert "\x1b" not in output
    agent = json.loads(output)["agent"]
    assert "config_json" in agent
    assert "system_prompt" in agent["config_json"]["context"]


def test_agent_commands_require_login(tmp_path):
    with pytest.raises(AgentError, match="尚未登录"):
        run_agent_list(
            _store(tmp_path, authenticated=False),
            None,
            _console(),
            client_factory=FakeAgentClient,
        )


@pytest.mark.parametrize(
    ("command", "capability"), [("list", "agent_list"), ("show", "agent_show")]
)
def test_agent_commands_reject_missing_server_capability(tmp_path, command, capability):
    FakeAgentClient.omit_caps = {capability}

    with pytest.raises(AgentError, match=f"cli.{capability}"):
        if command == "list":
            run_agent_list(
                _store(tmp_path), None, _console(), client_factory=FakeAgentClient
            )
        else:
            run_agent_show(
                _store(tmp_path),
                None,
                "research-agent",
                _console(),
                client_factory=FakeAgentClient,
            )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"agent": []}, "详情响应格式无效"),
        ({"agent": {"config_json": []}}, "配置响应格式无效"),
        ({"agent": {"config_json": {"context": []}}}, "context 响应格式无效"),
    ],
)
def test_agent_show_rejects_malformed_payload(tmp_path, payload, message):
    class InvalidClient(FakeAgentClient):
        def get_agent(self, _agent_slug):
            return payload

    with pytest.raises(AgentError, match=message):
        run_agent_show(
            _store(tmp_path),
            None,
            "research-agent",
            _console(),
            client_factory=InvalidClient,
        )


@pytest.mark.parametrize("as_json", [False, True])
def test_agent_list_rejects_malformed_payload_in_all_output_modes(tmp_path, as_json):
    class InvalidClient(FakeAgentClient):
        def list_agents(self):
            return {"agents": {"slug": "wrong"}}

    with pytest.raises(AgentError, match="列表响应格式无效"):
        run_agent_list(
            _store(tmp_path),
            None,
            _console(),
            as_json=as_json,
            client_factory=InvalidClient,
        )


def test_agent_show_json_rejects_malformed_payload(tmp_path):
    class InvalidClient(FakeAgentClient):
        def get_agent(self, _agent_slug):
            return {"agent": {"config_json": {"context": []}}}

    with pytest.raises(AgentError, match="context 响应格式无效"):
        run_agent_show(
            _store(tmp_path),
            None,
            "research-agent",
            _console(),
            as_json=True,
            client_factory=InvalidClient,
        )


def test_agent_show_preserves_not_found_error(tmp_path):
    class MissingClient(FakeAgentClient):
        def get_agent(self, _agent_slug):
            raise ClientError("智能体不存在", status_code=404)

    with pytest.raises(ClientError, match="智能体不存在") as exc_info:
        run_agent_show(
            _store(tmp_path),
            None,
            "hidden-agent",
            _console(),
            client_factory=MissingClient,
        )

    assert exc_info.value.status_code == 404
