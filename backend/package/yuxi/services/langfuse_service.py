from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from yuxi.utils.logging_config import logger

try:
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler
except Exception:  # pragma: no cover - optional dependency during local test collection
    Langfuse = None  # type: ignore[assignment]
    CallbackHandler = None  # type: ignore[assignment]


_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(slots=True)
class LangfuseRunContext:
    callbacks: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    trace_id: str | None = None


def is_langfuse_enabled() -> bool:
    enabled_raw = (os.getenv("LANGFUSE_ENABLED") or "true").strip().lower()
    if enabled_raw in _FALSE_VALUES:
        return False

    if Langfuse is None or CallbackHandler is None:
        return False

    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


@lru_cache(maxsize=1)
def get_langfuse_client() -> Langfuse | None:
    if not is_langfuse_enabled():
        return None

    kwargs: dict[str, Any] = {
        "public_key": os.getenv("LANGFUSE_PUBLIC_KEY"),
        "secret_key": os.getenv("LANGFUSE_SECRET_KEY"),
    }
    host = os.getenv("LANGFUSE_BASE_URL")
    if host:
        kwargs["host"] = host

    try:
        return Langfuse(**kwargs)
    except Exception as exc:
        logger.warning(f"初始化 Langfuse 客户端失败，将跳过 tracing: {exc}")
        return None


def build_trace_metadata(
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    username: str | None = None,
    login_user_id: str | None = None,
    department_id: int | str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "langfuse_user_id": user_id,
        "langfuse_session_id": thread_id,
        "request_id": request_id,
        "thread_id": thread_id,
        "agent_id": agent_id,
        "operation": operation,
        "source": "yuxi",
        "feature": "chat",
    }

    if backend_id:
        metadata["backend_id"] = backend_id
    if message_type:
        metadata["message_type"] = message_type
    if username:
        metadata["username"] = username
    if login_user_id:
        metadata["login_user_id"] = login_user_id
    if department_id is not None:
        metadata["department_id"] = str(department_id)
    if extra_metadata:
        metadata.update(extra_metadata)

    return metadata


def build_trace_tags(
    *,
    agent_id: str,
    operation: str,
    message_type: str | None = None,
    extra_tags: list[str] | None = None,
) -> list[str]:
    tags = ["yuxi", "chat", operation, f"agent:{agent_id}"]
    if message_type:
        tags.append(f"message_type:{message_type}")
    for tag in extra_tags or []:
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def build_run_context(
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    username: str | None = None,
    login_user_id: str | None = None,
    department_id: int | str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    extra_tags: list[str] | None = None,
) -> LangfuseRunContext:
    metadata = build_trace_metadata(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=request_id,
        operation=operation,
        backend_id=backend_id,
        message_type=message_type,
        username=username,
        login_user_id=login_user_id,
        department_id=department_id,
        extra_metadata=extra_metadata,
    )
    tags = build_trace_tags(
        agent_id=agent_id,
        operation=operation,
        message_type=message_type,
        extra_tags=extra_tags,
    )

    client = get_langfuse_client()
    if client is None or CallbackHandler is None:
        return LangfuseRunContext(metadata=metadata, tags=tags)

    trace_id = client.create_trace_id(seed=request_id)
    handler = CallbackHandler(trace_context={"trace_id": trace_id})
    return LangfuseRunContext(callbacks=[handler], metadata=metadata, tags=tags, trace_id=trace_id)


def get_trace_info(run_context: LangfuseRunContext | None) -> dict[str, Any]:
    if run_context is None:
        return {}

    metadata = run_context.metadata or {}
    trace_id = run_context.trace_id
    if run_context.callbacks:
        last_trace_id = getattr(run_context.callbacks[0], "last_trace_id", None)
        if last_trace_id:
            trace_id = last_trace_id

    if not trace_id:
        return {}

    trace_info = {
        "langfuse_trace_id": trace_id,
        "langfuse_user_id": metadata.get("langfuse_user_id"),
        "langfuse_session_id": metadata.get("langfuse_session_id"),
    }

    # Do not fetch trace_url on the request critical path. Langfuse resolves the
    # project id via a remote API call, which can add noticeable latency when the
    # base URL is slow or unreachable. If a trace URL is still needed, fetch it
    # later via get_trace_url_async() and patch message metadata asynchronously.
    return trace_info


async def get_trace_url_async(
    run_context: LangfuseRunContext | None,
    *,
    timeout: float = 5.0,
) -> str | None:
    if run_context is None:
        return None

    trace_id = run_context.trace_id
    if run_context.callbacks:
        last_trace_id = getattr(run_context.callbacks[0], "last_trace_id", None)
        if last_trace_id:
            trace_id = last_trace_id

    if not trace_id:
        return None

    client = get_langfuse_client()
    if client is None:
        return None

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(client.get_trace_url, trace_id=trace_id),
            timeout=timeout,
        )
    except Exception:
        return None


def flush_langfuse() -> None:
    client = get_langfuse_client()
    if client is None:
        return

    try:
        client.flush()
    except Exception as exc:
        logger.warning(f"刷新 Langfuse 事件失败: {exc}")
