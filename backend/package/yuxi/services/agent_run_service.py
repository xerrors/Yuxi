"""Agent run service (run creation, polling stream, cancel)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.agents.models import resolve_chat_model_spec
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.run_queue_service import (
    build_run_event_envelope,
    get_arq_pool,
    get_last_run_stream_seq,
    list_run_stream_events,
    normalize_after_seq,
    publish_cancel_signal,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

SSE_HEARTBEAT_SECONDS = int(os.getenv("RUN_SSE_HEARTBEAT_SECONDS", "15"))
SSE_MAX_CONNECTION_MINUTES = int(os.getenv("RUN_SSE_MAX_CONNECTION_MINUTES", "30"))
SSE_POLL_INTERVAL_SECONDS = float(os.getenv("RUN_SSE_POLL_INTERVAL_SECONDS", "1.0"))


def _validate_model_spec(model_spec: str | None) -> str | None:
    """校验对话级模型覆盖：未提供则返回 None；非法模型直接 422，不静默回退。"""
    normalized = model_spec.strip() if isinstance(model_spec, str) else None
    if not normalized:
        return None
    info = model_cache.get_model_info(normalized)
    if not info or info.model_type != "chat":
        raise HTTPException(status_code=422, detail=f"未找到可用聊天模型: '{normalized}'")
    return normalized


def _resolve_effective_model_spec(model_spec: str | None, agent_item, agent_backend) -> str:
    """解析本次 chat run 实际使用的模型：显式覆盖优先，否则配置模型，最后系统默认模型。"""
    resolved_model_spec = _validate_model_spec(model_spec)
    if resolved_model_spec:
        return resolved_model_spec

    context = agent_backend.context_schema()
    config_json = getattr(agent_item, "config_json", None) or {}
    config_context = config_json.get("context") if isinstance(config_json, dict) else {}
    if isinstance(config_context, dict):
        context.update_from_dict(config_context)
    return resolve_chat_model_spec(getattr(context, "model", None))


def _build_run_response(run) -> dict:
    return {
        "run_id": run.id,
        "thread_id": run.thread_id,
        "status": run.status,
        "request_id": run.request_id,
        "stream_url": f"/api/agent/runs/{run.id}/events",
    }


def _format_sse(data: dict, event: str, event_id: str | None = None) -> str:
    lines = [f"event: {event}", f"data: {json.dumps(data, ensure_ascii=False)}"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _format_heartbeat() -> str:
    return ": heartbeat\n\n"


def _compact_message_dict(message: dict) -> dict:
    compact = {
        key: message[key] for key in ("id", "role", "content", "type", "message_type") if message.get(key) is not None
    }
    extra_metadata = message.get("extra_metadata")
    if isinstance(extra_metadata, dict) and extra_metadata.get("attachments"):
        compact["extra_metadata"] = {"attachments": extra_metadata["attachments"]}
    return compact


def _compact_semantic_stream_event(stream_event: dict) -> dict:
    event_type = stream_event.get("type")
    if event_type == "message_delta":
        return {
            key: stream_event[key]
            for key in ("type", "message_id", "content", "reasoning_content", "additional_reasoning_content")
            if stream_event.get(key)
        }

    if event_type in {"tool_call", "tool_call_delta"}:
        compact = {
            key: stream_event[key]
            for key in ("type", "message_id", "tool_call_id", "name", "args", "args_delta")
            if stream_event.get(key) is not None and stream_event.get(key) != ""
        }
        if stream_event.get("index"):
            compact["index"] = stream_event["index"]
        return compact

    return {key: value for key, value in stream_event.items() if key not in {"thread_id", "namespace"}}


def _compact_tool_stream_event(event: dict) -> dict:
    compact = {key: event[key] for key in ("method",) if event.get(key)}
    data = event.get("data")
    if isinstance(data, dict):
        compact_data = {
            key: data[key]
            for key in ("event", "tool_call_id", "tool_name", "output", "error")
            if data.get(key) is not None and data.get(key) != ""
        }
        if compact_data:
            compact["data"] = compact_data
    return compact


def _compact_stream_chunk(chunk: dict) -> dict:
    compact = {
        key: chunk[key]
        for key in (
            "status",
            "run_id",
            "parent_run_id",
            "message",
            "error_type",
            "error_message",
            "retryable",
            "job_try",
            "questions",
            "interrupt_info",
            "source",
            "agent_state",
        )
        if chunk.get(key) is not None and chunk.get(key) != ""
    }
    if isinstance(chunk.get("msg"), dict):
        compact["msg"] = _compact_message_dict(chunk["msg"])
    if isinstance(chunk.get("stream_event"), dict):
        compact["stream_event"] = _compact_semantic_stream_event(chunk["stream_event"])
    if isinstance(chunk.get("event"), dict):
        compact["event"] = _compact_tool_stream_event(chunk["event"])
    return compact


def _request_id_from_chunk(chunk: object) -> str | None:
    if not isinstance(chunk, dict):
        return None
    request_id = chunk.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    msg = chunk.get("msg")
    extra_metadata = msg.get("extra_metadata") if isinstance(msg, dict) else None
    if isinstance(extra_metadata, dict):
        request_id = extra_metadata.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    return None


def _request_id_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    request_id = _request_id_from_chunk(payload.get("chunk"))
    if request_id:
        return request_id
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            request_id = _request_id_from_chunk(item)
            if request_id:
                return request_id
    return None


def _compact_run_event_payload(event_type: str, payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}

    if event_type == "messages":
        compact: dict = {}
        if isinstance(payload.get("items"), list):
            compact["items"] = [
                _compact_stream_chunk(item) if isinstance(item, dict) else item for item in payload["items"]
            ]
        if isinstance(payload.get("chunk"), dict):
            compact["chunk"] = _compact_stream_chunk(payload["chunk"])
        return compact

    compact = {key: value for key, value in payload.items() if key not in {"chunk", "request_id"}}
    if isinstance(payload.get("chunk"), dict):
        compact["chunk"] = _compact_stream_chunk(payload["chunk"])
    return compact


def _is_empty_agent_state(agent_state: object) -> bool:
    if not isinstance(agent_state, dict):
        return False
    return all(not value for value in agent_state.values())


def _compact_run_event_envelope(envelope: dict) -> dict | None:
    event_type = str(envelope.get("event") or "")
    payload = envelope.get("payload")
    if event_type == "metadata":
        return None
    if event_type == "custom" and isinstance(payload, dict) and payload.get("name") == "yuxi.agent_state":
        state = payload.get("agent_state")
        chunk = payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
        if _is_empty_agent_state(state) or _is_empty_agent_state(chunk.get("agent_state")):
            return None

    compact = {key: envelope[key] for key in ("run_id", "thread_id") if key in envelope}
    request_id = _request_id_from_payload(payload)
    if request_id:
        compact["request_id"] = request_id
    compact["payload"] = _compact_run_event_payload(event_type, payload)
    return compact


async def create_agent_run_view(
    *,
    query: str | None,
    agent_id: str,
    thread_id: str,
    meta: dict,
    image_content: str | None,
    current_uid: str,
    db: AsyncSession,
    model_spec: str | None = None,
    resume: object | None = None,
    parent_run_id: str | None = None,
    resume_request_id: str | None = None,
) -> dict:
    if not query and resume is None:
        raise HTTPException(status_code=422, detail="query 或 resume 不能为空")

    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")

    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(current_uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    if conversation.agent_id != agent_id:
        raise HTTPException(status_code=409, detail="已有线程已绑定智能体，不能切换")

    user_result = await db.execute(select(User).where(User.uid == str(current_uid)))
    current_user = user_result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(slug=agent_id, user=current_user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    agent_backend = agent_manager.get_agent(agent_item.backend_id)
    if not agent_backend:
        raise HTTPException(status_code=404, detail=f"智能体后端 {agent_item.backend_id} 不存在")

    run_type = "resume" if resume is not None else "chat"
    request_id = str(resume_request_id or (meta or {}).get("request_id") or uuid.uuid4())
    config = {"thread_id": thread_id, "agent_id": agent_id}
    run_repo = AgentRunRepository(db)
    # chat：快照本次实际模型；resume：沿用被恢复运行的原始模型，保证单次运行模型一致。
    resolved_model_spec = (
        _resolve_effective_model_spec(model_spec, agent_item, agent_backend) if run_type == "chat" else None
    )
    if run_type == "resume":
        if not parent_run_id:
            raise HTTPException(status_code=422, detail="parent_run_id 不能为空")
        parent_run = await run_repo.get_run_for_user(parent_run_id, str(current_uid))
        if not parent_run or parent_run.thread_id != thread_id:
            raise HTTPException(status_code=404, detail="被恢复的运行任务不存在")
        if parent_run.status != "interrupted":
            raise HTTPException(status_code=409, detail="只有 interrupted run 可以恢复")
        resolved_model_spec = (parent_run.input_payload or {}).get("model_spec")
        if resume_request_id:
            existing_resume = await run_repo.get_resume_run(parent_run_id, resume_request_id)
            if existing_resume and existing_resume.uid == str(current_uid):
                return _build_run_response(existing_resume)
    existing = await run_repo.get_run_by_request_id(request_id)
    if existing and existing.uid == str(current_uid):
        return _build_run_response(existing)
    if existing and existing.uid != str(current_uid):
        raise HTTPException(status_code=409, detail="request_id 冲突")

    run_id = str(uuid.uuid4())
    input_payload = {
        "query": query or "",
        "resume": resume,
        "parent_run_id": parent_run_id,
        "resume_request_id": resume_request_id,
        "run_type": run_type,
        "config": config or {},
        "image_content": image_content,
        "model_spec": resolved_model_spec,
        "agent_id": agent_id,
        "backend_id": agent_item.backend_id,
        "thread_id": thread_id,
        "uid": str(current_uid),
        "request_id": request_id,
        "attachment_file_ids": (meta or {}).get("attachment_file_ids") or [],
        "created_at": utc_now_naive().isoformat(),
    }
    try:
        run = await run_repo.create_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            uid=str(current_uid),
            request_id=request_id,
            input_payload=input_payload,
            conversation_id=conversation.id,
            parent_run_id=parent_run_id,
            run_type=run_type,
            resume_request_id=resume_request_id,
            checkpoint_thread_id=thread_id,
        )
        input_content = query or json.dumps(resume, ensure_ascii=False)
        input_metadata = {
            "request_id": request_id,
            "run_id": run_id,
            "run_type": run_type,
            "parent_run_id": parent_run_id,
            "resume": resume,
            "attachments": [],
            "model_spec": resolved_model_spec,
        }
        if run_type == "resume":
            input_metadata["source"] = "ask_user_question_resume"

        input_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=input_content,
            message_type="resume" if run_type == "resume" else "multimodal_image" if image_content else "text",
            image_content=image_content,
            run_id=run_id,
            request_id=request_id,
            delivery_status="complete",
            extra_metadata=input_metadata,
        )
        db.add(input_message)
        await db.flush()
        await run_repo.set_input_message(run_id, input_message.id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await run_repo.get_run_by_request_id(request_id)
        if existing and existing.uid == str(current_uid):
            return _build_run_response(existing)
        raise HTTPException(status_code=409, detail="request_id 冲突")

    queue = await get_arq_pool()
    await queue.enqueue_job("process_agent_run", run.id, _job_id=f"run:{run.id}")

    return _build_run_response(run)


async def get_agent_run_view(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    repo = AgentRunRepository(db)
    run = await repo.get_run_for_user(run_id, str(current_uid))
    if not run:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    return {"run": run.to_dict()}


async def cancel_agent_run_view(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    repo = AgentRunRepository(db)
    run = await repo.get_run_for_user(run_id, str(current_uid))
    if not run:
        raise HTTPException(status_code=404, detail="运行任务不存在")

    run = await repo.request_cancel(run_id)
    await publish_cancel_signal(run_id)
    return {"run": run.to_dict() if run else None}


async def stream_agent_run_events(
    *,
    run_id: str,
    after_seq: str,
    current_uid: str,
    verbose: bool = True,
) -> AsyncIterator[str]:
    started_at = utc_now_naive()
    last_heartbeat_ts = started_at

    last_seq = normalize_after_seq(after_seq)

    try:
        while True:
            try:
                async with pg_manager.get_async_session_context() as db:
                    repo = AgentRunRepository(db)
                    run = await repo.get_run_for_user(run_id, str(current_uid))
                    if not run:
                        yield _format_sse({"run_id": run_id, "message": "运行任务不存在"}, event="error")
                        return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Run SSE DB error for run {run_id}: {e}")
                yield _format_sse(
                    {
                        "run_id": run_id,
                        "message": "运行事件流暂时不可用，请重连",
                        "reason": "db_error",
                    },
                    event="error",
                )
                return

            try:
                events = await list_run_stream_events(run_id, after_seq=last_seq, limit=200)
            except Exception as e:
                logger.warning(f"Run SSE redis error for run {run_id}: {e}")
                yield _format_sse(
                    {
                        "run_id": run_id,
                        "message": "运行事件流暂时不可用，请重连",
                        "reason": "redis_error",
                    },
                    event="error",
                )
                return

            emitted_terminal = False
            for event in events:
                seq = str(event.get("seq") or "0-0")
                last_seq = seq
                event_type = event.get("event_type") or "message"
                envelope = event.get("payload") or {}
                if not verbose and isinstance(envelope, dict):
                    envelope = _compact_run_event_envelope(envelope)
                    if envelope is None:
                        continue
                yield _format_sse(envelope, event=event_type, event_id=seq)
                if event_type == "end":
                    emitted_terminal = True

            if emitted_terminal:
                return

            if run.status in TERMINAL_RUN_STATUSES and not events:
                terminal_seq = last_seq
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = await get_last_run_stream_seq(run_id)
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = None
                terminal_envelope = build_run_event_envelope(
                    run_id=run_id,
                    thread_id=run.thread_id,
                    event_type="end",
                    payload={"status": run.status, "request_id": run.request_id},
                    created_at=utc_now_naive().isoformat(),
                )
                if not verbose:
                    terminal_envelope = _compact_run_event_envelope(terminal_envelope)
                yield _format_sse(
                    terminal_envelope,
                    event="end",
                    event_id=terminal_seq,
                )
                return

            now = utc_now_naive()
            elapsed_seconds = (now - started_at).total_seconds()
            heartbeat_elapsed = (now - last_heartbeat_ts).total_seconds()
            if heartbeat_elapsed >= SSE_HEARTBEAT_SECONDS:
                yield _format_heartbeat()
                last_heartbeat_ts = now

            if elapsed_seconds >= SSE_MAX_CONNECTION_MINUTES * 60:
                return

            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        return


async def get_active_run_by_thread(*, thread_id: str, current_uid: str, db: AsyncSession) -> dict:
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentRun

    # 线程内的 run 是串行的，最近一条 run 即代表线程当前状态。
    # 已被回复的 interrupted run 会被更晚创建的 resume run 取代，因此不会再被当作待处理中断返回。
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.thread_id == thread_id,
            AgentRun.uid == str(current_uid),
            AgentRun.run_type.in_(["chat", "resume"]),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run and run.status in ("pending", "running", "cancel_requested", "interrupted"):
        return {"run": run.to_dict()}
    return {"run": None}
