"""External Agent invocation service.

This module owns non-chat entrypoints that invoke an Agent from another system,
currently agent-call and evaluation runs. Its job is to translate those request
shapes into normal conversation-backed ``AgentRun`` records, then adapt the
result back to the caller's response shape.

It must not create a parallel execution path. All durable run behavior is
delegated to ``agent_run_service``: idempotency, busy checks, model snapshots,
queueing, waiting for terminal status and result loading stay there. This keeps
external invocation, normal chat and subagent runs comparable in storage,
worker execution and observability.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_run_service import (
    AgentRunWaitTimeout,
    await_agent_run_result,
    create_agent_run_view,
    get_agent_run_result,
    get_agent_run_view,
)
from yuxi.services.input_message_service import (
    AgentRunInputMessage,
    build_chat_input_message,
    build_chat_input_message_from_openai_content,
)
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

EVALUATION_SOURCE = "agent_evaluation"
EVALUATION_FIELDS = ("dataset_name", "dataset_item_id", "experiment_name")
MAX_REQUEST_ID_LENGTH = 64
TRAJECTORY_SUMMARY_EVENT_LIMIT = 500
INTERRUPT_STATUSES = {"ask_user_question_required", "human_approval_required", "interrupted"}


async def create_agent_call_run_view(
    *,
    agent_slug: str,
    messages: list[dict[str, Any]],
    agent_call_meta: dict[str, Any] | None,
    requested_thread_id: str | None,
    request_id: str | None,
    model_spec: str | None,
    async_mode: bool,
    stream: bool,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    """创建外部系统非流式 Agent 调用，并返回 Agent Call 响应结构。

    Agent Call 是 HTTP/API 适配层语义：它接受 OpenAI 风格消息、支持同步等待
    或异步返回 run_id，并把最终结果包装为兼容外部调用方的 ``choices`` 结构。
    真正的 AgentRun 创建和执行仍交给 ``create_agent_invocation_run_view`` 与
    ``agent_run_service``。
    """
    agent_slug = _normalize_required_text(agent_slug, field_name="agent_slug")
    if stream:
        raise HTTPException(status_code=422, detail="agent-call 暂不支持 stream=true")

    input_message = _extract_agent_call_input_message(messages)
    normalized_request_id = _normalize_agent_call_request_id(request_id)
    normalized_thread_id = str(requested_thread_id or "").strip()
    _validate_agent_call_meta(agent_call_meta or {})

    run_response = await create_agent_invocation_run_view(
        input_message=input_message,
        agent_slug=agent_slug,
        invocation_metadata=_build_invocation_metadata(source="agent_call", invocation_meta=agent_call_meta),
        requested_thread_id=normalized_thread_id,
        request_id=normalized_request_id,
        model_spec=model_spec,
        current_user=current_user,
        db=db,
        conversation_title="Agent Call Run",
    )
    if async_mode:
        return _build_agent_call_response(
            {
                "run_id": run_response["run_id"],
                "agent_slug": agent_slug,
                "thread_id": run_response["thread_id"],
                "status": run_response["status"],
                "request_id": run_response["request_id"],
                "output": "",
            }
        )

    try:
        result = await await_agent_run_result(run_id=run_response["run_id"], current_uid=str(current_user.uid))
    except AgentRunWaitTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "message": "运行仍在进行中，等待最终结果超时",
                "run": exc.result,
            },
        ) from exc
    return _build_agent_call_response(result)


async def create_agent_eval_run_view(
    *,
    query: str,
    agent_slug: str,
    evaluation: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    image_content: str | None,
    model_spec: str | None,
    current_user: User,
    db: AsyncSession,
    include_trajectory_summary: bool = False,
) -> dict[str, Any]:
    """创建一次评估样例运行，并阻塞等待最终 AgentRun 结果。

    Eval 不维护数据集或评分规则；它只把 CLI/Langfuse 的单条样例转换为普通
    conversation-backed AgentRun，并通过 metadata 标记评估上下文，供 Langfuse
    trace 和后续结果归档使用。
    """
    agent_slug = _normalize_required_text(agent_slug, field_name="agent_slug")
    if not query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    meta = dict(meta or {})
    evaluation_metadata = _normalize_evaluation(evaluation)
    invocation_meta = {"evaluation": evaluation_metadata} if evaluation_metadata else {}
    run_response = await create_agent_invocation_run_view(
        input_message=build_chat_input_message(query, image_content),
        agent_slug=agent_slug,
        invocation_metadata=_build_invocation_metadata(source=EVALUATION_SOURCE, invocation_meta=invocation_meta),
        requested_thread_id="",
        request_id=_normalize_agent_invocation_request_id(meta),
        model_spec=model_spec,
        current_user=current_user,
        db=db,
        conversation_title="Agent Evaluation Run",
        attachment_file_ids=meta.get("attachment_file_ids") or [],
    )
    try:
        result = await await_agent_run_result(run_id=run_response["run_id"], current_uid=str(current_user.uid))
    except AgentRunWaitTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "message": "运行仍在进行中，等待最终结果超时",
                "run": exc.result,
            },
        ) from exc
    if include_trajectory_summary:
        try:
            trajectory_summary = await _load_trajectory_summary(run_response["run_id"])
            if result.get("langfuse_trace_id"):
                trajectory_summary["langfuse_trace_id"] = result["langfuse_trace_id"]
            result["trajectory_summary"] = trajectory_summary
        except Exception as e:
            logger.warning(f"Failed to load trajectory summary for run {run_response['run_id']}: {e}")
    return result


async def create_agent_invocation_run_view(
    *,
    agent_slug: str,
    input_message: AgentRunInputMessage,
    invocation_metadata: dict[str, Any],
    requested_thread_id: str,
    request_id: str,
    model_spec: str | None,
    current_user: User,
    db: AsyncSession,
    conversation_title: str,
    attachment_file_ids: list[str] | None = None,
) -> dict[str, Any]:
    """统一创建外部调用类 AgentRun，入口负责把请求解析成 input/meta。"""
    invocation_metadata = dict(invocation_metadata or {})
    if not str(invocation_metadata.get("source") or "").strip():
        raise HTTPException(status_code=422, detail="source 不能为空")

    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=current_user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    existing_run = await AgentRunRepository(db).get_run_by_request_id(request_id)
    if existing_run:
        if existing_run.uid != str(current_user.uid):
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if existing_run.agent_slug != agent_item.slug or existing_run.run_type != "chat":
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if requested_thread_id and existing_run.conversation_thread_id != requested_thread_id:
            raise HTTPException(status_code=409, detail="request_id 冲突")
        resolved_thread_id = existing_run.conversation_thread_id
    else:
        resolved_thread_id = requested_thread_id or str(uuid.uuid4())

    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(resolved_thread_id)
    if not conversation:
        await conv_repo.add_conversation(
            uid=str(current_user.uid),
            agent_id=agent_item.slug,
            title=conversation_title,
            thread_id=resolved_thread_id,
            metadata=invocation_metadata,
        )

    run_meta = {
        "request_id": request_id,
        **invocation_metadata,
    }
    if attachment_file_ids:
        run_meta["attachment_file_ids"] = list(attachment_file_ids)

    return await create_agent_run_view(
        input_message=input_message,
        agent_slug=agent_item.slug,
        thread_id=resolved_thread_id,
        meta=run_meta,
        current_uid=str(current_user.uid),
        db=db,
        model_spec=model_spec,
    )


async def get_agent_call_run_result_view(
    *,
    run_id: str,
    agent_slug: str | None,
    current_uid: str,
    db: AsyncSession,
) -> dict[str, Any]:
    run_id = str(run_id or "").strip()
    if not run_id:
        raise HTTPException(status_code=422, detail="run_id 不能为空")

    run_view = await get_agent_run_view(run_id=run_id, current_uid=current_uid, db=db)
    run = run_view["run"]
    expected_agent_slug = str(agent_slug or "").strip()
    if expected_agent_slug and run.get("agent_slug") != expected_agent_slug:
        raise HTTPException(status_code=409, detail="run_id 与 agent_slug 不匹配")

    result = await get_agent_run_result(run_id=run_id, current_uid=current_uid, db=db)
    return _build_agent_call_response(result)


async def _load_trajectory_summary(run_id: str) -> dict[str, Any]:
    """从 run event stream 读取有限事件并生成轻量轨迹摘要。"""
    events = await list_run_stream_events(run_id, after_seq="0-0", limit=TRAJECTORY_SUMMARY_EVENT_LIMIT)
    return _build_trajectory_summary(events)


def _build_trajectory_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合工具调用、工具错误和人工中断计数，避免暴露完整事件载荷。"""
    summary = _trajectory_summary_base(events)
    tool_calls: dict[str, str] = {}
    tool_errors: set[str] = set()
    open_tool_keys: dict[str, list[str]] = {}
    fallback_index = 0

    def next_tool_key(tool_call_id: str | None, name: str, *, is_start: bool, is_finish: bool) -> str:
        nonlocal fallback_index
        if tool_call_id:
            return str(tool_call_id)
        if is_finish and open_tool_keys.get(name):
            return open_tool_keys[name].pop(0)

        key = f"name:{name}:{fallback_index}"
        fallback_index += 1
        if is_start and not is_finish:
            open_tool_keys.setdefault(name, []).append(key)
        return key

    for event in events:
        event_type = event.get("event_type")
        if event_type == "interrupt":
            summary["interrupt_count"] += 1

        for chunk in _iter_event_chunks(event):
            if _is_interrupt_status_event(event_type, chunk):
                summary["interrupt_count"] += 1

            stream_event = chunk.get("stream_event")
            if isinstance(stream_event, dict) and stream_event.get("type") == "tool_call":
                name = str(stream_event.get("name") or "unknown")
                key = next_tool_key(stream_event.get("tool_call_id"), name, is_start=True, is_finish=False)
                tool_calls.setdefault(key, name)

            tool_event = chunk.get("event")
            tool_event_data = tool_event.get("data") if isinstance(tool_event, dict) else None
            if not isinstance(tool_event_data, dict):
                continue

            name = str(tool_event_data.get("tool_name") or tool_event_data.get("name") or "unknown")
            tool_event_type = tool_event_data.get("event")
            key = next_tool_key(
                tool_event_data.get("tool_call_id"),
                name,
                is_start=tool_event_type == "tool-started",
                is_finish=tool_event_type == "tool-finished",
            )
            if tool_event_type == "tool-started" or key not in tool_calls:
                tool_calls.setdefault(key, name)
            if tool_event_data.get("error") or event_type == "error":
                tool_errors.add(key)

    tools_by_name: dict[str, dict[str, Any]] = {}
    for key, name in tool_calls.items():
        item = tools_by_name.setdefault(name, {"name": name, "call_count": 0, "error_count": 0})
        item["call_count"] += 1
        if key in tool_errors:
            item["error_count"] += 1

    summary["tool_call_count"] = len(tool_calls)
    summary["tool_error_count"] = len(tool_errors)
    summary["tools"] = sorted(tools_by_name.values(), key=lambda item: item["name"])
    return summary


