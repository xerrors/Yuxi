"""统一的 AgentRun 消息提交应用服务。

Web Chat、Agent Call 和评估入口只在路由/适配层处理各自的输入输出协议，
实际的 AgentRunRequest 入队、Conversation 绑定和提交后派发都从这里进入。
Resume 与 Subagent 保留各自的特殊生命周期，不经过本服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.services.agent_request_queue_service import (
    DispatchResult,
    request_view,
    validate_queue_policy,
    get_thread_conversation,
    is_steerable_message_run,
    dispatch_ready_head,
    queue_conflict,
    REQUEST_STATUS_QUEUED,
    REQUEST_STATUS_REJECTED,
    DELIVERY_STATUS_QUEUED,
    DELIVERY_STATUS_REJECTED,
)
from yuxi.services.agent_run_service import create_agent_run_input_message, enqueue_agent_run, resolve_agent_run_config
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.workspace.paths import ensure_bound_user_workdir
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.project_service import create_implicit_project
from yuxi.services.workdir_service import WorkdirBinding, resolve_conversation_workdir_binding
from yuxi.storage.postgres.models_business import AgentRunRequest, User


@dataclass(frozen=True)
class RunOrigin:
    """描述一次 Run 请求的入口来源与传输通道。"""

    source: str
    channel: str
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRequestInput:
    """普通 Agent 请求的入口输入。"""

    agent_slug: str
    thread_id: str
    request_id: str
    input_message: AgentRunInputMessage
    origin: RunOrigin
    request_metadata: dict[str, Any] = field(default_factory=dict)
    model_spec: str | None = None
    tool_approval_mode: str | None = None
    queue_policy: str = "enqueue"
    create_conversation: bool = False
    conversation_title: str | None = None
    conversation_project_id: str | None = None


async def submit_agent_request(
    *,
    request_input: AgentRequestInput,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    """校验作用域、写入 Request 并在提交后投递消息型 AgentRun。

    ``create_conversation`` 仅用于没有显式 Thread 的外部入口；普通 Web Chat
    必须复用已经创建的 Conversation。不同入口的协议适配不应绕过这里。
    """

    origin = request_input.origin
    if not origin.source.strip() or not origin.channel.strip():
        raise HTTPException(status_code=422, detail="Run origin source/channel 不能为空")
    if len(origin.source) > 32:
        raise HTTPException(status_code=422, detail="Run origin source 不能超过 32 个字符")
    if len(origin.channel) > 32:
        raise HTTPException(status_code=422, detail="Run origin channel 不能超过 32 个字符")
    external_id = str(origin.external_id).strip() if origin.external_id is not None else None
    if external_id == "":
        external_id = None
    origin_metadata = {
        key: value for key, value in origin.metadata.items() if key not in {"source", "channel", "external_id"}
    }

    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(
        slug=request_input.agent_slug,
        user=current_user,
        kind="main",
    )
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    existing_request = await AgentRunRequestRepository(db).get_by_request_id(request_input.request_id)
    existing_run = (
        None if existing_request else await AgentRunRepository(db).get_run_by_request_id(request_input.request_id)
    )
    if existing_run and not existing_request:
        if existing_run.uid != str(current_user.uid):
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if existing_run.agent_slug != agent_item.slug or existing_run.run_type != "chat":
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if request_input.thread_id and existing_run.conversation_thread_id != request_input.thread_id:
            raise HTTPException(status_code=409, detail="request_id 冲突")
        return {
            "request_id": request_input.request_id,
            "status": existing_run.status,
            "queue_policy": request_input.queue_policy,
            "queue_position": 0,
            "message_id": existing_run.input_message_id,
            "run_id": existing_run.id,
            "stream_url": f"/api/agent/runs/{existing_run.id}/events",
            "request_events_url": None,
            "thread_id": existing_run.conversation_thread_id,
        }
    if existing_request:
        normalized_input = replace(request_input, origin=replace(origin, external_id=external_id))
        _validate_request_scope(existing_request, request_input=normalized_input, uid=str(current_user.uid))
        conversation = await ConversationRepository(db).get_conversation_by_thread_id(
            existing_request.conversation_thread_id
        )
        if (
            conversation is None
            or conversation.uid != str(current_user.uid)
            or conversation.status == "deleted"
            or conversation.agent_id != request_input.agent_slug
        ):
            raise HTTPException(status_code=404, detail="对话线程不存在")
        project = await ProjectRepository(db).get_for_user(conversation.project_id, str(current_user.uid))
        if project is None or project.status != "active":
            raise HTTPException(status_code=404, detail="Project 不存在或不可访问")
        return await request_view(repo=AgentRunRequestRepository(db), request=existing_request)

    agent_backend = agent_manager.get_agent(agent_item.backend_id)
    if not agent_backend:
        raise HTTPException(status_code=404, detail=f"智能体后端 {agent_item.backend_id} 不存在")

    conversation_repo = ConversationRepository(db)
    project = None
    conversation = await conversation_repo.get_conversation_by_thread_id(request_input.thread_id)
    if not conversation:
        if not request_input.create_conversation:
            raise HTTPException(status_code=404, detail="对话线程不存在")
        try:
            async with db.begin_nested():
                project = None
                if request_input.conversation_project_id:
                    project = await ProjectRepository(db).lock_active_for_user(
                        request_input.conversation_project_id,
                        str(current_user.uid),
                    )
                    if project is None:
                        raise HTTPException(status_code=404, detail="Project 不存在或不可访问")
                else:
                    project = await create_implicit_project(
                        uid=str(current_user.uid),
                        db=db,
                    )
                conversation = await conversation_repo.add_conversation(
                    uid=str(current_user.uid),
                    agent_id=agent_item.slug,
                    title=request_input.conversation_title,
                    thread_id=request_input.thread_id,
                    metadata={
                        **origin_metadata,
                        "source": origin.source,
                        "channel": origin.channel,
                    },
                    project_id=project.id,
                )
        except IntegrityError:
            conversation = await conversation_repo.get_conversation_by_thread_id(request_input.thread_id)
            if not conversation:
                raise

    request_metadata = dict(request_input.request_metadata or {})
    request_metadata["channel"] = origin.channel
    for key, value in origin_metadata.items():
        if key in {"source", "channel"}:
            continue
        request_metadata.setdefault(key, value)

    binding_project = project if project is not None and str(project.id) == str(conversation.project_id) else None
    workdir_binding = await resolve_conversation_workdir_binding(
        conversation=conversation,
        uid=str(current_user.uid),
        db=db,
        project=binding_project,
    )
    request_input = replace(
        request_input,
        origin=replace(origin, external_id=external_id, metadata=origin_metadata),
        request_metadata=request_metadata,
    )
    request, dispatch = await _persist_request(
        db=db,
        request_input=request_input,
        current_user=current_user,
        agent_item=agent_item,
        agent_backend=agent_backend,
        workdir_binding=workdir_binding,
    )
    response = await request_view(repo=AgentRunRequestRepository(db), request=request)
    await db.commit()
    if workdir_binding.materialize_managed:
        ensure_bound_user_workdir(workdir_binding.uid, workdir_binding.workdir_path)
    if dispatch is not None:
        await enqueue_agent_run(dispatch.run_id)
    return response


async def _persist_request(
    *,
    db: AsyncSession,
    request_input: AgentRequestInput,
    current_user: User,
    agent_item: Any,
    agent_backend: Any,
    workdir_binding: WorkdirBinding | None = None,
) -> tuple[AgentRunRequest, DispatchResult | None]:
    """保存请求并返回本事务实际派发的队头，供提交后投递。"""
    request_id = request_input.request_id
    uid = current_user.uid
    agent_slug = request_input.agent_slug
    thread_id = request_input.thread_id
    source, channel = request_input.origin.source, request_input.origin.channel
    external_id = request_input.origin.external_id
    origin_metadata = request_input.origin.metadata
    input_message = request_input.input_message
    model_spec, tool_approval_mode = request_input.model_spec, request_input.tool_approval_mode
    meta = request_input.request_metadata
    policy = validate_queue_policy(request_input.queue_policy)
    if policy == "steer" and source not in {"chat", "channel"}:
        raise HTTPException(status_code=422, detail="queue_policy 'steer' 仅支持主会话 Chat/Channel")
    meta = meta or {}
    uid_str = str(uid)
    repo = AgentRunRequestRepository(db)

    async def existing_request(binding: WorkdirBinding | None = None) -> AgentRunRequest | None:
        """幂等：相同 request_id 已存在时返回既有 request/run 视图，不存在返回 None。"""
        if binding is not None and (binding.uid != uid_str or binding.thread_id != thread_id):
            raise RuntimeError("传入的 Workdir 绑定与请求作用域不一致")
        existing = await repo.get_by_request_id(request_id)
        if not existing:
            return None
        _validate_request_scope(existing, request_input=request_input, uid=uid_str)
        return existing

    if result := await existing_request(workdir_binding):
        return result, None

    conversation = await get_thread_conversation(
        db=db,
        uid=uid_str,
        agent_slug=agent_slug,
        thread_id=thread_id,
        lock=True,
    )
    if workdir_binding is None:
        workdir_binding = await resolve_conversation_workdir_binding(
            conversation=conversation,
            uid=uid_str,
            db=db,
        )
    elif (
        workdir_binding.uid != uid_str
        or workdir_binding.conversation_id != conversation.id
        or workdir_binding.thread_id != conversation.thread_id
        or workdir_binding.project_id != conversation.project_id
    ):
        raise RuntimeError("传入的 Workdir 绑定与 Conversation 不一致")
    if result := await existing_request(workdir_binding):
        return result, None
    existing_requests = await repo.list_queued(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    existing_head = existing_requests[0] if existing_requests else None
    active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        uid=uid_str,
    )
    latest_run = await AgentRunRepository(db).get_latest_chat_or_resume_run(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if latest_run is not None and latest_run.status == "interrupted":
        raise queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
    if policy == "steer" and active_run is not None and not await is_steerable_message_run(db=db, run=active_run):
        raise queue_conflict("run_not_steerable", "当前运行不支持引导")
    if policy == "steer" and await repo.get_pending_steer(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    ):
        raise queue_conflict("steer_already_pending", "线程已有等待执行的引导请求")

    # reject 表示“不能立即成为并派发 FIFO 队头就拒绝”。
    reject_without_immediate_dispatch = policy == "reject" and (active_run is not None or existing_head is not None)
    if reject_without_immediate_dispatch:
        request_status = REQUEST_STATUS_REJECTED
        delivery_status = DELIVERY_STATUS_REJECTED
        input_payload = {}
    else:
        request_status = REQUEST_STATUS_QUEUED
        delivery_status = DELIVERY_STATUS_QUEUED
        conversation_model_spec = (conversation.extra_metadata or {}).get("model_spec")
        requested_model_spec = (
            model_spec if isinstance(model_spec, str) and model_spec.strip() else conversation_model_spec
        )
        resolved_model_spec, resolved_tool_approval_mode = await resolve_agent_run_config(
            requested_model_spec, tool_approval_mode, agent_item, agent_backend, db
        )
        input_payload = {
            "model_spec": resolved_model_spec,
            "tool_approval_mode": resolved_tool_approval_mode,
        }

    run_input_message = input_message.with_metadata(
        _build_message_metadata(request_id=request_id, source=source, input_message=input_message, meta=meta)
    )
    try:
        async with db.begin_nested():
            attachment_file_ids = _normalize_attachment_file_ids(meta.get("attachment_file_ids"))
            if not reject_without_immediate_dispatch and attachment_file_ids:
                bound_attachments = await ConversationRepository(db).bind_attachments_to_request(
                    conversation.id,
                    request_id,
                    attachment_file_ids,
                )
                bound_ids = {str(item.get("file_id")) for item in bound_attachments}
                missing_ids = [file_id for file_id in attachment_file_ids if file_id not in bound_ids]
                if missing_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=f"附件不存在、已被使用或已被删除: {', '.join(missing_ids)}",
                    )
            persisted_message = await create_agent_run_input_message(
                db=db,
                conversation_id=conversation.id,
                request_id=request_id,
                input_message=run_input_message,
                delivery_status=delivery_status,
            )
            persisted_request = await repo.create(
                request_id=request_id,
                uid=uid_str,
                agent_slug=agent_slug,
                conversation_thread_id=thread_id,
                source=source,
                channel=channel,
                external_id=external_id,
                origin_metadata=origin_metadata,
                queue_policy=policy,
                input_message_id=persisted_message.id,
                input_payload=input_payload,
                status=request_status,
            )
    except IntegrityError:
        if result := await existing_request(workdir_binding):
            return result, None
        raise

    dispatched = None
    if not reject_without_immediate_dispatch:
        if policy != "reject":
            await ConversationRepository(db).set_model_spec(conversation, resolved_model_spec)
        dispatched = await dispatch_ready_head(
            db=db,
            uid=uid_str,
            agent_slug=agent_slug,
            thread_id=thread_id,
            workdir_binding=workdir_binding,
            expected_request_id=request_id if policy == "reject" else None,
        )
        if dispatched and dispatched.request_id == request_id:
            if policy == "reject":
                await ConversationRepository(db).set_model_spec(conversation, resolved_model_spec)
            return persisted_request, dispatched

        if policy == "reject":
            persisted_request.status = REQUEST_STATUS_REJECTED
            persisted_request.input_payload = {}
            persisted_request.updated_at = utc_now_naive()
            persisted_message.delivery_status = DELIVERY_STATUS_REJECTED
            await db.flush()
    return persisted_request, dispatched


def _validate_request_scope(request: AgentRunRequest, *, request_input: AgentRequestInput, uid: str) -> None:
    """相同 ID 只允许重放同一不可变请求作用域。"""
    origin = request_input.origin
    expected_scope = (
        str(uid),
        request_input.agent_slug,
        request_input.thread_id,
        origin.source,
        origin.channel,
        origin.external_id,
    )
    actual_scope = (
        request.uid,
        request.agent_slug,
        request.conversation_thread_id,
        request.source,
        request.channel,
        request.external_id,
    )
    if actual_scope != expected_scope:
        raise queue_conflict("request_id_conflict", "request_id 已用于其他请求作用域")


def _build_message_metadata(
    *, request_id: str, source: str, input_message: AgentRunInputMessage, meta: dict
) -> dict[str, Any]:
    """构建 Message.extra_metadata：request_id + source + raw_message + 附加上下文。"""
    metadata: dict[str, Any] = {"request_id": request_id}
    if source:
        metadata["source"] = source
    if channel := meta.get("channel"):
        metadata["channel"] = channel
    if raw_message := input_message.raw_message():
        metadata["raw_message"] = raw_message
    if attachment_file_ids := meta.get("attachment_file_ids"):
        metadata["attachment_file_ids"] = attachment_file_ids
    if isinstance(meta.get("agent_invocation_meta"), dict):
        metadata["agent_invocation_meta"] = meta["agent_invocation_meta"]
    if meta.get("tool_approval_mode") is not None:
        metadata["tool_approval_mode"] = meta["tool_approval_mode"]
    return metadata


def _normalize_attachment_file_ids(value: object) -> list[str]:
    """规范化请求附件 ID，保持原始顺序并去重。"""
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for file_id in value:
        current = str(file_id).strip()
        if current and current not in seen:
            seen.add(current)
            normalized.append(current)
    return normalized
