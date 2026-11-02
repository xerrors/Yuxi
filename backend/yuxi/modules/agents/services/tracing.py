from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from yuxi.infrastructure.observability.langfuse import CallbackHandler, export_turn_root, get_langfuse_client
from yuxi.infrastructure.observability.logging import logger


@dataclass(slots=True)
class LangfuseRunContext:
    callbacks: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    trace_id: str | None = None
    root_observation_id: str | None = None
    run_observation_id: str | None = None
    run_observation: Any | None = None
    terminal_status: str | None = None


def build_trace_metadata(
    *,
    user_id: str,
    thread_id: str,
    agent_id: str,
    turn_id: str,
    run_id: str,
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
        "turn_id": turn_id,
        "run_id": run_id,
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
    turn_id: str,
    run_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    username: str | None = None,
    login_user_id: str | None = None,
    department_id: int | str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    extra_tags: list[str] | None = None,
    parent_observation_id: str | None = None,
) -> LangfuseRunContext:
    """为同一 Turn 的执行段复用 trace，并设置可持久恢复的父观察。"""
    metadata = build_trace_metadata(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        turn_id=turn_id,
        run_id=run_id,
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

    try:
        trace_id = client.create_trace_id(seed=turn_id)
        trace_context = {"trace_id": trace_id}
        if parent_observation_id:
            trace_context["parent_span_id"] = parent_observation_id
        handler = CallbackHandler(trace_context=trace_context)
    except Exception as exc:
        logger.warning("初始化 Langfuse Run 回调失败: {}", exc)
        return LangfuseRunContext(metadata=metadata, tags=tags)
    return LangfuseRunContext(callbacks=[handler], metadata=metadata, tags=tags, trace_id=trace_id)


def start_turn_observation(context: LangfuseRunContext) -> str | None:
    """为跨进程 Turn 预留根 ID；终态后再导出覆盖整轮的观察。"""
    turn_id = context.metadata.get("turn_id")
    if get_langfuse_client() is None or not context.trace_id or not isinstance(turn_id, str) or not turn_id:
        return None
    return hashlib.blake2b(turn_id.encode(), digest_size=8).hexdigest()


async def finish_turn_observation_if_terminal(turn_id: str) -> None:
    """提交后读取 Turn 终态，再尽力导出跨 Run 和等待期的根观察。"""
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.agents.repositories.turn import AgentTurnRepository

    if get_langfuse_client() is None:
        return
    try:
        async with pg_manager.get_async_session_context() as db:
            repo = AgentTurnRepository(db)
            turn = await repo.get(turn_id)
            if (
                turn is None
                or turn.status not in {"completed", "failed", "cancelled"}
                or not turn.langfuse_root_observation_id
                or turn.finished_at is None
            ):
                return
            runs = await repo.list_runs(turn.id)
            trace_id = next((run.langfuse_trace_id for run in runs if run.langfuse_trace_id), None)
            if trace_id is None:
                return
            details = {
                "trace_id": trace_id,
                "root_id": turn.langfuse_root_observation_id,
                "turn_id": turn.id,
                "thread_id": turn.thread_id,
                "uid": turn.uid,
                "status": turn.status,
                "created_at": turn.created_at,
                "finished_at": max(turn.finished_at, datetime.now(UTC)),
            }
        await asyncio.wait_for(asyncio.to_thread(export_turn_root, **details), timeout=3)
    except Exception as exc:
        logger.warning("结束 Langfuse Turn 根观察失败: {}", exc)


def attach_run_observation(
    context: LangfuseRunContext, *, root_observation_id: str, existing_observation_id: str | None = None
) -> str | None:
    """将本 Run 的模型和工具回调绑定到同一 Turn 根观察。"""
    client = get_langfuse_client()
    if client is None or CallbackHandler is None or not context.trace_id:
        return None
    context.root_observation_id = root_observation_id
    observation_id = existing_observation_id
    if observation_id is None:
        try:
            observation = client.start_observation(
                trace_context={"trace_id": context.trace_id, "parent_span_id": root_observation_id},
                name="agent.run",
                as_type="agent",
                metadata={
                    "turn_id": context.metadata.get("turn_id"),
                    "run_id": context.metadata.get("run_id"),
                    "operation": context.metadata.get("operation"),
                },
            )
            context.run_observation = observation
            observation_id = str(observation.id)
        except Exception as exc:
            logger.warning("创建 Langfuse Run 观察失败: {}", exc)
            return None
    try:
        callback = CallbackHandler(trace_context={"trace_id": context.trace_id, "parent_span_id": observation_id})
    except Exception as exc:
        logger.warning("初始化 Langfuse 观察回调失败: {}", exc)
        if context.run_observation is not None:
            context.terminal_status = "abandoned"
            finish_run_observation(context)
        return None
    context.run_observation_id = observation_id
    context.callbacks = [callback]
    return observation_id


def finish_run_observation(context: LangfuseRunContext | None) -> None:
    """记录当前 Run 的终态并结束本进程创建的观察。"""
    if context is None or context.run_observation is None:
        return
    try:
        status = context.terminal_status or "abandoned"
        context.run_observation.update(
            metadata={"run_id": context.metadata.get("run_id"), "status": status},
            level="ERROR" if status in {"failed", "abandoned"} else "DEFAULT",
        )
        context.run_observation.end()
        context.run_observation = None
    except Exception as exc:
        logger.warning("结束 Langfuse Run 观察失败: {}", exc)


def get_trace_info(run_context: LangfuseRunContext | None) -> dict[str, Any]:
    if run_context is None:
        return {}

    metadata = run_context.metadata or {}
    trace_id = run_context.trace_id
    if not trace_id and run_context.callbacks:
        trace_id = getattr(run_context.callbacks[0], "last_trace_id", None)

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
    # later via get_trace_url_by_id_async() and patch message metadata asynchronously.
    return trace_info