def _trajectory_summary_base(events: list[dict[str, Any]]) -> dict[str, Any]:
    """创建轨迹摘要的固定字段，后续只填充聚合计数。"""
    first_seq = _event_seq(events[0]) if events else None
    last_seq = _event_seq(events[-1]) if events else None
    return {
        "schema_version": 1,
        "source": "run_events",
        "event_count": len(events),
        "events_truncated": len(events) >= TRAJECTORY_SUMMARY_EVENT_LIMIT,
        "event_range": {"first_seq": first_seq, "last_seq": last_seq},
        "tool_call_count": 0,
        "tool_error_count": 0,
        "interrupt_count": 0,
        "tools": [],
    }


def _event_seq(event: dict[str, Any]) -> str | None:
    seq = event.get("seq")
    return str(seq) if seq is not None else None


def _iter_event_chunks(event: dict[str, Any]):
    payload = _event_payload(event)
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                yield item

    chunk = payload.get("chunk")
    if isinstance(chunk, dict):
        yield chunk


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    envelope = event.get("payload")
    if not isinstance(envelope, dict):
        return {}
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else {}


def _is_interrupt_status_event(event_type: str | None, chunk: dict[str, Any]) -> bool:
    return event_type not in {"interrupt", "end"} and chunk.get("status") in INTERRUPT_STATUSES


