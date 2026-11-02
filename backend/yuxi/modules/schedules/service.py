"""用户 Agent 定时任务的用例、校验和 worker 调度。"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, CroniterError, croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.runtime.agent_backends import AgentBackendNotFoundError, get_agent_backend
from yuxi.modules.agents.runtime.tool_approval import normalize_tool_approval_mode
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.inputs import create_thread
from yuxi.modules.identity.models import User
from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun
from yuxi.modules.schedules.repository import ScheduledAgentRepository
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.shared.datetime import format_utc_datetime, utc_now
from yuxi.shared.errors import DomainError, InvalidInputError, ResourceNotFoundError, StateConflictError
from yuxi.shared.hashing import json_intent_hash

SCHEDULED_AGENT_SOURCE = "scheduled_agent"
MAX_PROMPT_LENGTH = 32_000
MAX_NAME_LENGTH = 255
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def build_stable_id(prefix: str, value: str) -> str:
    """为调度对象生成稳定、长度受限的 ID。"""
    return f"{prefix[:16]}-{hashlib.sha256(value.encode()).hexdigest()[:47]}"


def _normalize_request_id(value: str) -> str:
    """校验客户端持有的稳定请求 ID。"""
    request_id = str(value or "").strip()
    if not 8 <= len(request_id) <= 64 or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise InvalidInputError("request_id 必须是 8 到 64 位字母、数字或 ._:-")
    return request_id


def _intent_hash(data: dict) -> str:
    """为规范化创建意图生成稳定摘要。"""
    return json_intent_hash(data)


def validate_schedule(cron_expression: str, timezone: str) -> tuple[str, str]:
    """校验 cron 表达式和 IANA 时区。"""
    expression = str(cron_expression or "").strip()
    if not expression:
        raise InvalidInputError("cron_expression 不能为空")
    try:
        if len(expression.split()) != 5 or not croniter.is_valid(expression):
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidInputError("cron_expression 不是有效的 5 段 cron 表达式") from None
    zone = str(timezone or "").strip()
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidInputError("timezone 必须是有效的 IANA 时区") from None
    try:
        next_run_at(expression, zone, utc_now())
    except (CroniterBadDateError, CroniterError, OverflowError, ValueError):
        raise InvalidInputError("cron_expression 没有可计算的下一次触发时间") from None
    return expression, zone


def next_run_at(cron_expression: str, timezone: str, after: datetime) -> datetime:
    """计算下一次 UTC 触发时间，数据库统一保存带时区 UTC。"""
    zone = ZoneInfo(timezone)
    local_after = after.astimezone(zone)
    next_local = croniter(cron_expression, local_after).get_next(datetime)
    return next_local.astimezone(ZoneInfo("UTC"))


def _normalize_text(value: str, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InvalidInputError(f"{field} 不能为空")
    if len(normalized) > maximum:
        raise InvalidInputError(f"{field} 不能超过 {maximum} 个字符")
    return normalized


async def _validate_project(project_id: str, user: User, db: AsyncSession):
    """锁定任务绑定的活动 Project，直到调用方提交事务。"""
    project = await ProjectRepository(db).lock_active_for_user(project_id, str(user.uid))
    if not project:
        raise ResourceNotFoundError("Project 不存在或不可访问")
    return project


async def _validate_agent(agent_slug: str, user: User, db: AsyncSession):
    repo = AgentRepository(db)
    agent = await repo.get_visible_by_slug(slug=agent_slug, user=user)
    if not agent:
        raise ResourceNotFoundError("智能体不存在或不可访问")
    try:
        get_agent_backend(agent.backend_id)
    except AgentBackendNotFoundError as exc:
        raise ResourceNotFoundError(str(exc)) from exc
    return agent


def _new_scheduled_run(
    *,
    job: ScheduledAgentJob,
    trigger: str,
    occurrence_key: str,
    scheduled_for: datetime,
    active_run: bool,
    identity: str | None = None,
) -> ScheduledAgentRun:
    """从任务快照创建一次触发意图。"""
    identity = identity or f"{job.id}:{occurrence_key}"
    return ScheduledAgentRun(
        id=build_stable_id("scheduled-run", identity),
        job_id=job.id,
        input_id=build_stable_id("scheduled-input", identity),
        thread_id=build_stable_id("scheduled-thread", identity),
        trigger=trigger,
        occurrence_key=occurrence_key,
        scheduled_for=scheduled_for,
        project_id=job.project_id,
        agent_slug=job.agent_slug,
        session_title=job.name,
        prompt=job.prompt,
        tool_approval_mode=job.tool_approval_mode,
        model_spec=job.model_spec,
        status="skipped" if active_run else "dispatching",
        error_message="上一次运行尚未结束" if active_run else None,
    )


async def _create_run_record(
    *,
    repo: ScheduledAgentRepository,
    job: ScheduledAgentJob,
    trigger: str,
    occurrence_key: str,
    scheduled_for: datetime,
) -> ScheduledAgentRun:
    """创建包含配置快照且禁止重叠的执行记录。"""
    return await repo.add_run(
        _new_scheduled_run(
            job=job,
            trigger=trigger,
            occurrence_key=occurrence_key,
            scheduled_for=scheduled_for,
            active_run=await repo.has_active_run(job.id),
        )
    )


async def list_scheduled_jobs(*, user: User, db: AsyncSession) -> dict:
    """列出当前用户自己的定时任务。"""
    repo = ScheduledAgentRepository(db)
    jobs = await repo.list_jobs(str(user.uid))
    runs_by_job: dict[str, list[dict]] = {job.id: [] for job in jobs}
    for scheduled_run, input_item, run in await repo.list_recent_runs([job.id for job in jobs], str(user.uid), 3):
        runs_by_job[scheduled_run.job_id].append(_execution_to_dict(scheduled_run, input_item, run))
    result = []
    for job in jobs:
        item = job.to_dict()
        item["runs"] = runs_by_job[job.id]
        result.append(item)
    return {"jobs": result}


async def list_scheduled_job_history(*, job_id: str, user: User, db: AsyncSession, limit: int, offset: int) -> dict | None:
    """分页读取用户自有任务的执行历史，沿用统一执行状态投影。"""
    repo = ScheduledAgentRepository(db)
    if await repo.get_job(job_id, str(user.uid)) is None:
        return None
    rows, total = await repo.list_run_history(job_id, str(user.uid), limit=limit, offset=offset)
    return {
        "items": [_execution_to_dict(*row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _execution_to_dict(scheduled_run, input_item, run) -> dict:
    """从 Input 与当前 Turn/Run 装配调度记录摘要。"""
    data = scheduled_run.to_dict()
    data["session_available"] = input_item is not None
    if scheduled_run.status != "submitted" or input_item is None:
        return data

    data["turn_id"] = input_item.turn_id
    data["run_id"] = run.id if run else None
    if input_item.status == "pending" or run is None:
        data["status"] = "queued" if input_item.status == "pending" else input_item.status
        return data

    data["status"] = run.status
    data["error_message"] = run.error_message
    data["completed_at"] = format_utc_datetime(run.finished_at)
    return data


async def create_scheduled_job(*, user: User, db: AsyncSession, data: dict) -> dict:
    """按稳定请求 ID 幂等创建用户定时任务。"""
    repo = ScheduledAgentRepository(db)
    request_id = _normalize_request_id(data.get("request_id"))
    project_id = _normalize_text(data.get("project_id"), "project_id", 64)
    agent_slug = _normalize_text(data.get("agent_slug"), "agent_slug", 64)
    name = _normalize_text(data.get("name"), "name", MAX_NAME_LENGTH)
    prompt = _normalize_text(data.get("prompt"), "prompt", MAX_PROMPT_LENGTH)
    expression, timezone = validate_schedule(data.get("cron_expression"), data.get("timezone"))
    model_spec = str(data.get("model_spec") or "").strip() or None
    if model_spec and len(model_spec) > 512:
        raise InvalidInputError("model_spec 不能超过 512 个字符")
    try:
        tool_approval_mode = normalize_tool_approval_mode(data.get("tool_approval_mode", "default"))
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from None
    intent_hash = _intent_hash(
        {
            "project_id": project_id,
            "agent_slug": agent_slug,
            "name": name,
            "prompt": prompt,
            "tool_approval_mode": tool_approval_mode,
            "model_spec": model_spec,
            "cron_expression": expression,
            "timezone": timezone,
            "enabled": bool(data.get("enabled", True)),
        }
    )
    existing = await repo.get_job_by_creation_request(str(user.uid), request_id)
    if existing is not None:
        if existing.creation_intent_hash != intent_hash:
            raise StateConflictError("request_id 已用于其他定时任务创建意图")
        return existing.to_dict()

    await _validate_project(project_id, user, db)
    await _validate_agent(agent_slug, user, db)
    now = utc_now()
    job = ScheduledAgentJob(
        id=str(uuid.uuid4()),
        uid=str(user.uid),
        creation_request_id=request_id,
        creation_intent_hash=intent_hash,
        project_id=project_id,
        agent_slug=agent_slug,
        name=name,
        prompt=prompt,
        tool_approval_mode=tool_approval_mode,
        model_spec=model_spec,
        cron_expression=expression,
        timezone=timezone,
        enabled=bool(data.get("enabled", True)),
        next_run_at=next_run_at(expression, timezone, now),
        created_at=now,
        updated_at=now,
    )
    try:
        await repo.add_job(job)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        replay = await repo.get_job_by_creation_request(str(user.uid), request_id)
        if replay is None:
            raise StateConflictError("定时任务创建冲突")
        if replay.creation_intent_hash != intent_hash:
            raise StateConflictError("request_id 已用于其他定时任务创建意图")
        job = replay
    return job.to_dict()


async def update_scheduled_job(*, job_id: str, user: User, db: AsyncSession, data: dict) -> dict | None:
    """更新当前用户拥有的任务；修改计划时从当前时刻重新计算下一次触发。"""
    repo = ScheduledAgentRepository(db)
    job = await repo.get_job(job_id, str(user.uid), lock=True)
    if not job:
        return None
    if "project_id" in data:
        project_id = _normalize_text(data["project_id"], "project_id", 64)
        await _validate_project(project_id, user, db)
        job.project_id = project_id
    if "agent_slug" in data:
        agent_slug = _normalize_text(data["agent_slug"], "agent_slug", 64)
        await _validate_agent(agent_slug, user, db)
        job.agent_slug = agent_slug
    if "name" in data:
        job.name = _normalize_text(data["name"], "name", MAX_NAME_LENGTH)
    if "prompt" in data:
        job.prompt = _normalize_text(data["prompt"], "prompt", MAX_PROMPT_LENGTH)
    if "tool_approval_mode" in data:
        try:
            job.tool_approval_mode = normalize_tool_approval_mode(data["tool_approval_mode"])
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from None
    if "model_spec" in data:
        model_spec = str(data["model_spec"] or "").strip() or None
        if model_spec and len(model_spec) > 512:
            raise InvalidInputError("model_spec 不能超过 512 个字符")
        job.model_spec = model_spec
    expression = data.get("cron_expression", job.cron_expression)
    timezone = data.get("timezone", job.timezone)
    expression, timezone = validate_schedule(expression, timezone)
    if expression != job.cron_expression or timezone != job.timezone:
        job.next_run_at = next_run_at(expression, timezone, utc_now())
    job.cron_expression, job.timezone = expression, timezone
    now = utc_now()
    if "enabled" in data:
        enabled = bool(data["enabled"])
        if enabled and not job.enabled:
            job.next_run_at = next_run_at(expression, timezone, now)
        job.enabled = enabled
    job.updated_at = now
    await db.commit()
    return job.to_dict()


async def delete_scheduled_job(*, job_id: str, user: User, db: AsyncSession) -> bool:
    """软删除任务定义，保留触发记录与 AgentRun。"""
    repo = ScheduledAgentRepository(db)
    job = await repo.get_job(job_id, str(user.uid), lock=True)
    if not job:
        return False
    await repo.delete_job(job)
    await db.commit()
    return True


async def run_scheduled_job_now(
    *,
    job_id: str,
    request_id: str,
    user: User,
    db: AsyncSession,
) -> dict | None:
    """按稳定请求 ID 幂等创建手动触发记录。"""
    repo = ScheduledAgentRepository(db)
    request_id = _normalize_request_id(request_id)
    job = await repo.get_job(job_id, str(user.uid), lock=True)
    if not job:
        return None
    identity = f"{user.uid}:manual:{request_id}"
    run_id = build_stable_id("scheduled-run", identity)
    run = await repo.get_run(run_id)
    if run is not None and run.job_id != job.id:
        raise StateConflictError("request_id 已用于其他立即运行意图")
    if run is None:
        await _validate_project(job.project_id, user, db)
        await _validate_agent(job.agent_slug, user, db)
        now = utc_now()
        run = _new_scheduled_run(
            job=job,
            trigger="manual",
            occurrence_key=f"manual:{request_id}",
            scheduled_for=now,
            active_run=await repo.has_active_run(job.id),
            identity=identity,
        )
        try:
            await repo.add_run(run)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            replay = await repo.get_run(run_id)
            if replay is None:
                raise StateConflictError("立即运行请求冲突")
            if replay.job_id != job.id:
                raise StateConflictError("request_id 已用于其他立即运行意图")
            run = replay
    else:
        await db.commit()
    if run.status != "dispatching":
        return run.to_dict()
    return await dispatch_scheduled_run(scheduled_run_id=run.id)


async def _settle_dispatch_error(
    scheduled_run_id: str,
    error: Exception,
    *,
    terminal: bool,
) -> dict | None:
    """串行重查 Input；仅明确不可恢复错误终结触发记录。"""
    async with pg_manager.get_async_session_context() as db:
        scheduled_run = await db.scalar(select(ScheduledAgentRun).where(ScheduledAgentRun.id == scheduled_run_id).with_for_update())
        if scheduled_run is None:
            return None
        input_item = None
        run = None
        if scheduled_run.status == "dispatching":
            input_item, run = await ScheduledAgentRepository(db).get_input_and_run(scheduled_run.input_id)
            if input_item is not None:
                scheduled_run.status = "submitted"
            elif terminal:
                scheduled_run.status = "failed"
                scheduled_run.error_message = str(error)
            if input_item is not None or terminal:
                await db.commit()
        return _execution_to_dict(scheduled_run, input_item, run)


async def dispatch_scheduled_run(*, scheduled_run_id: str) -> dict | None:
    """用同一 Thread 接入用例提交定时任务的首批输入。"""
    try:
        async with pg_manager.get_async_session_context() as db:
            scheduled_run = await db.scalar(select(ScheduledAgentRun).where(ScheduledAgentRun.id == scheduled_run_id).with_for_update())
            if scheduled_run is None or scheduled_run.status != "dispatching":
                return scheduled_run.to_dict() if scheduled_run else None
            job = await db.get(ScheduledAgentJob, scheduled_run.job_id)
            user = await db.scalar(select(User).where(User.uid == job.uid, User.is_deleted == 0).with_for_update()) if job else None
            if job is None or user is None:
                scheduled_run.status = "cancelled"
                scheduled_run.error_message = "任务已删除、停用或用户不存在"
                await db.commit()
                return scheduled_run.to_dict()
            if scheduled_run.trigger == "scheduled" and (not job.enabled or job.deleted_at is not None):
                scheduled_run.status = "cancelled"
                scheduled_run.error_message = "任务已停用或删除"
                await db.commit()
                return scheduled_run.to_dict()
            await _validate_project(scheduled_run.project_id, user, db)
            await _validate_agent(scheduled_run.agent_slug, user, db)
            intent = {
                "uid": str(user.uid),
                "agent_slug": scheduled_run.agent_slug,
                "thread_id": scheduled_run.thread_id,
                "idempotency_key": scheduled_run.id,
                "project_id": scheduled_run.project_id,
                "title": scheduled_run.session_title,
                "prompt": scheduled_run.prompt,
                "model_spec": scheduled_run.model_spec,
                "tool_approval_mode": scheduled_run.tool_approval_mode,
                "job_id": job.id,
            }
            await db.commit()

        async with pg_manager.get_async_session_context() as input_db:
            accepted = await create_thread(
                db=input_db,
                scope=ActorScope(uid=intent["uid"], app_id=None),
                agent_slug=intent["agent_slug"],
                thread_id=intent["thread_id"],
                idempotency_key=intent["idempotency_key"],
                project_id=intent["project_id"],
                title=intent["title"],
                messages=[build_chat_input_message(intent["prompt"])],
                model_spec=intent["model_spec"],
                tool_approval_mode=intent["tool_approval_mode"],
                source=SCHEDULED_AGENT_SOURCE,
                channel="worker",
                external_id=scheduled_run_id,
                origin_metadata={"scheduled_job_id": intent["job_id"], "scheduled_run_id": scheduled_run_id},
            )

        async with pg_manager.get_async_session_context() as result_db:
            current = await result_db.scalar(select(ScheduledAgentRun).where(ScheduledAgentRun.id == scheduled_run_id).with_for_update())
            if current is None:
                return None
            current.input_id = accepted["input_id"]
            current.status = "submitted"
            current.error_message = None
            current_job = await result_db.get(ScheduledAgentJob, current.job_id)
            if current_job is not None:
                current_job.updated_at = utc_now()
            await result_db.commit()
            input_item, run = await ScheduledAgentRepository(result_db).get_input_and_run(current.input_id)
            return _execution_to_dict(current, input_item, run)
    except DomainError as exc:
        settled = await _settle_dispatch_error(scheduled_run_id, exc, terminal=True)
        if settled is None:
            raise
        return settled
    except Exception as exc:
        settled = await _settle_dispatch_error(scheduled_run_id, exc, terminal=False)
        if settled is not None and settled["status"] == "submitted":
            return settled
        raise


async def recover_scheduled_dispatches(*, limit: int = 100) -> int:
    """恢复 worker 中断后遗留的定时触发意图。"""
    async with pg_manager.get_async_session_context() as db:
        records = await ScheduledAgentRepository(db).list_dispatching_runs(
            before=utc_now() - timedelta(seconds=30),
            limit=limit,
        )
    recovered = 0
    for record in records:
        try:
            await dispatch_scheduled_run(scheduled_run_id=record.id)
            recovered += 1
        except Exception:
            logger.opt(exception=True).error(f"恢复定时任务触发失败: scheduled_run={record.id}")
    return recovered


async def _claim_due_run(*, db: AsyncSession, now: datetime) -> ScheduledAgentRun | None:
    """在一个事务中领取到期任务、推进计划并创建触发意图。"""
    repo = ScheduledAgentRepository(db)
    while job := await repo.claim_due_job(now=now):
        scheduled_for = job.next_run_at
        try:
            job.next_run_at = next_run_at(job.cron_expression, job.timezone, now)
        except (CroniterError, OverflowError, TypeError, ValueError, ZoneInfoNotFoundError):
            job.enabled = False
            job.updated_at = now
            await db.commit()
            logger.opt(exception=True).error(f"已停用无法计算下次触发时间的定时任务: scheduled_job={job.id}")
            continue
        job.updated_at = now
        run = await _create_run_record(
            repo=repo,
            job=job,
            trigger="scheduled",
            occurrence_key=f"scheduled:{scheduled_for.isoformat()}",
            scheduled_for=scheduled_for,
        )
        await db.commit()
        return run
    return None


async def claim_and_dispatch_due_jobs(*, limit: int = 20) -> int:
    """批量领取到期任务并提交对应 AgentRun。"""
    count = 0
    for _ in range(max(0, limit)):
        async with pg_manager.get_async_session_context() as db:
            run = await _claim_due_run(db=db, now=utc_now())
            if run is None:
                break
            run_id = run.id
        if run.status == "dispatching":
            try:
                await dispatch_scheduled_run(scheduled_run_id=run_id)
            except Exception:
                logger.opt(exception=True).error(f"提交到期定时任务失败: scheduled_run={run_id}")
        count += 1
    return count
