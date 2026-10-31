"""Thread 输入的持久接收、配置冻结和幂等回执。"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.input_messages import AgentRunInputMessage, serialize_input_message
from yuxi.modules.agents.models.inputs import AgentInputReceipt, queue_resume_payload
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.receipts import input_intent_hash, require_idempotency_key, require_receipt_replay
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.runtime.agent_backends import AgentBackendNotFoundError, get_agent_backend
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.attachments import serialize_attachment, stage_input_attachments
from yuxi.modules.agents.services.cooperation_notifications import notify_user_intervention
from yuxi.modules.agents.services.input_config import (
    resolve_agent_context_snapshot,
    resolve_agent_run_model_spec,
    resolve_agent_run_tool_approval_mode,
)
from yuxi.modules.agents.services.public_items import serialize_public_items
from yuxi.modules.agents.services.scheduler import dispatch_next_input
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.paths import ensure_bound_user_workdir
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.modules.workspace.services.bindings import resolve_session_workdir_binding
from yuxi.modules.workspace.services.projects import create_implicit_project
from yuxi.shared.errors import InvalidInputError, ResourceNotFoundError, StateConflictError
from yuxi.shared.hashing import hash_id


def thread_id_for_creation(scope: ActorScope, idempotency_key: str) -> str:
    """以接收身份和幂等键生成跨 Thread/Session 别名稳定的 Thread ID。"""
    require_idempotency_key(idempotency_key)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"yuxi-thread:{scope.uid}:{scope.app_id}:{idempotency_key}"))


async def create_thread(
    *,
    db: AsyncSession,
    scope: ActorScope,
    agent_slug: str,
    thread_id: str,
    idempotency_key: str,
    project_id: str | None = None,
    title: str | None = None,
    messages: list[AgentRunInputMessage] | None = None,
    attachment_file_ids: list[str] | None = None,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    source: str = "public_api",
    channel: str = "api",
    external_id: str | None = None,
    origin_metadata: dict | None = None,
) -> dict:
    """在一个事务中创建 Thread，并可同时接收与领取首条输入。"""
    require_idempotency_key(idempotency_key)
    input_messages = list(messages or [])
    file_ids = list(attachment_file_ids or [])
    if file_ids and not input_messages:
        raise InvalidInputError("附件必须随非空输入提交")
    intent_hash = input_intent_hash(
        "yuxi.session.create",
        agent_slug,
        project_id,
        title,
        model_spec,
        tool_approval_mode,
        external_id,
        origin_metadata or {},
        [_message_intent(item) for item in input_messages],
        file_ids,
    )
    receipt_repo = AgentInputReceiptRepository(db)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, "yuxi.session.create", intent_hash)
        return _accepted(existing)

    user = await db.scalar(select(User).where(User.uid == scope.uid, User.is_deleted == 0))
    if user is None:
        raise ResourceNotFoundError("用户不存在")
    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=user, for_key_share=True)
    if agent_item is None:
        raise ResourceNotFoundError("智能体不存在")

    existing_thread = await SessionRepository(db).get_session_by_thread_id(thread_id)
    if existing_thread is not None:
        existing = await receipt_repo.get_for_scope(
            uid=scope.uid,
            app_id=scope.app_id,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            require_receipt_replay(existing, "yuxi.session.create", intent_hash)
            return _accepted(existing)
        raise StateConflictError("Thread ID 已存在")
    try:
        backend = get_agent_backend(agent_item.backend_id)
    except AgentBackendNotFoundError as exc:
        raise ResourceNotFoundError(str(exc)) from exc
    config_snapshot = await resolve_agent_context_snapshot(model_spec, tool_approval_mode, agent_item, backend, db)
    thread_metadata = {"source": source, "channel": channel}
    try:
        async with db.begin_nested():
            project = (
                await ProjectRepository(db).lock_active_for_user(project_id, scope.uid)
                if project_id
                else await create_implicit_project(uid=scope.uid, db=db)
            )
            if project is None:
                raise ResourceNotFoundError("Project 不存在或不可访问")
            agent_session = await SessionRepository(db).add_session(
                uid=scope.uid,
                agent_id=agent_slug,
                title=title,
                thread_id=thread_id,
                metadata=thread_metadata,
                config_snapshot=config_snapshot,
                project_id=project.id,
                creation_request_id=hash_id("thread:", f"{scope.uid}:{scope.app_id}:{idempotency_key}", length=64),
                app_id=scope.app_id,
            )
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        existing = await receipt_repo.get_for_scope(
            uid=scope.uid,
            app_id=scope.app_id,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            require_receipt_replay(existing, "yuxi.session.create", intent_hash)
            return _accepted(existing)
        raise StateConflictError("Thread 创建冲突") from exc
    binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=scope.uid, db=db, project=project)
    if input_messages:
        receipt = await accept_locked(
            db=db,
            scope=scope,
            agent_session=agent_session,
            idempotency_key=idempotency_key,
            event_type="yuxi.session.create",
            intent_hash=intent_hash,
            mode="follow_up",
            messages=input_messages,
            attachment_file_ids=file_ids,
            model_spec=model_spec,
            tool_approval_mode=tool_approval_mode,
            source=source,
            channel=channel,
            external_id=external_id,
            origin_metadata=origin_metadata,
        )
    else:
        receipt = await receipt_repo.create(
            receipt_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            uid=scope.uid,
            app_id=scope.app_id,
            thread_id=thread_id,
            event_type="yuxi.session.create",
            intent_hash=intent_hash,
        )
    await db.commit()
    if binding.materialize_managed:
        ensure_bound_user_workdir(binding.uid, binding.workdir_path)
    await dispatch_next_input(uid=scope.uid, agent_slug=agent_session.agent_id, thread_id=agent_session.thread_id)
    return _accepted(receipt)


async def accept_message(
    *,
    db: AsyncSession,
    scope: ActorScope,
    thread_id: str,
    idempotency_key: str,
    mode: Literal["follow_up", "steer"] | None,
    messages: list[AgentRunInputMessage],
    attachment_file_ids: list[str] | None = None,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    source: str = "public_api",
    channel: str = "api",
    external_id: str | None = None,
    origin_metadata: dict | None = None,
) -> dict:
    """锁定 Thread 后接收消息，提交新事实，再投递已领取的 Run。"""
    require_idempotency_key(idempotency_key)
    if not messages:
        raise InvalidInputError("输入消息不能为空")
    if mode not in {None, "follow_up", "steer"}:
        raise InvalidInputError("不支持的输入模式")
    intent_hash = input_intent_hash(
        "agent.session.input.message",
        mode,
        model_spec,
        tool_approval_mode,
        external_id,
        origin_metadata or {},
        [_message_intent(item) for item in messages],
        list(attachment_file_ids or []),
    )
    receipt_repo = AgentInputReceiptRepository(db)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, "agent.session.input.message", intent_hash)
        return _accepted(existing)

    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, "agent.session.input.message", intent_hash)
        return _accepted(existing)

    resume_queue = False
    if source == "public_api":
        from yuxi.modules.agents.services.cooperation import resume_tree_for_user_input

        resume_queue = await resume_tree_for_user_input(db=db, agent_session=agent_session)
        resume_queue = resume_queue or agent_session.queue_paused
    receipt = await accept_locked(
        db=db,
        scope=scope,
        agent_session=agent_session,
        idempotency_key=idempotency_key,
        event_type="agent.session.input.message",
        intent_hash=intent_hash,
        frozen_payload=queue_resume_payload() if resume_queue else None,
        mode=mode,
        messages=messages,
        attachment_file_ids=attachment_file_ids,
        model_spec=model_spec,
        tool_approval_mode=tool_approval_mode,
        source=source,
        channel=channel,
        external_id=external_id,
        origin_metadata=origin_metadata,
    )
    if resume_queue:
        agent_session.queue_paused = False
    await db.commit()
    await dispatch_next_input(uid=scope.uid, agent_slug=agent_session.agent_id, thread_id=agent_session.thread_id)
    return _accepted(receipt)


async def get_receipt_snapshot(*, db: AsyncSession, scope: ActorScope, thread_id: str, idempotency_key: str) -> dict:
    """响应丢失时按原幂等键读取接收事实，消费归属继续从 Input 查询。"""
    await require_thread(db=db, scope=scope, thread_id=thread_id)
    receipt = await AgentInputReceiptRepository(db).get_for_scope(
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        idempotency_key=idempotency_key,
    )
    if receipt is None:
        raise ResourceNotFoundError("回执不存在")
    return _accepted(receipt)


async def get_input_snapshot(*, db: AsyncSession, scope: ActorScope, thread_id: str, input_id: str) -> dict:
    """按完整作用域回读 Input 的接收与消费事实。"""
    await require_thread(db=db, scope=scope, thread_id=thread_id)
    input_item = await AgentInputRepository(db).get_for_scope(input_id=input_id, thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    if input_item is None:
        raise ResourceNotFoundError("Input 不存在")
    messages = await AgentInputRepository(db).list_messages(input_id)
    attachments = await AttachmentRepository(db).for_messages([message.id for message in messages])
    input_attachments = await AttachmentRepository(db).list_for_input(input_id)
    preparation = await AttachmentRepository(db).statuses_for_inputs([input_id])
    return {
        "input_id": input_item.id,
        "thread_id": thread_id,
        "kind": input_item.kind,
        "status": input_item.status,
        "turn_id": input_item.turn_id,
        "run_id": input_item.consumed_run_id,
        "received_seq": input_item.received_seq,
        "messages": input_item.messages,
        "attachment_file_ids": input_item.attachment_file_ids,
        "attachments": [serialize_attachment(item, thread_id=thread_id) for item in input_attachments],
        "received_at": input_item.created_at,
        "items": [
            item
            for message in messages
            for item in serialize_public_items(
                message,
                None,
                None,
                attachments=[serialize_attachment(item, thread_id=thread_id) for item in attachments[message.id]],
            )
        ],
        **preparation[input_id],
    }


async def accept_locked(
    *,
    db: AsyncSession,
    scope: ActorScope,
    agent_session: Session,
    idempotency_key: str,
    event_type: str,
    intent_hash: str,
    mode: Literal["follow_up", "steer"] | None,
    messages: list[AgentRunInputMessage],
    model_spec: str | None,
    tool_approval_mode: str | None,
    source: str,
    channel: str,
    external_id: str | None,
    origin_metadata: dict | None,
    frozen_payload: dict | None = None,
    attachment_file_ids: list[str] | None = None,
) -> AgentInputReceipt:
    """在调用方事务与 Thread 锁内保存原始 Input、附件与接收回执。"""
    if agent_session.status != "active":
        raise StateConflictError("Thread 已归档")
    turn_repo = AgentTurnRepository(db)
    active_turn = await turn_repo.lock_active_for_thread(thread_id=agent_session.thread_id, uid=scope.uid, app_id=scope.app_id)
    cooperation_wait = (
        active_turn is not None and active_turn.status == "waiting" and (active_turn.waitpoint or {}).get("kind") == "cooperation"
    )
    if active_turn is not None and (active_turn.status == "cancelling" or (active_turn.status == "waiting" and not cooperation_wait)):
        raise StateConflictError("当前 Turn 正在等待控制输入或取消清理")

    mode = mode or ("follow_up" if cooperation_wait or active_turn is None else "steer")
    if cooperation_wait and mode == "steer":
        raise StateConflictError("协作等待中请提交排队输入或取消当前 Turn")
    input_repo = AgentInputRepository(db)
    if mode == "steer" and (model_spec is not None or tool_approval_mode is not None):
        raise InvalidInputError("Steer 不指定模型或审批配置")
    user = await db.scalar(select(User).where(User.uid == scope.uid, User.is_deleted == 0))
    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_session.agent_id, user=user)
    if agent_item is None:
        raise ResourceNotFoundError("智能体不存在")
    if agent_session.config_snapshot is None:
        raise ValueError("Session 缺少配置快照")
    snapshot = deepcopy(agent_session.config_snapshot)
    if mode == "steer" and active_turn is not None:
        current = await AgentRunRepository(db).get_run(active_turn.current_run_id)
        snapshot = deepcopy(current.input_payload["context_snapshot"])
    else:
        # 助理可先建入口再配置模型；首次输入补齐默认值，之后沿用会话选择。
        if not snapshot.get("model") and model_spec is None:
            defaults = await resolve_agent_context_snapshot(None, None, agent_item, get_agent_backend(agent_item.backend_id), db)
            snapshot["model"] = defaults["model"]
            agent_session.config_snapshot = deepcopy(snapshot)
        if model_spec is not None:
            snapshot["model"] = await resolve_agent_run_model_spec(model_spec, None, db)
        if tool_approval_mode is not None:
            snapshot["tool_approval_mode"] = resolve_agent_run_tool_approval_mode(tool_approval_mode, None)
    input_payload = {**(frozen_payload or {}), "context_snapshot": snapshot}

    input_item = await input_repo.create(
        input_id=str(uuid.uuid4()),
        thread_id=agent_session.thread_id,
        uid=scope.uid,
        app_id=scope.app_id,
        api_key_id=scope.api_key_id,
        agent_slug=agent_session.agent_id,
        kind=mode,
        messages=[serialize_input_message(message) for message in messages],
        attachment_file_ids=list(attachment_file_ids or []),
        input_payload=input_payload,
        source=source,
        channel=channel,
        external_id=external_id,
        origin_metadata=origin_metadata,
    )

    receipt = await AgentInputReceiptRepository(db).create(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=agent_session.thread_id,
        event_type=event_type,
        intent_hash=intent_hash,
        input_id=input_item.id,
    )
    if input_item.attachment_file_ids:
        await stage_input_attachments(db=db, scope=scope, input_item=input_item, file_ids=input_item.attachment_file_ids)

    if source != "cooperation":
        await notify_user_intervention(db, agent_session, key=receipt.id, action=mode)
    return receipt


def _message_intent(message: AgentRunInputMessage) -> dict:
    """提取用于幂等核对的规范化消息意图。"""
    return {
        "content": message.content,
        "message_type": message.message_type,
        "image_content": message.image_content,
        "raw_message": message.raw_message(),
        "metadata": message.extra_metadata,
    }


def _accepted(receipt: AgentInputReceipt) -> dict:
    """返回接收时固定的命令目标，执行归属由 Input 查询。"""
    return {
        "event_id": receipt.id,
        "input_id": receipt.input_id,
        "thread_id": receipt.thread_id,
        "turn_id": receipt.turn_id,
        "run_id": receipt.run_id,
        "status": "accepted",
    }