def _normalize_required_text(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    return normalized


def _normalize_agent_call_request_id(request_id: str | None) -> str:
    if request_id is None or not str(request_id).strip():
        return str(uuid.uuid4())

    normalized = str(request_id).strip()
    if len(normalized) > MAX_REQUEST_ID_LENGTH:
        raise HTTPException(status_code=422, detail=f"request_id 不能超过 {MAX_REQUEST_ID_LENGTH} 个字符")
    return normalized


def _validate_agent_call_meta(agent_call_meta: dict[str, Any]) -> None:
    """Agent Call 只允许通过 model_spec 覆盖运行模型，不允许 metadata 覆盖 Agent context。"""
    if isinstance(agent_call_meta, dict) and "context" in agent_call_meta:
        raise HTTPException(
            status_code=422,
            detail="agent_call_meta.context 不允许覆盖 Agent context，请使用 model_spec 覆盖模型",
        )


def _normalize_agent_invocation_request_id(meta: dict[str, Any] | None) -> str:
    """返回去空白并校验长度的 request_id；缺省时生成新的 UUID。"""
    raw_request_id = (meta or {}).get("request_id")
    if raw_request_id is None or not str(raw_request_id).strip():
        return str(uuid.uuid4())

    request_id = str(raw_request_id).strip()
    if len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise HTTPException(status_code=422, detail=f"request_id 不能超过 {MAX_REQUEST_ID_LENGTH} 个字符")
    return request_id


def _normalize_evaluation(evaluation: dict[str, Any] | None) -> dict[str, str]:
    """仅保留已知评估字段，并统一转成去空白的非空字符串。"""
    if not isinstance(evaluation, dict):
        return {}

    normalized: dict[str, str] = {}
    for key in EVALUATION_FIELDS:
        value = evaluation.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text
    return normalized


def _build_invocation_metadata(*, source: str, invocation_meta: dict[str, Any] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": source}
    if isinstance(invocation_meta, dict) and invocation_meta:
        if "context" in invocation_meta:
            raise HTTPException(
                status_code=422,
                detail="agent_invocation_meta.context 不允许覆盖 Agent context，请使用 model_spec 覆盖模型",
            )
        metadata["agent_invocation_meta"] = dict(invocation_meta)
    return metadata


def _normalize_agent_call_usage_metadata(usage: object) -> dict[str, int]:
    """Map provider usage metadata into the OpenAI-compatible agent-call response shape."""
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    total_tokens = usage.get("total_tokens")
    if not isinstance(prompt_tokens, int):
        prompt_tokens = 0
    if not isinstance(completion_tokens, int):
        completion_tokens = 0
    if not isinstance(total_tokens, int):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _agent_call_finish_reason(status: str) -> str | None:
    if status == "completed":
        return "stop"
    if status in {"failed", "cancelled", "interrupted"}:
        return status
    return None


def _build_agent_call_response(result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "unknown")
    output = result.get("output") if isinstance(result.get("output"), str) else ""
    usage = _normalize_agent_call_usage_metadata(result.get("usage"))
    payload: dict[str, Any] = {
        "run_id": result.get("agent_run_id") or result.get("run_id"),
        "agent_slug": result.get("agent_slug"),
        "thread_id": result.get("thread_id"),
        "status": status,
        "request_id": result.get("request_id"),
        "output": output,
        "choices": [
            {
                "index": 0,
                "messages": [{"role": "assistant", "content": output}],
                "finish_reason": _agent_call_finish_reason(status),
            }
        ],
        "usage": usage,
    }
    if result.get("error"):
        payload["error"] = result["error"]
    return payload


def _extract_agent_call_input_message(messages: list[dict[str, Any]]) -> AgentRunInputMessage:
    if not messages:
        raise HTTPException(status_code=422, detail="messages 不能为空")

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        try:
            return build_chat_input_message_from_openai_content(content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    raise HTTPException(status_code=422, detail="messages 必须包含 user 消息")
