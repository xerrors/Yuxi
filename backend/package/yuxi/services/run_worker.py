"""ARQ worker for agent runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.services.chat_service import stream_agent_chat
from yuxi.services.mcp.server_service import ensure_builtin_mcp_servers_in_db
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


class ChunkedEventWriter:
    def __init__(self, run_id: str, interval_ms: int = 100, max_chars: int = 512):
        self.run_id = run_id
        self.interval_seconds = interval_ms / 1000
        self.max_chars = max_chars
        self.buffer: list[dict] = []
        self.buffer_chars = 0
        self.last_flush = time.monotonic()

    async def append(self, chunk: dict):
        self.buffer.append(chunk)
        content = chunk.get("response") or ""
        self.buffer_chars += len(content) if isinstance(content, str) else 0

        now = time.monotonic()
        if (now - self.last_flush) >= self.interval_seconds or self.buffer_chars >= self.max_chars:
            await self.flush()

    async def flush(self):
        if not self.buffer:
            return
        await append_run_event(self.run_id, "loading", {"items": self.buffer})
        self.buffer = []
        self.buffer_chars = 0
        self.last_flush = time.monotonic()


async def _get_run(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        return await repo.get_run(run_id)


async def append_run_event(run_id: str, event_type: str, payload: dict):
    await append_run_stream_event(run_id, event_type, payload)


async def mark_run_running(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        await repo.mark_running(run_id)


async def mark_run_terminal(run_id: str, status: str, error_type: str | None = None, error_message: str | None = None):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        await repo.set_terminal_status(run_id, status=status, error_type=error_type, error_message=error_message)


async def _load_user(user_id: str):
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(User).where(User.id == int(user_id)))
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
    config = payload.get("config") or {}
    agent_id = payload.get("agent_id")
    image_content = payload.get("image_content")
    user_id = payload.get("user_id")
    request_id = payload.get("request_id")

    user = await _load_user(user_id)
    if not user:
        await mark_run_terminal(run_id, "failed", "user_not_found", f"user {user_id} not found")
        return

    if not request_id:
        request_id = run.request_id

    meta = {
        "request_id": request_id,
        "query": query,
        "agent_id": agent_id,
        "server_model_name": config.get("model", agent_id),
        "thread_id": config.get("thread_id"),
        "user_id": user.id,
        "has_image": bool(image_content),
    }

    await mark_run_running(run_id)
    run_ctx = RunContext(run_id=run_id)
    writer = ChunkedEventWriter(
        run_id=run_id,
        interval_ms=LOADING_FLUSH_INTERVAL_MS,
        max_chars=LOADING_FLUSH_MAX_CHARS,
    )
    await run_ctx.start()
    terminal_set = False

    try:
        async with pg_manager.get_async_session_context() as db:
            stream = stream_agent_chat(
                query=query,
                agent_config_id=config.get("agent_config_id"),
                thread_id=config.get("thread_id"),
                meta=meta,
                image_content=image_content,
                current_user=user,
                db=db,
            )

            async for chunk_bytes in _consume_stream_with_cancel(stream, run_ctx):
                for chunk in _iter_json_chunks(chunk_bytes):
                    if chunk.get("status") == "loading":
                        await writer.append(chunk)
                        continue

                    await writer.flush()
                    status = chunk.get("status") or "event"
                    await append_run_event(run_id, status, {"chunk": chunk})

                    if status == "finished":
                        await mark_run_terminal(run_id, "completed")
                        terminal_set = True
                    elif status == "error":
                        await mark_run_terminal(
                            run_id,
                            "failed",
                            error_type=chunk.get("error_type") or "stream_error",
                            error_message=chunk.get("error_message") or chunk.get("message"),
                        )
                        terminal_set = True
                    elif status == "interrupted":
                        status_value = "cancelled" if await _is_cancel_requested(run_id) else "interrupted"
                        await mark_run_terminal(
                            run_id,
                            status_value,
                            error_type=status_value,
                            error_message=chunk.get("message"),
                        )
                        terminal_set = True
                    elif status == "ask_user_question_required":
                        questions = chunk.get("questions") if isinstance(chunk, dict) else None
                        first_question = ""
                        if isinstance(questions, list) and questions:
                            first = questions[0]
                            if isinstance(first, dict):
                                first_question = str(first.get("question") or "").strip()

                        await mark_run_terminal(
                            run_id,
                            "interrupted",
                            error_type="ask_user_question_required",
                            error_message=first_question or "需要用户回答问题",
                        )
                        terminal_set = True

                    if await run_ctx.is_cancelled():
                        raise asyncio.CancelledError(f"run {run_id} cancelled")

        await writer.flush()
        if not terminal_set:
            await mark_run_terminal(run_id, "completed")
            await append_run_event(run_id, "finished", {"chunk": {"status": "finished", "request_id": request_id}})

    except asyncio.CancelledError:
        await writer.flush()
        await append_run_event(
            run_id,
            "interrupted",
            {"chunk": {"status": "interrupted", "message": "对话已取消", "request_id": request_id}},
        )
        await mark_run_terminal(run_id, "cancelled", error_type="cancelled", error_message="对话已取消")
        logger.info(f"Run cancelled: {run_id}")
    except Exception as e:
        await writer.flush()
        if _is_retryable_exception(e):
            job_try = _job_try(ctx)
            logger.warning(f"Run retryable failure {run_id} (try={job_try}): {e}")
            await append_run_event(
                run_id,
                "error",
                {
                    "chunk": {
                        "status": "error",
                        "error_type": "retryable_worker_error",
                        "error_message": str(e),
                        "request_id": request_id,
                        "retryable": True,
                        "job_try": job_try,
                    }
                },
            )
            if _is_last_try(ctx):
                await mark_run_terminal(
                    run_id,
                    "failed",
                    error_type="retryable_worker_error",
                    error_message=str(e),
                )
                logger.error(f"Run failed after retries exhausted {run_id}: {e}")
                return

            if isinstance(e, RetryableRunError):
                raise
            raise RetryableRunError(str(e)) from e

        logger.error(f"Run failed {run_id}: {e}")
        await append_run_event(
            run_id,
            "error",
            {
                "chunk": {
                    "status": "error",
                    "error_type": "worker_error",
                    "error_message": str(e),
                    "request_id": request_id,
                    "retryable": False,
                }
            },
        )
        await mark_run_terminal(run_id, "failed", error_type="worker_error", error_message=str(e))
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


async def _worker_shutdown(ctx):
    await pg_manager.close()


class WorkerSettings:
    functions = [process_agent_run]
    max_tries = 2
    retry_jobs = True
    job_timeout = 900
    keep_result = 60
    on_startup = _worker_startup
    on_shutdown = _worker_shutdown
    try:
        from arq.connections import RedisSettings

        redis_settings = RedisSettings.from_dsn(REDIS_URL)
    except Exception:
        redis_settings = None
