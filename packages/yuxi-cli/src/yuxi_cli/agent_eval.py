from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langfuse import Langfuse
from rich.console import Console

from yuxi_cli.client import YuxiClient
from yuxi_cli.config import ConfigStore


class AgentEvalError(Exception):
    pass


@dataclass
class AgentEvalOptions:
    dataset_name: str
    agent_slug: str
    experiment_name: str | None = None
    max_concurrency: int = 1
    timeout_seconds: float = 900


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def build_langfuse_client() -> Langfuse:
    kwargs: dict[str, Any] = {
        "public_key": _env("LANGFUSE_PUBLIC_KEY"),
        "secret_key": _env("LANGFUSE_SECRET_KEY"),
    }
    host = _env("LANGFUSE_BASE_URL")
    if host:
        kwargs["host"] = host
    return Langfuse(**kwargs)


def extract_query(item_input: Any) -> str:
    if isinstance(item_input, str):
        return item_input
    if isinstance(item_input, dict):
        for key in ("input", "query", "question", "prompt"):
            value = item_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    raise AgentEvalError(f"无法从 Langfuse dataset item input 中提取 query: {item_input!r}")


def run_langfuse_agent_experiment(
    store: ConfigStore,
    remote_name: str | None,
    options: AgentEvalOptions,
    console: Console,
    *,
    langfuse_factory=build_langfuse_client,
    client_factory=YuxiClient,
) -> None:
    config = store.load()
    remote = config.get_remote(remote_name)
    if not remote.api_key:
        raise AgentEvalError(f"remote 尚未登录: {remote.name}。请先运行 yuxi login。")

    if options.max_concurrency < 1:
        raise AgentEvalError("--max-concurrency 必须大于等于 1")
    if options.timeout_seconds <= 0:
        raise AgentEvalError("--timeout-seconds 必须大于 0")

    experiment_name = options.experiment_name or f"yuxi-agent-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    langfuse_client = langfuse_factory()
    dataset = langfuse_client.get_dataset(options.dataset_name)

    def task(*, item, **_kwargs):
        return _run_agent_eval_item(
            remote=remote,
            agent_slug=options.agent_slug,
            dataset_name=options.dataset_name,
            experiment_name=experiment_name,
            item=item,
            timeout_seconds=options.timeout_seconds,
            client_factory=client_factory,
        )

    result = dataset.run_experiment(
        name=experiment_name,
        task=task,
        max_concurrency=options.max_concurrency,
        metadata={
            "source": "agent_evaluation",
            "agent_slug": options.agent_slug,
            "dataset_name": options.dataset_name,
            "remote": remote.name,
        },
    )
    console.print(result.format(include_item_results=True))
    langfuse_client.flush()
    processed_count = len(result.item_results)
    total_count = len(dataset.items)
    if processed_count != total_count:
        raise AgentEvalError(f"Langfuse experiment 部分失败: {processed_count}/{total_count} 个 item 成功写入")


def _run_agent_eval_item(
    *,
    remote,
    agent_slug: str,
    dataset_name: str,
    experiment_name: str,
    item: Any,
    timeout_seconds: float,
    client_factory,
) -> str:
    query = extract_query(item.input)
    item_id = str(getattr(item, "id", "") or "")
    request_id = f"eval-{uuid.uuid4()}"
    evaluation = {
        "dataset_name": dataset_name,
        "dataset_item_id": item_id,
        "experiment_name": experiment_name,
    }
    with client_factory(remote, timeout=timeout_seconds) as client:
        result = client.run_agent_eval(
            query=query,
            agent_slug=agent_slug,
            evaluation=evaluation,
            meta={"request_id": request_id},
            timeout_seconds=timeout_seconds,
        )

    if result.get("status") != "completed":
        raise AgentEvalError(f"Agent eval run failed for dataset item {item_id}: {result}")
    return str(result.get("output") or "")
