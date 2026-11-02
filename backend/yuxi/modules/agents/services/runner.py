"""AgentRun 执行与终态编排。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from arq.worker import RetryJob
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.runtime_settings import get_int_env
from yuxi.modules.agents.input_messages import restore_chat_input_message
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.repositories.environment import AgentEnvRepository
from yuxi.modules.agents.repositories.runs import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.modules.agents.runtime.callbacks.model_request_timing import FirstModelRequestRecorder
from yuxi.modules.agents.services.checkpoint_state import get_agent_state_view
from yuxi.modules.agents.services.event_writer import (
    LOADING_FLUSH_INTERVAL_MS,
    LOADING_FLUSH_MAX_CHARS,
    PublicEventWriter,
    append_run_event_best_effort,
    contains_model_output,
    flush_writer_best_effort,
    publish_run_settlement,
)
from yuxi.modules.agents.services.execution import RunExecutionResult, stream_agent_execution
from yuxi.modules.agents.services.leases import (
    WORKER_ID,
    mark_run_running,
    release_run_lease_for_retry,
    renew_run_lease,
    run_attempt_finished,
)
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.modules.agents.services.preparation import (
    prepare_and_record_run_execution,
)
from yuxi.modules.agents.services.run_lifecycle import complete_run_runtime_cleanup, finish_run_lifecycle, settle_run
from yuxi.modules.agents.services.titles import start_session_title
from yuxi.modules.agents.services.transport import (
    wait_for_cancel_signal,
)
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.bindings import (
    AuthorizedWorkdir,
    resolve_authorized_workdir,
)
from yuxi.shared.datetime import utc_now

MAX_RUN_TRIES = 2
RUN_TIMEOUT_SECONDS = get_int_env("YUXI_JOB_TIMEOUT_SECONDS", 3600)


RUN_CANCEL_POLL_SECONDS = 0.2


RUN_DURABLE_CANCEL_POLL_SECONDS = 1.0


RUN_HEARTBEAT_SECONDS = 30


SUPPORTED_RUN_TYPES = {"chat", "resume"}


class RetryableRunError(RetryJob):
    """Error type that should trigger ARQ retry."""


class RuntimeCleanupPendingError(RetryJob):
    """根 Run 已终态，但 execution runtime 的持久清理尚未完成。"""


class NonRetryableRunError(Exception):
    """Error type that should not trigger ARQ retry."""


class RunAdmissionRejected(Exception):
    """携带执行前拒绝的业务原因，由 runner 统一结算。"""

    def __init__(self, error_type: str, message: str):
        """保留已有错误类型与文案，不改变拒绝优先序。"""
        super().__init__(message)
        self.error_type = error_type


async def _validate_run_workdir_binding(run: AgentRun) -> AuthorizedWorkdir:
    """在执行器边界验证持久 Run 的 Session、执行树与 Workdir 归属。"""
    async with pg_manager.get_async_session_context() as db:
        binding = await resolve_authorized_workdir(
            thread_id=str(run.thread_id),
            uid=str(run.uid),
            app_id=run.app_id,
            db=db,
        )
        if int(binding.session_record_id) != int(run.session_record_id):
            raise NonRetryableRunError("AgentRun 的 Session 身份不一致")

        persisted_scope = str(run.runtime_scope_id or "").strip()
        if not persisted_scope:
            raise NonRetryableRunError("AgentRun 缺少 runtime scope")
        from sqlalchemy import select

        from yuxi.modules.agents.models.sessions import Session

        member = await db.get(Session, run.session_record_id)
        root = await db.scalar(select(Session).where(Session.thread_id == persisted_scope))
        if (
            member is None
            or root is None
            or member.tree_root_thread_id != persisted_scope
            or root.tree_root_thread_id != root.thread_id
            or root.parent_thread_id is not None
            or root.uid != run.uid
            or root.app_id != run.app_id
            or root.project_id != member.project_id
        ):
            raise NonRetryableRunError("AgentRun 的共享沙盒树归属非法")
        root_binding = await resolve_authorized_workdir(thread_id=root.thread_id, uid=str(run.uid), app_id=run.app_id, db=db)
        if root_binding.workdir_path != binding.workdir_path:
            raise NonRetryableRunError("协作树成员的 Workdir 不一致")
    return binding


@dataclass
class TerminalTransition:
    status: str | None
    changed: bool


@dataclass
class RunContext:
    run_id: str
    worker_id: str
    uid: str | None = None
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
        tasks = [task for task in (self._watch_task, self._durable_cancel_task, self._heartbeat_task) if task is not None]
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
        return self.cancel_event.is_set()

    async def _watch_cancel_signal(self) -> None:
        await wait_for_cancel_signal(
            self.run_id,
            poll_interval_seconds=RUN_CANCEL_POLL_SECONDS,
        )
        self.cancel_event.set()
        await self._cancel_sandbox_commands()

    async def _watch_durable_cancel(self) -> None:
        """低频轮询 PostgreSQL，确保 Redis 丢信号时取消仍然 fail-closed。"""

        while not self.cancel_event.is_set():
            try:
                cancelled = await _is_cancel_requested(self.run_id)
            except Exception:
                logger.opt(exception=True).error(f"Failed to read durable AgentRun cancellation: run={self.run_id}")
                self.cancel_event.set()
                return
            else:
                if cancelled:
                    self.cancel_event.set()
                    await self._cancel_sandbox_commands()
                    return
            try:
                await asyncio.wait_for(
                    self.cancel_event.wait(),
                    timeout=RUN_DURABLE_CANCEL_POLL_SECONDS,
                )
            except TimeoutError:
                continue

    async def _heartbeat_lease(self) -> None:
        # 取消意图不能终止续租：已开始的工具收尾仍拥有执行名额。
        while True:
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            try:
                renewed = await renew_run_lease(self.run_id, self.worker_id)
                if not renewed and await run_attempt_finished(self.run_id, self.worker_id):
                    # 终态事务已清除 lease；本 attempt 仍需完成流收尾、清理和事件发布。
                    return
            except Exception:
                logger.opt(exception=True).error(f"Failed to renew AgentRun lease: run={self.run_id}")
                renewed = False
            if not renewed:
                self.lease_lost = True
                self.cancel_event.set()
                await self._cancel_sandbox_commands()
                return

    async def _cancel_sandbox_commands(self) -> None:
        """远端终止独立于 LangGraph 流关闭，避免工具收尾相互等待。"""
        if self.uid is None:
            return
        from yuxi.infrastructure.filesystem import await_io
        from yuxi.modules.agents.runtime.sandbox import get_sandbox_provider

        try:
            await await_io(asyncio.to_thread(get_sandbox_provider().cleanup_run, self.uid, self.run_id, self.worker_id))
        except Exception:
            logger.exception("Run 外部命令尚未确认停止，保留持久清理责任: run={}", self.run_id)


async def _require_runtime_cleanup(run: AgentRun, message: str) -> None:
    """把外部命令/并发清理失败统一转成 ARQ 可重试的 durable cleanup。"""
    try:
        cleaned = await complete_run_runtime_cleanup(run)
    except Exception as exc:
        raise RuntimeCleanupPendingError(message) from exc
    if not cleaned:
        raise RuntimeCleanupPendingError(message)


async def _get_run(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        return await repo.get_run(run_id)


async def mark_run_terminal(
    run_id: str,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
    token_usage: dict | None = None,
    worker_id: str | None = None,
):
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        run = await repo.get_run(run_id)
        if run is None:
            return TerminalTransition(status=None, changed=False)
        if run.status not in TERMINAL_RUN_STATUSES:
            settled = await settle_run(
                db=db,
                run=run,
                worker_id=worker_id,
                status=status,
                token_usage=token_usage,
                error_type=error_type,
                error_message=error_message,
            )
            changed = settled.changed
            persisted_status = settled.status
        else:
            run, changed = await repo.set_terminal_status(
                run_id,
                status=status,
                error_type=error_type,
                error_message=error_message,
                token_usage=token_usage,
                worker_id=worker_id,
            )
            persisted_status = run.status if run else None
    return TerminalTransition(status=persisted_status, changed=changed)


async def _record_run_timing_best_effort(
    run_id: str,
    worker_id: str,
    phase: str,
    *,
    observed_at: datetime | None = None,
) -> None:
    """记录单次 Run 阶段时间；观测失败不覆盖业务执行结果。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            repository = AgentRunRepository(db)
            if phase == "prepared":
                await repository.record_prepared(run_id, worker_id=worker_id, observed_at=observed_at)
            elif phase == "first_output":
                await repository.record_first_output(run_id, worker_id=worker_id, observed_at=observed_at)
            else:
                raise ValueError(f"不支持的 AgentRun timing phase: {phase}")
    except Exception:
        logger.opt(exception=True).warning("Failed to persist AgentRun timing: run={}, phase={}", run_id, phase)


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
        logger.opt(exception=True).error(f"Failed to confirm durable AgentRun cancellation: run={run_id}")
        return False


