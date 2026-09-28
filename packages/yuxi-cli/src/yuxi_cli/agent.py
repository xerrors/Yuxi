from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from yuxi_cli.client import YuxiClient
from yuxi_cli.config import ConfigStore, Remote
from yuxi_cli.discovery import ServerCompatibilityError, ensure_server_compatible


class AgentError(Exception):
    """Agent 查询命令错误。"""


_TERMINAL_CONTROL_TRANSLATION = {
    codepoint: None
    for codepoint in (*range(32), 127, *range(128, 160))
    if codepoint not in (9, 10)
}


def run_agent_list(
    store: ConfigStore,
    remote_name: str | None,
    console: Console,
    *,
    as_json: bool = False,
    client_factory: type[YuxiClient] = YuxiClient,
) -> dict:
    """列出当前用户可调用的主 Agent。"""
    remote = _require_remote(store, remote_name)
    with client_factory(remote) as client:
        _ensure_capability(client, "cli.agent_list")
        data = client.list_agents()
    _render_agent_list(data, console, as_json=as_json)
    return data


def run_agent_show(
    store: ConfigStore,
    remote_name: str | None,
    agent_slug: str,
    console: Console,
    *,
    as_json: bool = False,
    client_factory: type[YuxiClient] = YuxiClient,
) -> dict:
    """展示当前用户可见的指定 Agent 配置。"""
    remote = _require_remote(store, remote_name)
    with client_factory(remote) as client:
        _ensure_capability(client, "cli.agent_show")
        data = client.get_agent(agent_slug)
    _render_agent_detail(data, console, as_json=as_json)
    return data


def _require_remote(store: ConfigStore, remote_name: str | None) -> Remote:
    """返回已登录的 remote。"""
    remote = store.load().get_remote(remote_name)
    if not remote.api_key:
        raise AgentError(f"remote 尚未登录: {remote.name}")
    return remote


def _ensure_capability(client: YuxiClient, capability: str) -> None:
    """确认服务端声明了命令所需能力。"""
    try:
        ensure_server_compatible(client.discovery(), capability)
    except ServerCompatibilityError as exc:
        raise AgentError(str(exc)) from exc


def _render_agent_list(data: dict, console: Console, *, as_json: bool) -> None:
    """渲染 Agent 列表或原始 JSON。"""
    agents = _validate_agent_list(data)
    if as_json:
        _print_json(data, console)
        return

    if not agents:
        console.print("没有可调用的 Agent")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Default", width=7, justify="center")
    table.add_column("Name")
    table.add_column("Slug")
    table.add_column("Description")
    for agent in agents:
        table.add_row(
            "*" if agent.get("is_default") else "",
            _text(agent.get("name")),
            _text(agent.get("slug") or agent.get("agent_id")),
            _text(agent.get("description")),
        )
    console.print(table)


def _render_agent_detail(data: dict, console: Console, *, as_json: bool) -> None:
    """渲染 Agent 详情或原始 JSON。"""
    agent, config_json, context = _validate_agent_detail(data)
    if as_json:
        _print_json(data, console)
        return

    details = Table(show_header=False, box=None, pad_edge=False)
    details.add_column(style="bold", no_wrap=True)
    details.add_column()
    details.add_row("Name", _text(agent.get("name")))
    details.add_row("Slug", _text(agent.get("slug") or agent.get("agent_id")))
    details.add_row("Description", _text(agent.get("description")))
    details.add_row("Default", "yes" if agent.get("is_default") else "no")
    details.add_row("Backend", _text(agent.get("backend_id")))
    details.add_row("Model", _text(context.get("model"), default="系统默认"))
    details.add_row("Skills", _selection(context.get("skills")))
    details.add_row("Tools", _selection(context.get("tools")))
    details.add_row("MCP servers", _selection(context.get("mcps") or []))
    details.add_row("Knowledge bases", _selection(context.get("knowledges")))
    details.add_row(
        "Subagents",
        _selection(context.get("subagents"), empty_means_default=True),
    )
    console.print(details)

    console.print("\n[bold]System prompt[/bold]")
    console.print(_text(context.get("system_prompt")))

    known_fields = {
        "model",
        "skills",
        "tools",
        "mcps",
        "knowledges",
        "subagents",
        "system_prompt",
    }
    other_context = {
        key: value for key, value in context.items() if key not in known_fields
    }
    other_config = {
        key: value for key, value in config_json.items() if key != "context"
    }
    if other_context or other_config:
        console.print("\n[bold]Other configuration[/bold]")
        _print_json({"context": other_context, **other_config}, console)


def _selection(value: Any, *, empty_means_default: bool = False) -> Text:
    """区分默认资源范围、显式空列表与具体选择。"""
    if value is None or (empty_means_default and value == []):
        return Text("默认（全部可用）")
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value) if value else "无"
    return Text(_safe_terminal_text(str(value)))


def _text(value: Any, *, default: str = "-") -> Text:
    """把服务端字段转为不含终端控制字符的纯文本。"""
    text = _safe_terminal_text(str(value)).strip() if value is not None else ""
    return Text(text or default)


def _safe_terminal_text(value: str) -> str:
    """移除可改变终端状态的 C0、DEL 与 C1 控制字符。"""
    return value.translate(_TERMINAL_CONTROL_TRANSLATION)


def _validate_agent_list(data: dict) -> list[dict]:
    """校验 Agent 列表响应的最小结构。"""
    agents = data.get("agents")
    if not isinstance(agents, list) or any(
        not isinstance(agent, dict) for agent in agents
    ):
        raise AgentError("远程 Agent 列表响应格式无效")
    return agents


def _validate_agent_detail(data: dict) -> tuple[dict, dict, dict]:
    """校验 Agent 详情响应并返回渲染所需结构。"""
    agent = data.get("agent")
    if not isinstance(agent, dict):
        raise AgentError("远程 Agent 详情响应格式无效")
    config_json = agent.get("config_json")
    if config_json is None:
        config_json = {}
    if not isinstance(config_json, dict):
        raise AgentError("远程 Agent 配置响应格式无效")
    context = config_json.get("context")
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise AgentError("远程 Agent context 响应格式无效")
    return agent, config_json, context


def _print_json(data: dict, console: Console) -> None:
    """输出保留中文的 JSON。"""
    console.file.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
