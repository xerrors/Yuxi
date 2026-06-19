"""ARQ worker for agent runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db
from yuxi.agents.skills.service import init_builtin_skills
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.services.chat_service import stream_agent_chat, stream_agent_resume
from yuxi.services.run_queue_service import (
    append_run_stream_event,
    clear_cancel_signal,
    has_cancel_signal,
    wait_for_cancel_signal,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger

LOADING_FLUSH_INTERVAL_MS = 100
LOADING_FLUSH_MAX_CHARS = 512
RUN_CANCEL_POLL_SECONDS = 0.2
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


class RetryableRunError(Exception):
    """Error type that should trigger ARQ retry."""


class NonRetryableRunError(Exception):
    """Error type that should not trigger ARQ retry."""


@dataclass
class RunContext:
    run_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _watch_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._watch_task is None:
            self._watch_task = asyncio.create_task(self._watch_cancel_signal())

    async def close(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
            self._watch_task = None

    async def wait_cancelled(self) -> None:
        await self.cancel_event.wait()

    async def is_cancelled(self) -> bool:
        if self.cancel_event.is_set():
            return True
        if await has_cancel_signal(self.run_id):
            self.cancel_event.set()
            return True
        return False

    async def _watch_cancel_signal(self) -> None:
        while not self.cancel_event.is_set():
            cancelled = await wait_for_cancel_signal(
                self.run_id,
                poll_timeout_seconds=RUN_CANCEL_POLL_SECONDS,
            )
            if cancelled:
                self.cancel_event.set()
                return


_ALL_THREADS = object()


@dataclass
class _ThreadBuffer:
    items: list[dict] = field(default_factory=list)
    chars: int = 0
    last_flush: float = field(default_factory=time.monotonic)


class ChunkedEventWriter:
    def __init__(self, run_id: str, thread_id: str | None, interval_ms: int = 100, max_chars: int = 512):
        self.run_id = run_id
        self.thread_id = thread_id
        self.interval_seconds = interval_ms / 1000
        self.max_chars = max_chars
        self.thread_buffers: dict[str | None, _ThreadBuffer] = {}

    def _target_thread_id(self, thread_id: str | None = None) -> str | None:
        return thread_id or self.thread_id

    async def append(self, chunk: dict, *, thread_id: str | None = None):
        target_thread_id = self._target_thread_id(thread_id or _thread_id_from_mapping(chunk))
        buffer = self.thread_buffers.setdefault(target_thread_id, _ThreadBuffer())
        buffer.items.append(chunk)
        buffer.chars += _loading_chunk_size(chunk)

        if _flush_loading_chunk_immediately(chunk):
            await self.flush(target_thread_id)
            return

        if (time.monotonic() - buffer.last_flush) >= self.interval_seconds or buffer.chars >= self.max_chars:
            await self.flush(target_thread_id)

    async def flush(self, thread_id: str | None | object = _ALL_THREADS):
        if thread_id is _ALL_THREADS:
            for target_thread_id in list(self.thread_buffers):
                await self.flush(target_thread_id)
            return

        buffer = self.thread_buffers.get(thread_id)
        if not buffer or not buffer.items:
            return
        await append_run_event(self.run_id, "messages", {"items": buffer.items}, thread_id=thread_id)
        buffer.items = []
        buffer.chars = 0
        buffer.last_flush = time.monotonic()


async def _get_run(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        return await repo.get_run(run_id)


async def append_run_event(run_id: str, event_type: str, payload: dict, *, thread_id: str | None = None):
    await append_run_stream_event(run_id, event_type, payload, thread_id=thread_id)


async def mark_run_running(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        await repo.mark_running(run_id)


async def mark_run_terminal(run_id: str, status: str, error_type: str | None = None, error_message: str | None = None):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        await repo.set_terminal_status(run_id, status=status, error_type=error_type, error_message=error_message)


async def _load_user(uid: str):
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
        return result.scalar_one_or_none()


async def _is_cancel_requested(run_id: str) -> bool:
    run = await _get_run(run_id)
    return bool(run and run.status == "cancel_requested")


def _job_try(ctx) -> int:
    if isinstance(ctx, dict):
        try:
            return int(ctx.get("job_try") or 1)
        except Exception:
            return 1
    return 1


def _is_last_try(ctx) -> bool:
    return _job_try(ctx) >= max(1, int(getattr(WorkerSettings, "max_tries", 1)))


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, NonRetryableRunError):
        return False
    return isinstance(exc, (RetryableRunError, OperationalError, ConnectionError, TimeoutError, asyncio.TimeoutError))


def _iter_json_chunks(chunk_bytes: bytes) -> list[dict]:
    text = chunk_bytes.decode("utf-8")
    chunks: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except Exception:
            logger.warning(f"Failed to parse run stream chunk: {line[:200]}")
    return chunks


def _thread_id_from_mapping(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    thread_id = value.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()
    for key in ("meta", "metadata", "configurable", "stream_event"):
        nested = value.get(key)
        if isinstance(nested, dict):
            nested_thread_id = _thread_id_from_mapping(nested)
            if nested_thread_id:
                return nested_thread_id
    return None


def _loading_chunk_size(chunk: dict) -> int:
    response = chunk.get("response")
    total = len(response) if isinstance(response, str) else 0
    stream_event = chunk.get("stream_event")
    if not isinstance(stream_event, dict):
        return total

    for key in ("content", "reasoning_content", "additional_reasoning_content", "args_delta"):
        value = stream_event.get(key)
        if isinstance(value, str):
            total += len(value)
    return total


def _flush_loading_chunk_immediately(chunk: dict) -> bool:
    stream_event = chunk.get("stream_event")
    return isinstance(stream_event, dict) and stream_event.get("type") == "tool_call"


def _chunk_thread_id(chunk: dict, fallback: str | None) -> str | None:
    return _thread_id_from_mapping(chunk) or fallback


def _map_chunk_to_run_event(chunk: dict) -> tuple[str, dict]:
    status = chunk.get("status") or "event"
    if status == "loading":
        return "messages", {"chunk": chunk}
    if status == "agent_state":
        return "custom", {"name": "yuxi.agent_state", "chunk": chunk, "agent_state": chunk.get("agent_state") or {}}
    if status in {"ask_user_question_required", "human_approval_required", "interrupted"}:
        reason = "human_approval" if status == "human_approval_required" else status
        return "interrupt", {"reason": reason, "chunk": chunk}
    if status == "warning":
        return "custom", {"name": "yuxi.warning", "chunk": chunk}
    if status == "error":
        return "error", {"chunk": chunk, "retryable": bool(chunk.get("retryable"))}
    if status == "finished":
        return "end", {"status": "completed", "chunk": chunk}
    return "custom", {"name": f"yuxi.{status}", "chunk": chunk}


async def _append_end_event(run_id: str, status: str, *, thread_id: str | None, payload: dict | None = None):
    end_payload = {"status": status}
    if payload:
        end_payload.update(payload)
    await append_run_event(run_id, "end", end_payload, thread_id=thread_id)


async def _consume_stream_with_cancel(agen, run_ctx: RunContext):
    while True:
        next_task = asyncio.create_task(agen.__anext__())
        cancel_task = asyncio.create_task(run_ctx.wait_cancelled())
        done, _ = await asyncio.wait({next_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)

        if cancel_task in done:
            next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)
            raise asyncio.CancelledError(f"run {run_ctx.run_id} cancelled")

        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)
        try:
            yield next_task.result()
        except StopAsyncIteration:
            return


async def process_agent_run(ctx, run_id: str):
    run = await _get_run(run_id)
    if not run:
        logger.warning(f"Run not found: {run_id}")
        return

    if run.status in TERMINAL_RUN_STATUSES:
        logger.info(f"Run already terminal, skip: {run_id}, status={run.status}")
        return

    payload = run.input_payload or {}
    query = payload.get("query")
    resume_input = payload.get("resume")
    run_type = payload.get("run_type") or "chat"
    config = payload.get("config") or {}
    agent_id = payload.get("agent_id")
    image_content = payload.get("image_content")
    uid = payload.get("uid")
    request_id = payload.get("request_id")
    thread_id = config.get("thread_id") or payload.get("thread_id")

    user = await _load_user(uid)
    if not user:
        await mark_run_terminal(run_id, "failed", "user_not_found", f"user {uid} not found")
        return

    if not request_id:
        request_id = run.request_id

    meta = {
        "run_id": run_id,
        "request_id": request_id,
        "query": query,
        "agent_id": agent_id,
        "server_model_name": config.get("model", agent_id),
        "thread_id": config.get("thread_id"),
        "uid": user.uid,
        "has_image": bool(image_content),
        "attachment_file_ids": payload.get("attachment_file_ids") or [],
        "model_spec": payload.get("model_spec"),
    }
    if payload.get("source"):
        meta["source"] = payload.get("source")
    if isinstance(payload.get("evaluation"), dict):
        meta["evaluation"] = payload.get("evaluation") or {}

    await mark_run_running(run_id)
    run_ctx = RunContext(run_id=run_id)
    writer = ChunkedEventWriter(
        run_id=run_id,
        thread_id=thread_id,
        interval_ms=LOADING_FLUSH_INTERVAL_MS,
        max_chars=LOADING_FLUSH_MAX_CHARS,
    )
    await run_ctx.start()
    await append_run_event(
        run_id,
        "metadata",
        {
            "request_id": request_id,
            "agent_id": agent_id,
            "backend_id": payload.get("backend_id"),
            "uid": uid,
            "source": payload.get("source"),
            "evaluation": payload.get("evaluation") or {},
        },
        thread_id=thread_id,
    )
    terminal_set = False

    try:
        async with pg_manager.get_async_session_context() as db:
            if run_type == "resume":
                stream = stream_agent_resume(
                    thread_id=thread_id,
                    resume_input=resume_input,
                    meta=meta,
                    current_user=user,
                    db=db,
                )
            else:
                stream = stream_agent_chat(
                    query=query,
                    agent_id=config.get("agent_id") or agent_id,
                    thread_id=thread_id,
                    meta=meta,
                    image_content=image_content,
                    current_user=user,
                    db=db,
                    save_user_message=False,
                )

            async for chunk_bytes in _consume_stream_with_cancel(stream, run_ctx):
                for chunk in _iter_json_chunks(chunk_bytes):
                    target_thread_id = _chunk_thread_id(chunk, thread_id)
                    if chunk.get("status") == "loading":
                        await writer.append(chunk, thread_id=target_thread_id)
                        continue

                    await writer.flush(target_thread_id)
                    status = chunk.get("status") or "event"
                    event_type, event_payload = _map_chunk_to_run_event(chunk)
                    if event_type != "end":
                        await append_run_event(run_id, event_type, event_payload, thread_id=target_thread_id)

                    if target_thread_id != thread_id:
                        if await run_ctx.is_cancelled():
                            raise asyncio.CancelledError(f"run {run_id} cancelled")
                        continue

                    if status == "finished":
                        await mark_run_terminal(run_id, "completed")
                        await _append_end_event(run_id, "completed", thread_id=thread_id, payload={"chunk": chunk})
                        terminal_set = True
                    elif status == "error":
                        await mark_run_terminal(
                            run_id,
                            "failed",
                            error_type=chunk.get("error_type") or "stream_error",
                            error_message=chunk.get("error_message") or chunk.get("message"),
                        )
                        await _append_end_event(run_id, "failed", thread_id=thread_id, payload={"chunk": chunk})
                        terminal_set = True
                    elif status == "interrupted":
                        status_value = "cancelled" if await _is_cancel_requested(run_id) else "interrupted"
                        await mark_run_terminal(
                            run_id,
                            status_value,
                            error_type=status_value,
                            error_message=chunk.get("message"),
                        )
                        await _append_end_event(run_id, status_value, thread_id=thread_id, payload={"chunk": chunk})
                        terminal_set = True
                    elif status in {"ask_user_question_required", "human_approval_required"}:
                        questions = chunk.get("questions") if isinstance(chunk, dict) else None
                        first_question = ""
                        if isinstance(questions, list) and questions:
                            first = questions[0]
                            if isinstance(first, dict):
                                first_question = str(first.get("question") or "").strip()

                        await mark_run_terminal(
                            run_id,
                            "interrupted",
                            error_type=status,
                            error_message=first_question or "需要用户回答问题",
                        )
                        await _append_end_event(run_id, "interrupted", thread_id=thread_id, payload={"chunk": chunk})
                        terminal_set = True

                    if await run_ctx.is_cancelled():
                        raise asyncio.CancelledError(f"run {run_id} cancelled")

        await writer.flush()
        if not terminal_set:
            finished_chunk = {"status": "finished", "request_id": request_id}
            await mark_run_terminal(run_id, "completed")
            await _append_end_event(run_id, "completed", thread_id=thread_id, payload={"chunk": finished_chunk})

    except asyncio.CancelledError:
        await writer.flush()
        cancel_chunk = {"status": "interrupted", "message": "对话已取消", "request_id": request_id}
        await append_run_event(
            run_id,
            "interrupt",
            {"reason": "cancelled", "chunk": cancel_chunk},
            thread_id=thread_id,
        )
        await mark_run_terminal(run_id, "cancelled", error_type="cancelled", error_message="对话已取消")
        await _append_end_event(run_id, "cancelled", thread_id=thread_id, payload={"chunk": cancel_chunk})
        logger.info(f"Run cancelled: {run_id}")
    except Exception as e:
        await writer.flush()
        if _is_retryable_exception(e):
            job_try = _job_try(ctx)
            logger.warning(f"Run retryable failure {run_id} (try={job_try}): {e}")
            retryable_error_chunk = {
                "status": "error",
                "error_type": "retryable_worker_error",
                "error_message": str(e),
                "request_id": request_id,
                "retryable": True,
                "job_try": job_try,
            }
            await append_run_event(
                run_id,
                "error",
                {"chunk": retryable_error_chunk, "retryable": True},
                thread_id=thread_id,
            )
            if _is_last_try(ctx):
                await mark_run_terminal(
                    run_id,
                    "failed",
                    error_type="retryable_worker_error",
                    error_message=str(e),
                )
                await _append_end_event(
                    run_id,
                    "failed",
                    thread_id=thread_id,
                    payload={"chunk": retryable_error_chunk},
                )
                logger.error(f"Run failed after retries exhausted {run_id}: {e}")
                return

            if isinstance(e, RetryableRunError):
                raise
            raise RetryableRunError(str(e)) from e

        logger.error(f"Run failed {run_id}: {e}")
        error_chunk = {
            "status": "error",
            "error_type": "worker_error",
            "error_message": str(e),
            "request_id": request_id,
            "retryable": False,
        }
        await append_run_event(
            run_id,
            "error",
            {"chunk": error_chunk, "retryable": False},
            thread_id=thread_id,
        )
        await mark_run_terminal(run_id, "failed", error_type="worker_error", error_message=str(e))
        await _append_end_event(run_id, "failed", thread_id=thread_id, payload={"chunk": error_chunk})
        return
    finally:
        await run_ctx.close()
        await clear_cancel_signal(run_id)


async def _worker_startup(ctx):
    del ctx
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    await ensure_builtin_mcp_servers_in_db()
    async with pg_manager.get_async_session_context() as session:
        await init_builtin_skills(session)


async def _worker_shutdown(ctx):
    await pg_manager.close()


class WorkerSettings:
    functions = [process_agent_run]
    max_tries = 2
    retry_jobs = True
    job_timeout = 3600
    keep_result = 60
    on_startup = _worker_startup
    on_shutdown = _worker_shutdown
    try:
        from arq.connections import RedisSettings

        redis_settings = RedisSettings.from_dsn(REDIS_URL)
    except Exception:
        redis_settings = None