async def _read_run_token_usage_from_state(*, run_id: str, thread_id: str, current_user, app_id: str | None = None) -> dict | None:
    """从当前线程 state 读取属于指定 Run 的用量快照。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            view = await get_agent_state_view(
                thread_id=thread_id,
                current_user=current_user,
                db=db,
                app_id=app_id,
                include_relations=False,
            )
    except Exception:
        logger.opt(exception=True).warning(f"Failed to read token usage from state for run {run_id}")
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
    return _job_try(ctx) >= max(1, int(MAX_RUN_TRIES))


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


async def _publish_committed_terminal(
    run: AgentRun | None, *, run_id: str, status: str, thread_id: str | None, publish_end: bool = True
) -> None:
    """已提交的状态释放运行资源后才发布；状态核对由执行结果边界负责。"""
    if run is not None:
        await _require_runtime_cleanup(run, f"Run {run.id} 尚未完成命令清理")
    if publish_end:
        await publish_run_settlement(run_id, status, thread_id=thread_id)


async def _settle_uncommitted_run(
    run_id: str,
    status: Literal["failed", "cancelled"],
    *,
    thread_id: str | None,
    current_user,
    worker_id: str,
    error_type: str | None = None,
    error_message: str | None = None,
    writer: PublicEventWriter | None = None,
    publish_end: bool = True,
) -> TerminalTransition:
    """收敛尚未提交的失败或用户取消；已提交执行结果由公共发布边界收尾。"""
    if writer is not None:
        await flush_writer_best_effort(writer)
    run = await _get_run(run_id)
    token_usage = {"available": False}
    if thread_id and current_user is not None:
        token_usage = (
            await _read_run_token_usage_from_state(
                run_id=run_id,
                thread_id=thread_id,
                current_user=current_user,
                app_id=run.app_id if run else None,
            )
            or token_usage
        )
    if status == "cancelled":
        error_type, error_message = "cancelled", "对话已取消"
    transition = await mark_run_terminal(
        run_id,
        status,
        error_type=error_type,
        error_message=error_message,
        token_usage=token_usage,
        worker_id=worker_id,
    )
    if status == "failed" and transition.status == "cancel_requested":
        # 用户取消可能晚于失败检查、早于终态事务；仍由当前 owner 确认取消。
        transition = await mark_run_terminal(
            run_id,
            "cancelled",
            error_type="cancelled",
            error_message="对话已取消",
            token_usage=token_usage,
            worker_id=worker_id,
        )
    if publish_end and transition.status in TERMINAL_RUN_STATUSES:
        committed_run = await _get_run(run_id)
        await _publish_committed_terminal(
            committed_run,
            run_id=run_id,
            status=transition.status,
            thread_id=thread_id,
            publish_end=publish_end and transition.changed,
        )
    return transition


async def _consume_stream_with_cancel(agen, run_ctx: RunContext):
    """每 Run 只建一个取消等待器，退出前回收执行任务和生成器。"""
    cancel_task = asyncio.create_task(run_ctx.wait_cancelled())
    next_task = None
    try:
        while True:
            next_task = asyncio.create_task(agen.__anext__())
            done, _ = await asyncio.wait({next_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task in done:
                raise asyncio.CancelledError(f"run {run_ctx.run_id} cancelled")
            try:
                yield next_task.result()
            except StopAsyncIteration:
                return
    finally:

        async def close_execution():
            """关闭整条执行链后，外层才能释放 lease 或重试。"""
            tasks = [cancel_task] if next_task is None else [cancel_task, next_task]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await agen.aclose()

        cleanup = asyncio.create_task(close_execution())
        cancelled_during_cleanup = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # ARQ abort 和进程退出可以重复取消；执行未关闭就不能交出 owner。
                cancelled_during_cleanup = True
        cleanup.result()
        if cancelled_during_cleanup:
            raise asyncio.CancelledError


async def process_agent_run(ctx, run_id: str):
    """在业务执行时限内运行，保留 ARQ 外层时间用于清理。"""
    async with asyncio.timeout(RUN_TIMEOUT_SECONDS) as execution_timeout:
        await _process_agent_run(ctx, run_id, execution_timeout)


async def _process_agent_run(ctx, run_id: str, execution_timeout: asyncio.Timeout):
    """执行持久 Run，分别收敛用户取消、执行超时和基础设施退出。"""
    run = await _get_run(run_id)
    if not run:
        logger.warning(f"Run not found: {run_id}")
        return

    if run.status in TERMINAL_RUN_STATUSES:
        cleanup_was_pending = bool(getattr(run, "runtime_cleanup_pending", False))
        if cleanup_was_pending:
            await _require_runtime_cleanup(run, f"Run {run_id} 尚未完成 runtime cleanup")
            await publish_run_settlement(run_id, run.status, thread_id=run.thread_id)
        # cleanup 在独立事务释放责任；重读后复用正常执行的派发与观察收尾。
        current = await _get_run(run_id) if cleanup_was_pending else run
        if current is not None:
            await finish_run_lifecycle(current)
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
    turn_id = run.turn_id
    thread_id = run.thread_id
    user = None
    from yuxi.modules.agents.runtime.sandbox import sandbox_run_env, sandbox_run_owner

    sandbox_owner_token = sandbox_run_owner.set((run_id, worker_id))
    sandbox_env_token = sandbox_run_env.set(None)
    run_ctx = RunContext(run_id=run_id, worker_id=worker_id, uid=uid)
    writer = PublicEventWriter(
        run_id=run_id,
        interval_ms=LOADING_FLUSH_INTERVAL_MS,
        max_chars=LOADING_FLUSH_MAX_CHARS,
    )
    model_request_recorder = FirstModelRequestRecorder()
    try:
        if await _is_cancel_requested(run_id):
            run_ctx.cancel_event.set()
            raise asyncio.CancelledError(f"run {run_id} cancelled before execution")

        # 准入拒绝共用出口；结算自身失败仍交给外层的取消与重试策略。
        try:
            if not isinstance(run.input_payload, dict):
                raise RunAdmissionRejected("invalid_input_payload", "run input_payload 必须是对象")
            payload = run.input_payload
            runtime = payload.get("runtime") or {}
            if not isinstance(runtime, dict):
                raise RunAdmissionRejected("invalid_runtime_payload", "run input_payload.runtime 必须是对象")

            input_messages = await _load_run_input_messages(run)
            if not input_messages:
                raise RunAdmissionRejected("input_message_not_found", "运行任务缺少输入消息")
            if any(not isinstance(message.extra_metadata, dict) for message in input_messages):
                raise RunAdmissionRejected("invalid_input_metadata", "输入消息 metadata 必须是对象")

            input_metadata = input_messages[0].extra_metadata
            image_content = any(message.image_content for message in input_messages)

            if run_type not in SUPPORTED_RUN_TYPES:
                raise RunAdmissionRejected("invalid_run_type", f"不支持的 run_type: {run_type}")

            user = await _load_user(uid)
            if not user:
                raise RunAdmissionRejected("user_not_found", f"user {uid} not found")

            try:
                workdir_binding = await _validate_run_workdir_binding(run)
            except Exception as exc:  # noqa: BLE001
                raise RunAdmissionRejected("invalid_runtime_scope", str(exc))

            resume_input = None
            normalized_input_messages = []
            if run_type == "resume":
                resume_input = input_metadata.get("resume")
                if resume_input is None:
                    raise RunAdmissionRejected("resume_input_not_found", "resume run 缺少 resume 输入")
            else:
                try:
                    normalized_input_messages = [
                        restore_chat_input_message(
                            content=message.content,
                            image_content=message.image_content,
                            metadata={**message.extra_metadata, "message_id": message.id},
                        )
                        for message in input_messages
                    ]
                except ValueError as exc:
                    raise RunAdmissionRejected("invalid_input_message", str(exc))

            await run_ctx.start()
            if run_type == "chat":
                start_session_title(thread_id=thread_id, uid=uid, app_id=run.app_id, content=input_messages[0].content)
            # 准备配置期间也续租；manifest 提交成功前不得开始构图执行。
            try:
                prepared_execution = await prepare_and_record_run_execution(
                    run=run,
                    user=user,
                    worker_id=worker_id,
                    workdir_binding=workdir_binding,
                )
            except Exception as manifest_error:
                if await _is_cancel_requested(run_id):
                    raise asyncio.CancelledError(f"run {run_id} cancelled during preparation")
                logger.opt(exception=True).error(f"Failed to persist AgentRun manifest: run={run_id}")
                raise RunAdmissionRejected("manifest_persist_failed", f"运行清单固化失败，执行未开始：{manifest_error}")

        except RunAdmissionRejected as rejection:
            await _settle_uncommitted_run(
                run_id,
                "failed",
                worker_id=worker_id,
                error_type=rejection.error_type,
                error_message=str(rejection),
                thread_id=thread_id,
                current_user=user,
            )
            return

        # 固化期间用户可能已取消；复查一次，把取消竞态窗口恢复到执行开始前的水平。
        if await _is_cancel_requested(run_id):
            raise asyncio.CancelledError(f"run {run_id} cancelled after manifest recorded")

        context = prepared_execution.context
        meta = {
            "run_id": run_id,
            "turn_id": turn_id,
            "input_ids": list(dict.fromkeys(message.source_input_id for message in input_messages if message.source_input_id)),
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "uid": user.uid,
            "has_image": bool(image_content),
            "model_spec": context.model,
            "tool_approval_mode": context.tool_approval_mode,
            "run_type": run_type,
            "created_by_run_id": run.created_by_run_id,
            "worker_id": worker_id,
            "runtime_scope_id": context.runtime_scope_id,
            "workdir_relative_path": context.workdir_relative_path,
            "workdir_path": context.workdir_path,
        }
        if input_metadata.get("source"):
            meta["source"] = input_metadata.get("source")

        async def record_prepared() -> None:
            await _record_run_timing_best_effort(
                run_id,
                worker_id,
                "prepared",
                observed_at=utc_now(),
            )

        terminal_set = False
        first_output_observed = bool(getattr(run, "first_output_at", None))
        async with pg_manager.get_async_session_context() as db:
            # 密钥只留在本次执行上下文；并发命令复用，重试 attempt 重新取快照。
            saved_env = await AgentEnvRepository(db).get_by_uid(uid)
            sandbox_run_env.set(dict(saved_env.env or {}) if saved_env else {})
            stream = stream_agent_execution(
                thread_id=thread_id,
                meta=meta,
                current_user=user,
                db=db,
                prepared_execution=prepared_execution,
                on_prepared=record_prepared,
                model_request_recorder=model_request_recorder,
                agent_slug=agent_slug,
                input_messages=normalized_input_messages,
                resume_input=resume_input,
                is_resume=run_type == "resume",
            )

            async with aclosing(_consume_stream_with_cancel(stream, run_ctx)) as events:
                async for event in events:
                    if not isinstance(event, RunExecutionResult):
                        owner = event
                        if (
                            owner.get("session_id") != thread_id
                            or owner.get("turn_id") != turn_id
                            or event.get("yuxi", {}).get("run_id") != run_id
                        ):
                            raise ValueError("公开事件缺少当前 Thread、Turn、Run 的明确归属")
                        await writer.append(event)
                        if not first_output_observed and contains_model_output(event):
                            first_output_observed = True
                            await writer.flush()
                            await _record_run_timing_best_effort(
                                run_id,
                                worker_id,
                                "first_output",
                                observed_at=utc_now(),
                            )
                        continue
                    await writer.flush()
                    if event.status != "failed" and event.checkpoint is None:
                        raise RuntimeError("执行结果缺少最终 checkpoint")
                    if not event.committed:
                        if event.status != "failed":
                            raise RuntimeError(f"执行结果缺少 PostgreSQL {event.status} 终态")
                        transition = await _settle_uncommitted_run(
                            run_id,
                            event.status,
                            thread_id=thread_id,
                            current_user=user,
                            worker_id=worker_id,
                            error_type=event.error_type,
                            error_message=event.error_message,
                            publish_end=False,
                        )
                        if transition.status != event.status:
                            raise RuntimeError("执行结果未能提交 PostgreSQL 终态")
                    committed_run = await _get_run(run_id)
                    if committed_run is None or committed_run.status != event.status:
                        raise RuntimeError("执行结果与 PostgreSQL 终态不一致")
                    await _publish_committed_terminal(committed_run, run_id=run_id, status=event.status, thread_id=thread_id)
                    terminal_set = True
        await writer.flush()
        if not terminal_set:
            if await run_ctx.is_cancelled():
                raise asyncio.CancelledError(f"run {run_id} cancelled")
            raise RuntimeError("执行流结束但未交付执行结果和持久终态")

    except asyncio.CancelledError as cancellation:
        await model_request_recorder.persist(run_id=run_id, worker_id=worker_id)
        await flush_writer_best_effort(writer)
        if run_ctx.lease_lost:
            logger.warning(f"Run stopped after losing its lease: {run_id}")
            return
        if await _confirmed_user_cancel(run_id):
            transition = await _settle_uncommitted_run(
                status="cancelled",
                run_id=run_id,
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                writer=writer,
            )
            logger.info(f"Run user cancellation settled: run={run_id}, changed={transition.changed}")
            return

        if execution_timeout.expired():
            await _settle_uncommitted_run(
                run_id,
                "failed",
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                error_type="execution_timeout",
                error_message=f"任务执行超过 {RUN_TIMEOUT_SECONDS} 秒时限",
            )
            return

        try:
            released = await release_run_lease_for_retry(run_id, worker_id)
        except Exception:
            logger.opt(exception=True).error(f"Infrastructure cancellation could not release AgentRun lease: run={run_id}")
            raise cancellation
        if not released and await _confirmed_user_cancel(run_id):
            transition = await _settle_uncommitted_run(
                status="cancelled",
                run_id=run_id,
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                writer=writer,
            )
            logger.info(f"Run concurrent user cancellation settled: run={run_id}, changed={transition.changed}")
            return
        if not released:
            logger.warning(f"Infrastructure cancellation could not release AgentRun lease: run={run_id}")
        raise
    except RuntimeCleanupPendingError:
        raise
    except Exception as exc:
        await flush_writer_best_effort(writer)
        if await _confirmed_user_cancel(run_id):
            await _settle_uncommitted_run(
                status="cancelled",
                run_id=run_id,
                thread_id=thread_id,
                current_user=user,
                worker_id=worker_id,
                writer=writer,
            )
            return
        if _is_retryable_exception(exc) and not _is_last_try(ctx):
            if not await release_run_lease_for_retry(run_id, worker_id):
                return
            retry_run = await _get_run(run_id)
            await _require_runtime_cleanup(retry_run, f"Run {run_id} 尚未完成 retry runtime cleanup")
            adapter = OpenAIEventAdapter(run_id=run_id, turn_id=turn_id, thread_id=thread_id, worker_id=worker_id)
            await append_run_event_best_effort(
                run_id,
                adapter.extension(
                    "run.retrying",
                    f"retry:{_job_try(ctx)}",
                    job_try=_job_try(ctx),
                    message=str(exc),
                ),
            )
            raise RetryableRunError(str(exc)) from exc
        logger.error("Run failed {}: {}", run_id, exc)
        await _settle_uncommitted_run(
            run_id,
            "failed",
            thread_id=thread_id,
            error_type="worker_error",
            error_message=str(exc),
            current_user=user,
            worker_id=worker_id,
        )
    finally:
        sandbox_run_env.reset(sandbox_env_token)
        sandbox_run_owner.reset(sandbox_owner_token)
        await run_ctx.close()
        try:
            final_run = await _get_run(run_id)
        except Exception:
            logger.opt(exception=True).error(f"Failed to load AgentRun during lifecycle cleanup: run={run_id}")
            final_run = None
        if final_run:
            await finish_run_lifecycle(final_run)


async def _load_run_input_messages(run: AgentRun) -> list[Message]:
    """按已消费 Input 顺序恢复本段消息；控制和子执行使用单条输入。"""
    async with pg_manager.get_async_session_context() as db:
        return list(await db.scalars(select(Message).where(Message.run_id == run.id, Message.role == "user").order_by(Message.id)))
