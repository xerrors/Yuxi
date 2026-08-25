"""ARQ worker for agent runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from arq.worker import RetryJob, func
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from yuxi.agents.backends.paths import runtime_workdir_path
from yuxi.agents.backends.sandbox.provider import get_sandbox_provider
from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db
from yuxi.agents.skills.service import init_builtin_skills
from yuxi.config.runtime import lite_mode_enabled
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.services.agent_request_queue_service import (
    dispatch_next_request,
    recover_pending_dispatches,
)
from yuxi.services.agent_run_manifest_service import build_run_manifest_result, compute_manifest_fingerprint
from yuxi.services.chat_service import get_agent_state_view, stream_agent_chat, stream_agent_resume
from yuxi.services.input_message_service import restore_chat_input_message
from yuxi.services.run_queue_service import (
    RUN_RECONCILIATION_SECONDS,
    WORKER_HEALTH_INTERVAL_SECONDS,
    WORKER_HEALTH_KEY,
    WORKER_RECONCILIATION_HEALTH_KEY,
    WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    append_run_stream_event,
    clear_cancel_signal,
    get_redis_client,
    has_cancel_signal,
    publish_cancel_signal,
    wait_for_cancel_signal,
)
from yuxi.services.task_queue_service import (
    TASK_RECONCILIATION_HEALTH_KEY,
    TASK_RECONCILIATION_HEALTH_TTL_SECONDS,
    TASK_RECONCILIATION_SECONDS,
    reconcile_and_publish_tasks,
)
from yuxi.services.task_service import TASKER_MAX_TIMEOUT_SECONDS, process_task
from yuxi.services.workdir_service import (
    AuthorizedWorkdir,
    resolve_authorized_workdir,
    resolve_conversation_workdir_path,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message, User
from yuxi.storage.redis import get_arq_redis_settings
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.logging_config import logger
from yuxi.utils.thread_utils import extract_thread_id

LOADING_FLUSH_INTERVAL_MS = 100
LOADING_FLUSH_MAX_CHARS = 512
RUN_CANCEL_POLL_SECONDS = 0.2
RUN_DURABLE_CANCEL_POLL_SECONDS = 1.0
RUN_LEASE_SECONDS = 120
RUN_HEARTBEAT_SECONDS = 30
SUPPORTED_RUN_TYPES = {"chat", "resume", "subagent"}
WORKER_ID = f"worker-{uuid.uuid4().hex}"
_RECONCILIATION_TASK_KEY = "agent_run_reconciliation_task"
_TASK_RECONCILIATION_TASK_KEY = "durable_task_reconciliation_task"


class RetryableRunError(RetryJob):
    """Error type that should trigger ARQ retry."""


class RuntimeCleanupPendingError(RetryJob):
    """根 Run 已终态，但 execution runtime 的持久清理尚未完成。"""


class NonRetryableRunError(Exception):
    """Error type that should not trigger ARQ retry."""


async def _validate_run_workdir_binding(run: AgentRun) -> AuthorizedWorkdir:
    """在执行器边界验证持久 Run 的 Conversation、执行树与 Workdir 归属。"""
    async with pg_manager.get_async_session_context() as db:
        binding = await resolve_authorized_workdir(
            thread_id=str(run.conversation_thread_id),
            uid=str(run.uid),
            db=db,
        )
        if int(binding.conversation_id) != int(run.conversation_id):
            raise NonRetryableRunError("AgentRun 的 Conversation 身份不一致")

        persisted_scope = str(run.runtime_scope_id or "").strip()
        if not persisted_scope:
            raise NonRetryableRunError("AgentRun 缺少 runtime scope")
        if run.run_type in {"chat", "resume"} and persisted_scope != str(run.conversation_thread_id):
            raise NonRetryableRunError(f"{str(run.run_type).capitalize()} AgentRun 的 runtime scope 非法")

        if run.run_type == "subagent":
            creator_id = str(run.created_by_run_id or "").strip()
            if not creator_id:
                raise NonRetryableRunError("SubAgent Run 缺少创建者")
            repo = AgentRunRepository(db)
            execution_pair = await repo.get_subagent_run_with_creator(
                uid=str(run.uid),
                created_by_run_id=creator_id,
                run_id=str(run.id),
            )
            if execution_pair is None:
                raise NonRetryableRunError("SubAgent Run 的线程关系非法")
            creator_run, _persisted_run = execution_pair
            if creator_run.run_type not in {"chat", "resume"}:
                raise NonRetryableRunError("SubAgent Run 的创建者非法")
            creator_binding = await resolve_authorized_workdir(
                thread_id=str(creator_run.conversation_thread_id),
                uid=str(run.uid),
                db=db,
            )
            if (
                persisted_scope != str(creator_run.runtime_scope_id)
                or int(creator_binding.conversation_id) != int(creator_run.conversation_id)
                or creator_binding.project_id != binding.project_id
            ):
                raise NonRetryableRunError("SubAgent Run 的 runtime scope 不属于创建者执行树")
    return binding


@dataclass(frozen=True)
class TerminalTransition:
    status: str | None
    changed: bool


@dataclass
class RunContext:
    run_id: str
    worker_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _watch_task: asyncio.Task | None = None
    _durable_cancel_task: asyncio.Task | None = None
    _heartbeat_task: asyncio.Task | None = None
    lease_lost: bool = False

    async def start(self) -> None:
        if self._watch_task is None:
            self._watch_task = asyncio.create_task(self._watch_cancel_signal())
        if self._durable_cancel_task is None:
            self._durable_cancel_task = asyncio.create_task(self._watch_durable_cancel())
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_lease())

    async def close(self) -> None:
        tasks = [
            task for task in (self._watch_task, self._durable_cancel_task, self._heartbeat_task) if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._watch_task = None
        self._durable_cancel_task = None
        self._heartbeat_task = None

    async def wait_cancelled(self) -> None:
        await self.cancel_event.wait()

    async def is_cancelled(self) -> bool:
        if self.cancel_event.is_set():
            return True
        if await _is_cancel_requested(self.run_id):
            self.cancel_event.set()
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

    async def _watch_durable_cancel(self) -> None:
        """低频轮询 PostgreSQL，确保 Redis 丢信号时取消仍然 fail-closed。"""

        while not self.cancel_event.is_set():
            try:
                cancelled = await _is_cancel_requested(self.run_id)
            except Exception:
                logger.error(f"Failed to read durable AgentRun cancellation: run={self.run_id}", exc_info=True)
                self.cancel_event.set()
                return
            else:
                if cancelled:
                    self.cancel_event.set()
                    return
            try:
                await asyncio.wait_for(
                    self.cancel_event.wait(),
                    timeout=RUN_DURABLE_CANCEL_POLL_SECONDS,
                )
            except TimeoutError:
                continue

    async def _heartbeat_lease(self) -> None:
        while not self.cancel_event.is_set():
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            if self.cancel_event.is_set():
                return
            try:
                renewed = await renew_run_lease(self.run_id, self.worker_id)
            except Exception:
                logger.error(f"Failed to renew AgentRun lease: run={self.run_id}", exc_info=True)
                renewed = False
            if not renewed:
                self.lease_lost = True
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
        await _append_run_event_best_effort(
            self.run_id,
            "messages",
            {"items": buffer.items},
            thread_id=thread_id,
        )
        buffer.items = []
        buffer.chars = 0
        buffer.last_flush = time.monotonic()


async def _release_runtime_if_idle(run: AgentRun) -> bool:
    """在 PostgreSQL cleanup fence 内串行销毁根 execution runtime。"""
    if run.run_type == "subagent":
        return False
    runtime_scope_id = str(getattr(run, "runtime_scope_id", None) or run.conversation_thread_id)
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"yuxi-runtime-cleanup:{run.uid}:{runtime_scope_id}"},
        )
        current = await db.scalar(select(AgentRun).where(AgentRun.id == run.id).with_for_update())
        if current is None:
            raise RuntimeError(f"Run {run.id} 不存在，不能确认 runtime cleanup Owner")
        if not current.runtime_cleanup_pending:
            return True
        result = await db.execute(
            select(AgentRun.id)
            .where(
                AgentRun.runtime_scope_id == runtime_scope_id,
                AgentRun.id != current.id,
                AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
            )
            .limit(1)
        )
        if result.scalar_one_or_none() is not None:
            return False
        conversation = await db.scalar(select(Conversation).where(Conversation.id == current.conversation_id))
        if conversation is None or conversation.uid != str(current.uid):
            raise RuntimeError(f"Run {run.id} 的 Conversation 身份不一致")
        workdir_path = await resolve_conversation_workdir_path(
            conversation=conversation,
            uid=str(current.uid),
            db=db,
        )
        await asyncio.to_thread(
            get_sandbox_provider().release,
            runtime_scope_id,
            uid=str(current.uid),
            clear_cache_on_delete_failure=True,
            workdir_path=workdir_path,
        )
        current.runtime_cleanup_pending = False
        await db.flush()
    return True


async def _release_runtime_before_terminal_event(run: AgentRun | None) -> None:
    """在终态事件可见前收敛 runtime，避免客户端撞上随后发生的删除。"""
    if run is None or run.run_type == "subagent":
        return
    await _require_runtime_cleanup(run, f"Run {run.id} 的 execution tree 尚未完成 runtime cleanup")


async def _require_runtime_cleanup(run: AgentRun, message: str) -> None:
    """把 provisioner/并发清理失败统一转成 ARQ 可重试的 durable cleanup。"""
    try:
        cleaned = await _release_runtime_if_idle(run)
    except Exception as exc:
        raise RuntimeCleanupPendingError(message) from exc
    if not cleaned:
        raise RuntimeCleanupPendingError(message)


async def _finish_execution_tree_children(run: AgentRun) -> None:
    """收敛 execution tree 后代的数据库终态并通知其停止执行。"""
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        descendants = await repo.cancel_active_execution_tree_descendants(run)
        await db.commit()
    await _publish_execution_tree_cancel_signals(descendants)


async def _get_run(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        return await repo.get_run(run_id)


async def append_run_event(run_id: str, event_type: str, payload: dict, *, thread_id: str | None = None):
    await append_run_stream_event(run_id, event_type, payload, thread_id=thread_id)


async def _append_run_event_best_effort(
    run_id: str,
    event_type: str,
    payload: dict,
    *,
    thread_id: str | None = None,
) -> bool:
    """发布短期事件；失败只记录，不能阻断 PostgreSQL 状态收敛。"""

    try:
        await append_run_event(run_id, event_type, payload, thread_id=thread_id)
    except Exception:
        logger.warning(
            f"Failed to publish non-authoritative AgentRun event: run={run_id}, event={event_type}",
            exc_info=True,
        )
        return False
    return True


async def _flush_writer_best_effort(writer: ChunkedEventWriter) -> None:
    """尽力发布缓冲事件，不让 Redis 可用性决定 durable transition。"""

    try:
        await writer.flush()
    except Exception:
        logger.warning(f"Failed to flush non-authoritative AgentRun events: run={writer.run_id}", exc_info=True)


async def _clear_cancel_signal_best_effort(run_id: str) -> None:
    """取消键清理失败不能覆盖已经提交的 Run 终态。"""

    try:
        await clear_cancel_signal(run_id)
    except Exception:
        logger.warning(f"Failed to clear non-authoritative AgentRun cancel signal: run={run_id}", exc_info=True)


async def _publish_execution_tree_cancel_signals(cancelled: list[tuple[str, str]]) -> None:
    """尽力通知已由 PostgreSQL 收敛的后代 Run 停止执行。"""

    if not cancelled:
        return
    results = await asyncio.gather(
        *(publish_cancel_signal(run_id) for run_id, _thread_id in cancelled),
        return_exceptions=True,
    )
    for (run_id, _thread_id), result in zip(cancelled, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Failed to publish execution-tree cancel signal: run=%s", run_id, exc_info=result)


async def mark_run_running(run_id: str, worker_id: str) -> bool:
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        _, acquired = await repo.mark_running(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )
        return acquired


async def renew_run_lease(run_id: str, worker_id: str) -> bool:
    """在独立事务中续租；owner 或 lease 已失效时返回 False。"""
    async with pg_manager.get_async_session_context() as db:
        return await AgentRunRepository(db).renew_lease(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )


async def release_run_lease_for_retry(run_id: str, worker_id: str) -> bool:
    """释放当前 attempt 的 lease，允许下一次 ARQ attempt 使用新 token。"""
    cancelled_descendants: list[tuple[str, str]] = []
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        released = await repo.release_lease_for_retry(run_id, worker_id=worker_id)
        if released:
            run = await repo.get_run(run_id)
            if run is not None:
                cancelled_descendants = await repo.cancel_active_execution_tree_descendants(run)
    if cancelled_descendants:
        await asyncio.gather(
            *(publish_cancel_signal(child_id) for child_id, _thread_id in cancelled_descendants),
            return_exceptions=True,
        )
    return released


async def mark_run_terminal(
    run_id: str,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
    token_usage: dict | None = None,
    worker_id: str | None = None,
):
    cancelled_descendants: list[tuple[str, str]] = []
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        run, changed = await repo.set_terminal_status(
            run_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
            token_usage=token_usage,
            worker_id=worker_id,
        )
        if changed and run is not None:
            cancelled_descendants = await repo.cancel_active_execution_tree_descendants(run)
        persisted_status = run.status if run else None
    await _publish_execution_tree_cancel_signals(cancelled_descendants)
    return TerminalTransition(status=persisted_status, changed=changed)


async def reconcile_expired_run_leases(*, now: datetime | None = None) -> list[str]:
    """收敛过期 Run ownership；重复或并发执行只返回本次实际转换的 Run。"""
    async with pg_manager.get_async_session_context() as db:
        runs, cancelled_descendants = await AgentRunRepository(db).reconcile_expired_leases(now=now)
    if cancelled_descendants:
        await asyncio.gather(
            *(publish_cancel_signal(child_id) for child_id, _thread_id in cancelled_descendants),
            return_exceptions=True,
        )
    await reconcile_pending_runtime_cleanups()
    return [run.id for run in runs]


async def reconcile_pending_runtime_cleanups() -> list[str]:
    """重试 PostgreSQL 持久拥有的 runtime cleanup，并在成功后发布终态。"""
    async with pg_manager.get_async_session_context() as db:
        pending_runs = await AgentRunRepository(db).list_pending_runtime_cleanups()
    cleaned: list[str] = []
    for run in pending_runs:
        try:
            if not await _release_runtime_if_idle(run):
                continue
        except Exception:
            logger.error("Failed to reconcile execution-tree runtime cleanup: run=%s", run.id, exc_info=True)
            continue
        if run.status in TERMINAL_RUN_STATUSES:
            await _append_end_event(run.id, run.status, thread_id=run.conversation_thread_id)
        if run.status in {"pending", "completed"}:
            await dispatch_next_request(
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
            )
        cleaned.append(run.id)
    return cleaned


def _require_persisted_manifest_match(persisted_run: AgentRun | None, *, recorded: bool, fingerprint: str) -> None:
    """重试只能复用与 write-once manifest 完全一致的运行资产。"""
    if recorded:
        return
    if persisted_run is None or persisted_run.manifest_fingerprint != fingerprint:
        raise RuntimeError("运行资产已在重试前变化，与已固化 manifest 不一致")


async def persist_run_manifest(*, run: AgentRun, user, worker_id: str) -> dict:
    """在执行上下文构造前固化运行清单与指纹；固化失败由调用方显式失败。"""
    async with pg_manager.get_async_session_context() as db:
        result = await build_run_manifest_result(run=run, user=user, db=db)
        fingerprint = compute_manifest_fingerprint(result.manifest)
        persisted_run, recorded = await AgentRunRepository(db).record_run_manifest(
            run.id,
            manifest=result.manifest,
            fingerprint=fingerprint,
            worker_id=worker_id,
        )
        _require_persisted_manifest_match(persisted_run, recorded=recorded, fingerprint=fingerprint)
        return {
            "normalized_context": result.normalized_context,
            "skill_runtime_snapshot": result.skill_runtime_snapshot,
        }


async def _load_user(uid: str):
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
        return result.scalar_one_or_none()


async def _is_cancel_requested(run_id: str) -> bool:
    run = await _get_run(run_id)
    return bool(run and run.status == "cancel_requested")


async def _confirmed_user_cancel(run_id: str) -> bool:
    """只把 PostgreSQL 中的 cancel_requested 视为用户取消事实。"""

    try:
        return await _is_cancel_requested(run_id)
    except Exception:
        logger.error(f"Failed to confirm durable AgentRun cancellation: run={run_id}", exc_info=True)
        return False


async def _read_run_token_usage_from_state(*, run_id: str, thread_id: str, current_user) -> dict | None:
    """从当前线程 state 读取属于指定 Run 的用量快照。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            view = await get_agent_state_view(
                thread_id=thread_id,
                current_user=current_user,
                db=db,
                include_relations=False,
            )
    except Exception:
        logger.warning(f"Failed to read token usage from state for run {run_id}", exc_info=True)
        return None

    agent_state = view.get("agent_state") if isinstance(view, dict) else None
    token_usage = agent_state.get("token_usage") if isinstance(agent_state, dict) else None
    if not isinstance(token_usage, dict) or token_usage.get("current_run_id") != run_id:
        return None
    run_usage = token_usage.get("run")
    return dict(run_usage) if isinstance(run_usage, dict) else None


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


