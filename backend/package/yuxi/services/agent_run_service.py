"""Agent run service (run creation, polling stream, cancel)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.run_queue_service import (
    get_arq_pool,
    get_last_run_stream_seq,
    list_run_stream_events,
    normalize_after_seq,
    publish_cancel_signal,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

SSE_HEARTBEAT_SECONDS = int(os.getenv("RUN_SSE_HEARTBEAT_SECONDS", "15"))
SSE_MAX_CONNECTION_MINUTES = int(os.getenv("RUN_SSE_MAX_CONNECTION_MINUTES", "30"))
SSE_POLL_INTERVAL_SECONDS = float(os.getenv("RUN_SSE_POLL_INTERVAL_SECONDS", "1.0"))


def _build_run_response(run) -> dict:
    return {
        "run_id": run.id,
        "thread_id": run.thread_id,
        "status": run.status,
        "request_id": run.request_id,
        "stream_url": f"/api/chat/runs/{run.id}/events?after_seq=0",
    }


def _format_sse(data: dict, event: str | None = None) -> str:
    lines = []
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    lines.append("")
    return "\n".join(lines) + "\n"


async def create_agent_run_view(
    *,
    agent_id: str,
    query: str,
    config: dict,
    image_content: str | None,
    current_user_id: str,
    db: AsyncSession,
) -> dict:
    if not query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    if not agent_manager.get_agent(agent_id):
        raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

    thread_id = (config or {}).get("thread_id")
    if not thread_id:
        raise HTTPException(status_code=422, detail="config.thread_id 不能为空")

    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(current_user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    request_id = str((config or {}).get("request_id") or uuid.uuid4())
    run_repo = AgentRunRepository(db)
    existing = await run_repo.get_run_by_request_id(request_id)
    if existing and existing.user_id == str(current_user_id):
        return _build_run_response(existing)
    if existing and existing.user_id != str(current_user_id):
        raise HTTPException(status_code=409, detail="request_id 冲突")

    run_id = str(uuid.uuid4())
    input_payload = {
        "query": query,
        "config": config or {},
        "image_content": image_content,
        "agent_id": agent_id,
        "thread_id": thread_id,
        "user_id": str(current_user_id),
        "request_id": request_id,
        "created_at": utc_now_naive().isoformat(),
    }
    try:
        run = await run_repo.create_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            user_id=str(current_user_id),
            request_id=request_id,
            input_payload=input_payload,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await run_repo.get_run_by_request_id(request_id)
        if existing and existing.user_id == str(current_user_id):
            return _build_run_response(existing)
        raise HTTPException(status_code=409, detail="request_id 冲突")

    queue = await get_arq_pool()
    await queue.enqueue_job("process_agent_run", run.id, _job_id=f"run:{run.id}")

    return _build_run_response(run)


async def get_agent_run_view(*, run_id: str, current_user_id: str, db: AsyncSession) -> dict:
    repo = AgentRunRepository(db)
    run = await repo.get_run_for_user(run_id, str(current_user_id))
    if not run:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    return {"run": run.to_dict()}


async def cancel_agent_run_view(*, run_id: str, current_user_id: str, db: AsyncSession) -> dict:
    repo = AgentRunRepository(db)
    run = await repo.get_run_for_user(run_id, str(current_user_id))
    if not run:
        raise HTTPException(status_code=404, detail="运行任务不存在")

    run = await repo.request_cancel(run_id)
    await publish_cancel_signal(run_id)
    return {"run": run.to_dict() if run else None}


async def stream_agent_run_events(
    *,
    run_id: str,
    after_seq: str | int,
    current_user_id: str,
) -> AsyncIterator[str]:
    started_at = utc_now_naive()
    last_heartbeat_ts = started_at

    last_seq = normalize_after_seq(after_seq)

    try:
        while True:
            try:
                async with pg_manager.get_async_session_context() as db:
                    repo = AgentRunRepository(db)
                    run = await repo.get_run_for_user(run_id, str(current_user_id))
                    if not run:
                        yield _format_sse({"run_id": run_id, "message": "运行任务不存在"}, event="error")
                        yield _format_sse({"run_id": run_id, "last_seq": last_seq}, event="close")
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
                yield _format_sse({"run_id": run_id, "last_seq": last_seq}, event="close")
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
                yield _format_sse({"run_id": run_id, "last_seq": last_seq}, event="close")
                return

            for event in events:
                seq = str(event.get("seq") or "0-0")
                last_seq = seq

                yield _format_sse(
                    {
                        "run_id": run_id,
                        "seq": seq,
                        "event_type": event.get("event_type") or "message",
                        "payload": event.get("payload") or {},
                        "ts": event.get("ts"),
                    },
                    event=event.get("event_type") or "message",
                )

            if run.status in TERMINAL_RUN_STATUSES and not events:
                terminal_seq = last_seq
                if terminal_seq in {"", "0", "0-0"}:
                    terminal_seq = await get_last_run_stream_seq(run_id)

                yield _format_sse(
                    {"run_id": run_id, "status": run.status, "last_seq": terminal_seq},
                    event="close",
                )
                return

            now = utc_now_naive()
            elapsed_seconds = (now - started_at).total_seconds()
            heartbeat_elapsed = (now - last_heartbeat_ts).total_seconds()
            if heartbeat_elapsed >= SSE_HEARTBEAT_SECONDS:
                yield _format_sse({"run_id": run_id, "last_seq": last_seq}, event="heartbeat")
                last_heartbeat_ts = now

            if elapsed_seconds >= SSE_MAX_CONNECTION_MINUTES * 60:
                yield _format_sse({"run_id": run_id, "last_seq": last_seq}, event="close")
                return

            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        return


async def get_active_run_by_thread(*, thread_id: str, current_user_id: str, db: AsyncSession) -> dict:
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentRun

    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.thread_id == thread_id,
            AgentRun.user_id == str(current_user_id),
            AgentRun.status.notin_(list(TERMINAL_RUN_STATUSES)),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    return {"run": run.to_dict() if run else None}
