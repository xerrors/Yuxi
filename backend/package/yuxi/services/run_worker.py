"""ARQ worker for agent runs."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db
from yuxi.agents.skills.service import init_builtin_skills
from yuxi.config import config as sys_config
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.services.agent_request_queue_service import (
    RUN_STATUS_TO_DELIVERY_STATUS,
    dispatch_next_request,
    recover_pending_dispatches,
)
from yuxi.services.chat_service import stream_agent_chat, stream_agent_resume
from yuxi.services.input_message_service import restore_chat_input_message
from yuxi.services.run_queue_service import (
    append_run_stream_event,
    clear_cancel_signal,
    has_cancel_signal,
    wait_for_cancel_signal,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message, User
from yuxi.storage.redis import get_arq_redis_settings
from yuxi.utils.logging_config import logger
from yuxi.utils.thread_utils import extract_thread_id

LOADING_FLUSH_INTERVAL_MS = 100
LOADING_FLUSH_MAX_CHARS = 512
RUN_CANCEL_POLL_SECONDS = 0.2
SUPPORTED_RUN_TYPES = {"chat", "resume", "subagent"}


class RetryableRunError(Exception):
    """Error type that should trigger ARQ retry."""


class NonRetryableRunError(Exception):
    """Error type that should not trigger ARQ retry."""


@dataclass(frozen=True)
class TerminalTransition:
    status: str | None
    changed: bool


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
        target_thread_id = self._target_thread_id(thread_id or extract_thread_id(chunk))
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
        run, changed = await repo.set_terminal_status(
            run_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
        )
        persisted_status = run.status if run else None
        delivery_status = RUN_STATUS_TO_DELIVERY_STATUS.get(persisted_status or "")
        if changed and run and run.input_message_id and delivery_status:
            await db.execute(
                update(Message).where(Message.id == run.input_message_id).values(delivery_status=delivery_status)
            )
        return TerminalTransition(status=persisted_status, changed=changed)


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
    return extract_thread_id(chunk, fallback)


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


async def _finish_run(
    run_id: str,
    status: str,
    *,
    thread_id: str | None,
    chunk: dict,
    error_type: str | None = None,
    error_message: str | None = None,
) -> TerminalTransition:
    transition = await mark_run_terminal(
        run_id,
        status,
        error_type=error_type,
        error_message=error_message,
    )
    if transition.changed and transition.status:
        await _append_end_event(run_id, transition.status, thread_id=thread_id, payload={"chunk": chunk})
    return transition


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
    """执行队列中的 AgentRun，并只从 run 列和输入消息恢复运行参数。"""
    run = await _get_run(run_id)
    if not run:
        logger.warning(f"Run not found: {run_id}")
        return

    if run.status in TERMINAL_RUN_STATUSES:
        if run.status == "completed":
            await dispatch_next_request(
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
            )
        logger.info(f"Run already terminal, skip: {run_id}, status={run.status}")
        return

    if not isinstance(run.input_payload, dict):
        await mark_run_terminal(run_id, "failed", "invalid_input_payload", "run input_payload 必须是对象")
        return
    payload = run.input_payload
    runtime = payload.get("runtime") or {}
    if not isinstance(runtime, dict):
        await mark_run_terminal(run_id, "failed", "invalid_runtime_payload", "run input_payload.runtime 必须是对象")
        return

    input_message = await _load_input_message(run.input_message_id)
    if not input_message:
        await mark_run_terminal(run_id, "failed", "input_message_not_found", "运行任务缺少输入消息")
        return
    if not isinstance(input_message.extra_metadata, dict):
        await mark_run_terminal(run_id, "failed", "invalid_input_metadata", "输入消息 metadata 必须是对象")
        return

    run_type = run.run_type
    agent_slug = run.agent_slug
    uid = run.uid
    request_id = run.request_id
    thread_id = run.conversation_thread_id
    input_metadata = input_message.extra_metadata
    image_content = input_message.image_content

    if run_type not in SUPPORTED_RUN_TYPES:
        await mark_run_terminal(run_id, "failed", "invalid_run_type", f"不支持的 run_type: {run_type}")
        return

    user = await _load_user(uid)
    if not user:
        await mark_run_terminal(run_id, "failed", "user_not_found", f"user {uid} not found")
        return

    resume_input = None
    if run_type == "resume":
        resume_input = input_metadata.get("resume")
        if resume_input is None:
            await mark_run_terminal(run_id, "failed", "resume_input_not_found", "resume run 缺少 resume 输入")
            return
    else:
        try:
            normalized_input_message = restore_chat_input_message(
                content=input_message.content,
                image_content=image_content,
                metadata=input_metadata,
            )
        except ValueError as exc:
            await mark_run_terminal(run_id, "failed", "invalid_input_message", str(exc))
            return

    meta = {
        "run_id": run_id,
        "request_id": request_id,
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "uid": user.uid,
        "has_image": bool(image_content),
        "attachment_file_ids": input_metadata.get("attachment_file_ids") or [],
        "model_spec": payload.get("model_spec"),
        "tool_approval_mode": payload.get("tool_approval_mode"),
        "run_type": run_type,
        "created_by_run_id": run.created_by_run_id,
    }
    if run_type == "subagent":
        # 三个线程 ID 在 subagent_run_service 创建 run 时已写入 runtime，此处不再二次兜底；
        # 缺失会在 chat_service._apply_subagent_runtime_context 处直接报错。
        meta["parent_thread_id"] = runtime.get("parent_thread_id")
        meta["file_thread_id"] = runtime.get("file_thread_id")
        meta["skills_thread_id"] = runtime.get("skills_thread_id")
    if input_metadata.get("source"):
        meta["source"] = input_metadata.get("source")
    if isinstance(input_metadata.get("agent_invocation_meta"), dict):
        meta["agent_invocation_meta"] = input_metadata.get("agent_invocation_meta") or {}

    await mark_run_running(run_id)
    run_ctx = RunContext(run_id=run_id)
    writer = ChunkedEventWriter(
        run_id=run_id,
        thread_id=thread_id,
        interval_ms=LOADING_FLUSH_INTERVAL_MS,
        max_chars=LOADING_FLUSH_MAX_CHARS,
    )
    await run_ctx.start()
    metadata_event = {
        "request_id": request_id,
        "agent_slug": agent_slug,
        "uid": uid,
        "source": input_metadata.get("source"),
        "run_type": run_type,
        "created_by_run_id": run.created_by_run_id,
        "subagent_slug": agent_slug if run_type == "subagent" else None,
    }
    if isinstance(input_metadata.get("agent_invocation_meta"), dict):
        metadata_event["agent_invocation_meta"] = input_metadata.get("agent_invocation_meta") or {}

    await append_run_event(
        run_id,
        "metadata",
        metadata_event,
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
            elif run_type in {"chat", "subagent"}:
                stream = stream_agent_chat(
                    agent_slug=agent_slug,
                    thread_id=thread_id,
                    meta=meta,
                    input_message=normalized_input_message,
                    current_user=user,
                    db=db,
                    save_user_message=False,
                )
            else:
                raise RuntimeError(f"unsupported run_type after validation: {run_type}")

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

                    if await run_ctx.is_cancelled():
                        raise asyncio.CancelledError(f"run {run_id} cancelled")

                    if target_thread_id != thread_id:
                        continue

                    if status == "finished":
                        transition = await _finish_run(
                            run_id,
                            "completed",
                            thread_id=thread_id,
                            chunk=chunk,
                        )
                        terminal_set = transition.status is not None
                    elif status == "error":
                        transition = await _finish_run(
                            run_id,
                            "failed",
                            thread_id=thread_id,
                            chunk=chunk,
                            error_type=chunk.get("error_type") or "stream_error",
                            error_message=chunk.get("error_message") or chunk.get("message"),
                        )
                        terminal_set = transition.status is not None
                    elif status == "interrupted":
                        status_value = "cancelled" if await _is_cancel_requested(run_id) else "interrupted"
                        transition = await _finish_run(
                            run_id,
                            status_value,
                            thread_id=thread_id,
                            chunk=chunk,
                            error_type=status_value,
                            error_message=chunk.get("message"),
                        )
                        terminal_set = transition.status is not None
                    elif status in {"ask_user_question_required", "human_approval_required"}:
                        questions = chunk.get("questions") if isinstance(chunk, dict) else None
                        first_question = ""
                        if isinstance(questions, list) and questions:
                            first = questions[0]
                            if isinstance(first, dict):
                                first_question = str(first.get("question") or "").strip()

                        transition = await _finish_run(
                            run_id,
                            "interrupted",
                            thread_id=thread_id,
                            chunk=chunk,
                            error_type=status,
                            error_message=(
                                "需要用户审批工具操作"
                                if status == "human_approval_required"
                                else first_question or "需要用户回答问题"
                            ),
                        )
                        terminal_set = transition.status is not None

        await writer.flush()
        if not terminal_set:
            if await run_ctx.is_cancelled():
                raise asyncio.CancelledError(f"run {run_id} cancelled")
            finished_chunk = {"status": "finished", "request_id": request_id}
            await _finish_run(
                run_id,
                "completed",
                thread_id=thread_id,
                chunk=finished_chunk,
            )

    except asyncio.CancelledError:
        await writer.flush()
        cancel_chunk = {"status": "interrupted", "message": "对话已取消", "request_id": request_id}
        transition = await mark_run_terminal(
            run_id,
            "cancelled",
            error_type="cancelled",
            error_message="对话已取消",
        )
        if transition.changed:
            await append_run_event(
                run_id,
                "interrupt",
                {"reason": "cancelled", "chunk": cancel_chunk},
                thread_id=thread_id,
            )
            await _append_end_event(run_id, "cancelled", thread_id=thread_id, payload={"chunk": cancel_chunk})
            logger.info(f"Run cancelled: {run_id}")
        else:
            logger.info(f"Run cancellation ignored after terminal status: {run_id}, status={transition.status}")
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
                await _finish_run(
                    run_id,
                    "failed",
                    thread_id=thread_id,
                    chunk=retryable_error_chunk,
                    error_type="retryable_worker_error",
                    error_message=str(e),
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
        await _finish_run(
            run_id,
            "failed",
            thread_id=thread_id,
            chunk=error_chunk,
            error_type="worker_error",
            error_message=str(e),
        )
        return
    finally:
        await run_ctx.close()
        await clear_cancel_signal(run_id)
        # completed 后尝试派发线程的下一个排队请求
        final_run = await _get_run(run_id)
        if final_run and final_run.status == "completed":
            await dispatch_next_request(
                uid=uid,
                agent_slug=agent_slug,
                thread_id=thread_id,
            )


async def _load_input_message(message_id: int | None) -> Message | None:
    """加载 run 绑定的输入消息；worker 从这里恢复 query、resume、图片和请求元数据。"""
    if not message_id:
        return None
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(Message).where(Message.id == message_id))
        return result.scalar_one_or_none()


async def _worker_startup(ctx):
    """初始化 worker 依赖。"""

    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    await ensure_builtin_mcp_servers_in_db()
    async with pg_manager.get_async_session_context() as session:
        await init_builtin_skills(session)
        from yuxi.config.options import ensure_options_in_db

        await ensure_options_in_db(session)
    sys_config.start_runtime_sync()
    await recover_pending_dispatches()


async def _worker_shutdown(ctx):
    """关闭 worker 数据库连接。"""

    del ctx
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
        redis_settings = get_arq_redis_settings()
    except Exception:
        redis_settings = None