def _worker_identity(ctx) -> str:
    """返回当前 worker 进程在所有 job 中复用的 identity。"""
    if isinstance(ctx, dict):
        worker_id = ctx.get("worker_id")
        if isinstance(worker_id, str) and worker_id:
            return worker_id
    return WORKER_ID


def _run_owner_token(ctx) -> str:
    """为一次 job attempt 生成带稳定 worker identity 的唯一 owner token。"""
    return f"{_worker_identity(ctx)}:{uuid.uuid4().hex}"


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
    await _append_run_event_best_effort(run_id, "end", end_payload, thread_id=thread_id)


async def _finish_run(
    run_id: str,
    status: str,
    *,
    thread_id: str | None,
    chunk: dict,
    current_user,
    worker_id: str,
    error_type: str | None = None,
    error_message: str | None = None,
    publish_end: bool = True,
) -> TerminalTransition:
    run = await _get_run(run_id)
    token_usage = {"available": False}
    if thread_id:
        state_token_usage = await _read_run_token_usage_from_state(
            run_id=run_id,
            thread_id=thread_id,
            current_user=current_user,
        )
        if state_token_usage is not None:
            token_usage = state_token_usage
    transition = await mark_run_terminal(
        run_id,
        status,
        error_type=error_type,
        error_message=error_message,
        token_usage=token_usage,
        worker_id=worker_id,
    )
    if transition.status in TERMINAL_RUN_STATUSES:
        committed_run = await _get_run(run_id)
        await _release_runtime_before_terminal_event(committed_run or run)
    if publish_end and transition.changed and transition.status:
        await _append_end_event(run_id, transition.status, thread_id=thread_id, payload={"chunk": chunk})
    return transition


