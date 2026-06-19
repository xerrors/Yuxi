from __future__ import annotations

import argparse
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from langfuse import Langfuse


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def _build_langfuse_client() -> Langfuse:
    kwargs: dict[str, Any] = {
        "public_key": _env("LANGFUSE_PUBLIC_KEY"),
        "secret_key": _env("LANGFUSE_SECRET_KEY"),
    }
    host = _env("LANGFUSE_BASE_URL")
    if host:
        kwargs["host"] = host
    return Langfuse(**kwargs)


def _login(api_base_url: str, username: str, password: str) -> str:
    response = httpx.post(
        f"{api_base_url.rstrip('/')}/api/auth/token",
        data={"username": username, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _resolve_auth_token(args: argparse.Namespace) -> str:
    token = args.auth_token or _env("YUXI_EVAL_AUTH_TOKEN")
    if token:
        return token

    username = args.username or _env("YUXI_EVAL_USERNAME") or _env("TEST_USERNAME")
    password = args.password or _env("YUXI_EVAL_PASSWORD") or _env("TEST_PASSWORD")
    if not username or not password:
        raise SystemExit(
            "需要提供 --auth-token，或提供 --username/--password，或设置 YUXI_EVAL_USERNAME/YUXI_EVAL_PASSWORD"
        )
    return _login(args.api_base_url, username, password)


def _extract_query(item_input: Any) -> str:
    if isinstance(item_input, str):
        return item_input
    if isinstance(item_input, dict):
        for key in ("input", "query", "question", "prompt"):
            value = item_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    raise ValueError(f"无法从 dataset item input 中提取 query: {item_input!r}")


def _run_agent_eval_item(
    *,
    api_base_url: str,
    auth_token: str,
    agent_slug: str,
    dataset_name: str,
    experiment_name: str,
    item: Any,
    timeout_seconds: float,
) -> str:
    query = _extract_query(item.input)
    item_id = str(getattr(item, "id", "") or "")
    request_id = f"eval-{uuid.uuid4()}"
    payload = {
        "agent_slug": agent_slug,
        "query": query,
        "evaluation": {
            "dataset_name": dataset_name,
            "dataset_item_id": item_id,
            "experiment_name": experiment_name,
        },
        "meta": {"request_id": request_id},
    }
    headers = {"Authorization": f"Bearer {auth_token}"}
    url = f"{api_base_url.rstrip('/')}/api/agent/eval/runs"

    response = httpx.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    result = response.json()

    if result.get("status") != "completed":
        raise RuntimeError(f"Agent eval run failed for dataset item {item_id}: {result}")
    return str(result.get("output") or "")


def _ensure_smoke_dataset_item(args: argparse.Namespace, client: Langfuse) -> None:
    if not args.create_smoke_item:
        return
    try:
        client.create_dataset(
            name=args.dataset_name,
            description="Yuxi agent evaluation smoke dataset",
        )
    except Exception as exc:
        print(f"[smoke] create_dataset skipped: {exc}")
    try:
        client.create_dataset_item(
            dataset_name=args.dataset_name,
            id=args.smoke_item_id,
            input=args.smoke_input,
            expected_output=args.smoke_expected_output,
            metadata={"source": "yuxi_agent_eval_smoke"},
        )
    except Exception as exc:
        print(f"[smoke] create_dataset_item skipped: {exc}")
    client.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Yuxi agent against a Langfuse dataset.")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--agent-slug", required=True)
    parser.add_argument("--experiment-name")
    parser.add_argument("--api-base-url", default=_env("YUXI_API_BASE_URL", "http://localhost:5050"))
    parser.add_argument("--auth-token")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--create-smoke-item", action="store_true")
    parser.add_argument("--smoke-item-id", default="yuxi-agent-eval-smoke-1")
    parser.add_argument("--smoke-input", default="请只回答数字：2+2 等于几？")
    parser.add_argument("--smoke-expected-output", default="4")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    experiment_name = args.experiment_name or f"yuxi-agent-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    client = _build_langfuse_client()
    _ensure_smoke_dataset_item(args, client)
    auth_token = _resolve_auth_token(args)
    dataset = client.get_dataset(args.dataset_name)

    def task(*, item, **_kwargs):
        return _run_agent_eval_item(
            api_base_url=args.api_base_url,
            auth_token=auth_token,
            agent_slug=args.agent_slug,
            dataset_name=args.dataset_name,
            experiment_name=experiment_name,
            item=item,
            timeout_seconds=args.timeout_seconds,
        )

    result = dataset.run_experiment(
        name=experiment_name,
        task=task,
        max_concurrency=args.max_concurrency,
        metadata={
            "source": "agent_evaluation",
            "agent_slug": args.agent_slug,
            "dataset_name": args.dataset_name,
        },
    )
    print(result.format(include_item_results=True))
    client.flush()


if __name__ == "__main__":
    main()