async def _finish_user_cancel(
    *,
    run_id: str,
    request_id: str,
    thread_id: str,
    current_user,
    worker_id: str,
    writer: ChunkedEventWriter,
    run: AgentRun,
) -> TerminalTransition:
    """在 PostgreSQL 已确认取消后，由当前 owner 写入 cancelled。"""

    await _flush_writer_best_effort(writer)
    cancel_chunk = {"status": "interrupted", "message": "对话已取消", "request_id": request_id}
    state_token_usage = None
    if current_user is not None:
        state_token_usage = await _read_run_token_usage_from_state(
            run_id=run_id,
            thread_id=thread_id,
            current_user=current_user,
        )
    transition = await mark_run_terminal(
        run_id,
        "cancelled",
        error_type="cancelled",
        error_message="对话已取消",
        token_usage=state_token_usage or {"available": False},
        worker_id=worker_id,
    )
    if run.run_type != "subagent":
        await _release_runtime_before_terminal_event(run)
    if transition.changed:
        await _append_run_event_best_effort(
            run_id,
            "interrupt",
            {"reason": "cancelled", "chunk": cancel_chunk},
            thread_id=thread_id,
        )
        await _append_end_event(run_id, "cancelled", thread_id=thread_id, payload={"chunk": cancel_chunk})
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
        await _finish_execution_tree_children(run)
        cleanup_was_pending = bool(getattr(run, "runtime_cleanup_pending", False))
        if cleanup_was_pending:
            await _require_runtime_cleanup(run, f"Run {run_id} 的 execution tree 尚未完成 runtime cleanup")
            await _append_end_event(run_id, run.status, thread_id=run.conversation_thread_id)
        if run.status == "completed":
            await dispatch_next_request(
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
            )
        logger.info(f"Run already terminal, skip: {run_id}, status={run.status}")
        return

    if bool(getattr(run, "runtime_cleanup_pending", False)):
        await _require_runtime_cleanup(run, f"Run {run_id} 尚未完成 retry runtime cleanup")
        run = await _get_run(run_id)
        if run is None:
            raise NonRetryableRunError(f"Run {run_id} 在 runtime cleanup 后不存在")

    worker_id = _run_owner_token(ctx)
    if not await mark_run_running(run_id, worker_id):
        logger.info(f"Run lease is owned elsewhere or expired, skip: {run_id}")
        return

    run_type = run.run_type
    agent_slug = run.agent_slug
    uid = run.uid
    request_id = run.request_id
    thread_id = run.conversation_thread_id
    user = None
    run_ctx = RunContext(run_id=run_id, worker_id=worker_id)
    writer = ChunkedEventWriter(
        run_id=run_id,
        thread_id=thread_id,
        interval_ms=LOADING_FLUSH_INTERVAL_MS,
        max_chars=LOADING_FLUSH_MAX_CHARS,
    )
    try:
        if await _is_cancel_requested(run_id):
            run_ctx.cancel_event.set()
            raise asyncio.CancelledError(f"run {run_id} cancelled before execution")

        if not isinstance(run.input_payload, dict):
            await mark_run_terminal(
                run_id,
                "failed",
                "invalid_input_payload",
                "run input_payload 必须是对象",
                worker_id=worker_id,
            )
            return
        payload = run.input_payload
        runtime = payload.get("runtime") or {}
        if not isinstance(runtime, dict):
            await mark_run_terminal(
                run_id,
                "failed",
                "invalid_runtime_payload",
                "run input_payload.runtime 必须是对象",
                worker_id=worker_id,
            )
            return

        input_message = await _load_input_message(run.input_message_id)
        if not input_message:
            await mark_run_terminal(
                run_id,
                "failed",
                "input_message_not_found",
                "运行任务缺少输入消息",
                worker_id=worker_id,
            )
            return
        if not isinstance(input_message.extra_metadata, dict):
            await mark_run_terminal(
                run_id,
                "failed",
                "invalid_input_metadata",
                "输入消息 metadata 必须是对象",
                worker_id=worker_id,
            )
            return

        input_metadata = input_message.extra_metadata
        image_content = input_message.image_content

        if run_type not in SUPPORTED_RUN_TYPES:
            await mark_run_terminal(
                run_id,
                "failed",
                "invalid_run_type",
                f"不支持的 run_type: {run_type}",
                worker_id=worker_id,
            )
            return

        user = await _load_user(uid)
        if not user:
            await mark_run_terminal(
                run_id,
                "failed",
                "user_not_found",
                f"user {uid} not found",
                worker_id=worker_id,
            )
            return

        try:
            workdir_binding = await _validate_run_workdir_binding(run)
        except Exception as exc:  # noqa: BLE001
            await mark_run_terminal(
                run_id,
                "failed",
                "invalid_runtime_scope",
                str(exc),
                worker_id=worker_id,
            )
            return

        resume_input = None
        if run_type == "resume":
            resume_input = input_metadata.get("resume")
            if resume_input is None:
                await mark_run_terminal(
                    run_id,
                    "failed",
                    "resume_input_not_found",
                    "resume run 缺少 resume 输入",
                    worker_id=worker_id,
                )
                return
        else:
            try:
                normalized_input_message = restore_chat_input_message(
                    content=input_message.content,
                    image_content=image_content,
                    metadata=input_metadata,
                )
            except ValueError as exc:
                await mark_run_terminal(
                    run_id,
                    "failed",
                    "invalid_input_message",
                    str(exc),
                    worker_id=worker_id,
                )
                return

        # 运行清单必须在真正构造执行上下文前固化；固化失败时执行不得开始。
        try:
            execution_snapshot = await persist_run_manifest(run=run, user=user, worker_id=worker_id)
        except Exception as manifest_error:
            logger.error(f"Failed to persist AgentRun manifest: run={run_id}", exc_info=True)
            await mark_run_terminal(
                run_id,
                "failed",
                error_type="manifest_persist_failed",
                error_message=f"运行清单固化失败，执行未开始：{manifest_error}",
                worker_id=worker_id,
            )
            return

        # 固化期间用户可能已取消；复查一次，把取消竞态窗口恢复到执行开始前的水平。
        if await _is_cancel_requested(run_id):
            raise asyncio.CancelledError(f"run {run_id} cancelled after manifest recorded")

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
            "worker_id": worker_id,
            "runtime_scope_id": str(getattr(run, "runtime_scope_id", None) or thread_id),
            "workdir_relative_path": workdir_binding.workdir_path,
            "workdir_path": runtime_workdir_path(workdir_binding.workdir_path),
        }
        if run_type == "subagent":
            meta["parent_thread_id"] = runtime.get("parent_thread_id")
        if input_metadata.get("source"):
            meta["source"] = input_metadata.get("source")
        if isinstance(input_metadata.get("agent_invocation_meta"), dict):
            meta["agent_invocation_meta"] = input_metadata.get("agent_invocation_meta") or {}

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

        await _append_run_event_best_effort(
            run_id,
            "metadata",
            metadata_event,
            thread_id=thread_id,
        )
        terminal_set = False
        pending_interrupt: tuple[dict, str | None] | None = None
        async with pg_manager.get_async_session_context() as db:
            if run_type == "resume":
                stream = stream_agent_resume(
                    thread_id=thread_id,
                    resume_input=resume_input,
                    meta=meta,
                    current_user=user,
                    db=db,
                    execution_snapshot=execution_snapshot,
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
                    execution_snapshot=execution_snapshot,
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
                    is_parent_approval = target_thread_id == thread_id and status in {
                        "ask_user_question_required",
                        "human_approval_required",
                    }
                    if is_parent_approval:
                        pending_interrupt = (chunk, target_thread_id)
                    elif event_type != "end" and not (
                        target_thread_id == thread_id and status in {"error", "interrupted"}
                    ):
                        await _append_run_event_best_effort(
                            run_id,
                            event_type,
                            event_payload,
                            thread_id=target_thread_id,
                        )

                    if await run_ctx.is_cancelled():
                        raise asyncio.CancelledError(f"run {run_id} cancelled")

                    if target_thread_id != thread_id:
                        continue

                    if status == "finished":
                        if chunk.get("terminal_committed") is True:
                            committed_run = await _get_run(run_id)
                            if committed_run is not None:
                                await _finish_execution_tree_children(committed_run)
                            await _release_runtime_before_terminal_event(committed_run)
                            await _append_end_event(
                                run_id,
                                "completed",
                                thread_id=thread_id,
                                payload={"chunk": chunk},
                            )
                            terminal_set = True
                        else:
                            transition = await _finish_run(
                                run_id,
                                "completed",
                                thread_id=thread_id,
                                chunk=chunk,
                                current_user=user,
                                worker_id=worker_id,
                            )
                            terminal_set = transition.status in TERMINAL_RUN_STATUSES
                    elif status == "error":
                        transition = await _finish_run(
                            run_id,
                            "failed",
                            thread_id=thread_id,
                            chunk=chunk,
                            error_type=chunk.get("error_type") or "stream_error",
                            error_message=chunk.get("error_message") or chunk.get("message"),
                            current_user=user,
                            worker_id=worker_id,
                            publish_end=False,
                        )
                        if transition.changed:
                            await _append_run_event_best_effort(
                                run_id,
                                event_type,
                                event_payload,
                                thread_id=target_thread_id,
                            )
                            await _append_end_event(
                                run_id,
                                transition.status or "failed",
                                thread_id=thread_id,
                                payload={"chunk": chunk},
                            )
                        terminal_set = transition.status in TERMINAL_RUN_STATUSES
                    elif status == "interrupted":
                        status_value = "cancelled" if await _is_cancel_requested(run_id) else "interrupted"
                        transition = await _finish_run(
                            run_id,
                            status_value,
                            thread_id=thread_id,
                            chunk=chunk,
                            error_type=status_value,
                            error_message=chunk.get("message"),
                            current_user=user,
                            worker_id=worker_id,
                            publish_end=False,
                        )
                        if transition.changed or transition.status == "interrupted":
                            await _append_run_event_best_effort(
                                run_id,
                                event_type,
                                event_payload,
                                thread_id=target_thread_id,
                            )
                            await _append_end_event(
                                run_id,
                                transition.status or status_value,
                                thread_id=thread_id,
                                payload={"chunk": chunk},
                            )
                        terminal_set = transition.status in TERMINAL_RUN_STATUSES

        await writer.flush()
        if pending_interrupt and not terminal_set:
            interrupt_chunk, interrupt_thread_id = pending_interrupt
            event_type, event_payload = _map_chunk_to_run_event(interrupt_chunk)

            questions = interrupt_chunk.get("questions")
            first_question = ""
            if isinstance(questions, list) and questions:
                first = questions[0]
                if isinstance(first, dict):
                    first_question = str(first.get("question") or "").strip()

            interrupt_status = interrupt_chunk.get("status")
            transition = await _finish_run(
                run_id,
                "interrupted",
                thread_id=thread_id,
                chunk=interrupt_chunk,
                error_type=interrupt_status,
                error_message=(
                    "需要用户审批工具操作"
                    if interrupt_status == "human_approval_required"
                    else first_question or "需要用户回答问题"
                ),
                current_user=user,
                worker_id=worker_id,
                publish_end=False,
            )
            if transition.changed or transition.status == "interrupted":
                await _append_run_event_best_effort(
                    run_id,
                    event_type,
                    event_payload,
                    thread_id=interrupt_thread_id,
                )
                await _append_end_event(
                    run_id,
                    transition.status or "interrupted",
                    thread_id=thread_id,
                    payload={"chunk": interrupt_chunk},
                )
            terminal_set = transition.status in TERMINAL_RUN_STATUSES

        if not terminal_set:
            if await run_ctx.is_cancelled():
                raise asyncio.CancelledError(f"run {run_id} cancelled")
            finished_chunk = {"status": "finished", "request_id": request_id}
            await _finish_run(
                run_id,
                "completed",
                thread_id=thread_id,
                chunk=finished_chunk,
                current_user=user,
                worker_id=worker_id,
            )

    except asyncio.CancelledError as cancellation:
        await _flush_writer_best_effort(writer)
        if run_ctx.lease_lost:
            logger.warning(f"Run stopped after losing its lease: {run_id}")
            return
        if await _confirmed_user_cancel(run_id):
            transition = await _finish_user_cancel(
                run_id=run_id,
                request_id=request_id,
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                writer=writer,
                run=run,
            )
            logger.info(f"Run user cancellation settled: run={run_id}, changed={transition.changed}")
            return

        try:
            released = await release_run_lease_for_retry(run_id, worker_id)
        except Exception:
            logger.error(f"Infrastructure cancellation could not release AgentRun lease: run={run_id}", exc_info=True)
            raise cancellation
        if not released and await _confirmed_user_cancel(run_id):
            transition = await _finish_user_cancel(
                run_id=run_id,
                request_id=request_id,
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                writer=writer,
                run=run,
            )
            logger.info(f"Run concurrent user cancellation settled: run={run_id}, changed={transition.changed}")
            return
        if not released:
            logger.warning(f"Infrastructure cancellation could not release AgentRun lease: run={run_id}")
        raise
    except RuntimeCleanupPendingError:
        raise
    except ExceptionGroup as e:
        await _flush_writer_best_effort(writer)
        message = str(e)
        logger.error(f"Run failed {run_id}: {message}")
        error_chunk = {
            "status": "error",
            "error_type": "worker_error",
            "error_message": message,
            "request_id": request_id,
            "retryable": False,
        }
        transition = await _finish_run(
            run_id,
            "failed",
            thread_id=thread_id,
            chunk=error_chunk,
            error_type="worker_error",
            error_message=message,
            current_user=user,
            worker_id=worker_id,
            publish_end=False,
        )
        if transition.changed:
            await _append_run_event_best_effort(
                run_id,
                "error",
                {"chunk": error_chunk, "retryable": False},
                thread_id=thread_id,
            )
            await _append_end_event(run_id, "failed", thread_id=thread_id, payload={"chunk": error_chunk})
        return
    except Exception as e:
        await _flush_writer_best_effort(writer)
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
            if await _confirmed_user_cancel(run_id):
                await _finish_user_cancel(
                    run_id=run_id,
                    request_id=request_id,
                    thread_id=thread_id,
                    current_user=user,
                    worker_id=worker_id,
                    writer=writer,
                    run=run,
                )
                return
            if _is_last_try(ctx):
                transition = await _finish_run(
                    run_id,
                    "failed",
                    thread_id=thread_id,
                    chunk=retryable_error_chunk,
                    error_type="retryable_worker_error",
                    error_message=str(e),
                    current_user=user,
                    worker_id=worker_id,
                    publish_end=False,
                )
                if transition.changed:
                    await _append_run_event_best_effort(
                        run_id,
                        "error",
                        {"chunk": retryable_error_chunk, "retryable": True},
                        thread_id=thread_id,
                    )
                    await _append_end_event(
                        run_id,
                        transition.status or "failed",
                        thread_id=thread_id,
                        payload={"chunk": retryable_error_chunk},
                    )
                logger.error(f"Run failed after retries exhausted {run_id}: {e}")
                return

            if not await release_run_lease_for_retry(run_id, worker_id):
                if await _confirmed_user_cancel(run_id):
                    await _finish_user_cancel(
                        run_id=run_id,
                        request_id=request_id,
                        thread_id=thread_id,
                        current_user=user,
                        worker_id=worker_id,
                        writer=writer,
                        run=run,
                    )
                    return
                logger.warning(f"Run retry skipped after ownership changed: {run_id}")
                return
            retry_run = await _get_run(run_id)
            if retry_run is not None and retry_run.run_type != "subagent":
                await _require_runtime_cleanup(retry_run, f"Run {run_id} 尚未完成 retry runtime cleanup")
            await _append_run_event_best_effort(
                run_id,
                "error",
                {"chunk": retryable_error_chunk, "retryable": True},
                thread_id=thread_id,
            )
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
        transition = await _finish_run(
            run_id,
            "failed",
            thread_id=thread_id,
            chunk=error_chunk,
            error_type="worker_error",
            error_message=str(e),
            current_user=user,
            worker_id=worker_id,
            publish_end=False,
        )
        if transition.changed:
            await _append_run_event_best_effort(
                run_id,
                "error",
                {"chunk": error_chunk, "retryable": False},
                thread_id=thread_id,
            )
            await _append_end_event(run_id, "failed", thread_id=thread_id, payload={"chunk": error_chunk})
        return
    finally:
        await run_ctx.close()
        try:
            final_run = await _get_run(run_id)
        except Exception:
            logger.error(f"Failed to load AgentRun during lifecycle cleanup: run={run_id}", exc_info=True)
            final_run = None
        if final_run and final_run.status in TERMINAL_RUN_STATUSES:
            await _finish_execution_tree_children(final_run)
        if final_run and final_run.status == "cancelled":
            await _clear_cancel_signal_best_effort(run_id)
        # completed 后尝试派发线程的下一个排队请求
        if final_run and final_run.status == "completed" and not final_run.runtime_cleanup_pending:
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


async def _reconcile_agent_run_leases_forever() -> None:
    """周期收敛失去 heartbeat 的 Run；多个 worker 并发执行仍由行锁保证单赢家。"""
    while True:
        await asyncio.sleep(RUN_RECONCILIATION_SECONDS)
        try:
            reconciled_ids = await reconcile_expired_run_leases()
            if reconciled_ids:
                logger.warning(f"Reconciled expired AgentRun leases: count={len(reconciled_ids)}")
            cleaned_ids = await reconcile_pending_runtime_cleanups()
            if cleaned_ids:
                logger.warning(f"Reconciled pending runtime cleanups: count={len(cleaned_ids)}")
            await recover_pending_dispatches()
            await _publish_reconciliation_health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Failed to reconcile expired AgentRun leases", exc_info=True)


async def _reconcile_durable_tasks_forever() -> None:
    """周期收敛失联通用 Task，并补发持久 pending 意图。"""
    while True:
        await asyncio.sleep(TASK_RECONCILIATION_SECONDS)
        try:
            reconciled = await reconcile_and_publish_tasks()
            if reconciled:
                logger.warning("Reconciled expired durable tasks: count=%s", len(reconciled))
            await _publish_task_reconciliation_health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Failed to reconcile durable tasks", exc_info=True)


async def _publish_task_reconciliation_health() -> None:
    """续租 worker 的 Durable Task 收敛与 pending 补发能力。"""
    redis = await get_redis_client()
    await redis.set(
        TASK_RECONCILIATION_HEALTH_KEY,
        WORKER_ID,
        ex=TASK_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


async def _publish_reconciliation_health() -> None:
    """续租 worker 的 AgentRun lease 收敛能力；持续失败后 readiness 自动失效。"""

    redis = await get_redis_client()
    await redis.set(
        WORKER_RECONCILIATION_HEALTH_KEY,
        WORKER_ID,
        ex=WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


async def _worker_startup(ctx):
    """初始化 worker 依赖。"""

    if not isinstance(ctx, dict):
        raise TypeError("ARQ worker context 必须是字典")
    AuthUtils.require_security_secrets()
    ctx["worker_id"] = WORKER_ID
    pg_manager.initialize()
    await pg_manager.require_current_schema(include_knowledge=not lite_mode_enabled())
    async with pg_manager.get_async_session_context() as session:
        from yuxi.config.options import (
            ensure_options_in_db,
            invalidate_option_cache,
            system_options,
        )

        await ensure_options_in_db(session)
        await session.commit()
    await invalidate_option_cache(system_options.key)
    try:
        await ensure_builtin_mcp_servers_in_db()
    except Exception as exc:
        logger.error(
            "Optional worker component failed: component=builtin_mcp_servers, type=%s",
            type(exc).__name__,
        )
    async with pg_manager.get_async_session_context() as session:
        await init_builtin_skills(session)
    reconciled_ids = await reconcile_expired_run_leases()
    if reconciled_ids:
        logger.warning(f"Reconciled expired AgentRun leases at startup: count={len(reconciled_ids)}")
    await reconcile_pending_runtime_cleanups()
    await recover_pending_dispatches()
    await reconcile_and_publish_tasks()
    await _publish_task_reconciliation_health()
    await _publish_reconciliation_health()
    ctx[_RECONCILIATION_TASK_KEY] = asyncio.create_task(_reconcile_agent_run_leases_forever())
    ctx[_TASK_RECONCILIATION_TASK_KEY] = asyncio.create_task(_reconcile_durable_tasks_forever())


async def _worker_shutdown(ctx):
    """关闭 worker 共享连接。"""

    if isinstance(ctx, dict):
        reconciliation_tasks = [
            ctx.pop(_RECONCILIATION_TASK_KEY, None),
            ctx.pop(_TASK_RECONCILIATION_TASK_KEY, None),
        ]
        reconciliation_tasks = [task for task in reconciliation_tasks if task is not None]
        for task in reconciliation_tasks:
            task.cancel()
        if reconciliation_tasks:
            await asyncio.gather(*reconciliation_tasks, return_exceptions=True)
    from yuxi.services.run_queue_service import close_queue_clients

    await close_queue_clients()
    await pg_manager.close()


class WorkerSettings:
    functions = [
        process_agent_run,
        func(process_task, timeout=TASKER_MAX_TIMEOUT_SECONDS + 30),
    ]
    max_jobs = 10
    max_tries = 2
    retry_jobs = True
    # 单任务最长执行时间（秒），可配置：超长图谱构建/深度检索场景需调大，
    # 避免长任务被 arq 取消并误标为 cancelled。
    job_timeout = int(os.getenv("YUXI_JOB_TIMEOUT_SECONDS", "3600"))
    keep_result = 60
    health_check_interval = WORKER_HEALTH_INTERVAL_SECONDS
    health_check_key = WORKER_HEALTH_KEY
    on_startup = _worker_startup
    on_shutdown = _worker_shutdown
    redis_settings = get_arq_redis_settings()
