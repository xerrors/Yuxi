"""Agents Public API 对现有 Agent Request/Run 用例的薄业务适配。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agents.input_receipt import AgentSessionInputReceiptRepository
from yuxi.repositories.agents.turn import AgentTurnRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.agent_request_queue_service import stream_request_events
from yuxi.services.agent_request_service import (
    AgentRequestInput,
    RunOrigin,
    get_agent_request_result,
    submit_agent_request,
)
from yuxi.services.agent_run_service import get_agent_run_result, stream_agent_run_events
from yuxi.services.conversation_service import create_thread_view
from yuxi.services.input_message_service import (
    build_chat_input_message,
    build_chat_input_message_from_openai_content,
    extract_image_contents,
    normalize_image_contents,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, APIKey, User
from yuxi.utils.datetime_utils import format_utc_datetime
from yuxi.utils.hash_utils import hash_id
from yuxi.utils.sse_utils import format_sse


async def resolve_public_user(*, owner: User, api_key: APIKey, end_user_id: str | None, db: AsyncSession) -> User:
    """只在 Public API 边界解析 APP 声明的终端用户身份。"""
    if end_user_id is None:
        return owner
    if not end_user_id or end_user_id != end_user_id.strip() or len(end_user_id) > 128:
        raise HTTPException(status_code=422, detail="X-End-User-Id 必须为 1 至 128 个无首尾空白的字符")

    user = await UserRepository(db).get_or_create_public_end_user(
        owner=owner, app_id=api_key.app_id, end_user_id=end_user_id
    )
    if user.is_deleted:
        raise HTTPException(status_code=403, detail="终端用户已停用")
    await db.commit()
    return user


async def list_public_agents(*, user: User, db: AsyncSession) -> list[Agent]:
    """列出当前 Key 主体可调用的主 Agent。"""
    return await AgentRepository(db).list_visible(user=user)


async def get_public_agent(*, agent_id: str, user: User, db: AsyncSession) -> Agent:
    """按后端可见性读取一个可调用 Agent。"""
    agent = await AgentRepository(db).get_visible_by_slug(slug=agent_id, user=user, kind="main")
    if agent is None:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return agent


async def has_legacy_public_creation(
    *, idempotency_key: str, user: User, api_key: APIKey | None, db: AsyncSession
) -> bool:
    """识别迁移前由首条输入创建的 Session，以保留已有幂等键。"""
    app_id = api_key.app_id if api_key else None
    legacy_identity = f"{user.uid}:{app_id}:new-session:{idempotency_key}"
    identity = json.dumps(
        [str(user.uid), app_id, "new-session", idempotency_key], ensure_ascii=False, separators=(",", ":")
    )
    for request_id, thread_id in (
        (hash_id("pubreq_", legacy_identity, length=64), hash_id("pubsess_", legacy_identity, length=64)),
        (hash_id("pubreq_", identity, length=64), hash_id("pubsess_", identity, length=64)),
    ):
        request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
        if request is None:
            continue
        if (
            request.uid != str(user.uid)
            or request.app_id != app_id
            or request.conversation_thread_id != thread_id
            or request.source != "public_api"
        ):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他创建意图")
        return True
    return False


async def create_public_session(
    *,
    agent_id: str,
    idempotency_key: str,
    user: User,
    api_key: APIKey | None,
    project_id: str | None,
    title: str | None,
    model_spec: str | None,
    tool_approval_mode: str | None,
    input_intent: list[dict] | None = None,
    db: AsyncSession,
) -> dict:
    """复用 Conversation 用例建立可由 Public 首条事件继续的空 Session。"""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key 长度必须为 1 至 128")
    app_id = api_key.app_id if api_key else None
    identity = json.dumps([str(user.uid), app_id, "new-session", idempotency_key], separators=(",", ":"))
    creation_request_id = hash_id("pubsess_", identity, length=64)
    intent_hash = _public_creation_intent_hash(
        agent_id, project_id, title, model_spec, tool_approval_mode, input_intent
    )
    existing = await ConversationRepository(db).get_conversation_by_creation_request_id(
        str(user.uid), creation_request_id
    )
    if existing is not None and existing.app_id != app_id:
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 APP 来源")
    legacy_empty = (
        existing is not None
        and (existing.extra_metadata or {}).get("public_creation_intent") is None
        and input_intent is None
        and existing.agent_id == agent_id
        and existing.title == (title or "新的对话")
        and (existing.extra_metadata or {}).get("source") == "public_api"
        and (existing.extra_metadata or {}).get("app_id") == app_id
        and (existing.extra_metadata or {}).get("model_spec") == model_spec
        and (existing.extra_metadata or {}).get("tool_approval_mode") == tool_approval_mode
    )
    if (
        existing is not None
        and not legacy_empty
        and (existing.extra_metadata or {}).get("public_creation_intent") != intent_hash
    ):
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 创建意图")
    metadata = {"source": "public_api", "channel": "api" if api_key else "web"}
    metadata["public_creation_intent"] = intent_hash
    if model_spec:
        metadata["model_spec"] = model_spec
    if tool_approval_mode:
        metadata["tool_approval_mode"] = tool_approval_mode
    try:
        created = await create_thread_view(
            agent_slug=agent_id,
            request_id=creation_request_id,
            title=title,
            metadata=metadata,
            project_id=project_id,
            db=db,
            current_uid=str(user.uid),
            app_id=app_id,
        )
    except (HTTPException, OSError, ValueError) as exc:
        if isinstance(exc, HTTPException) and exc.status_code != 400:
            raise
        stored = await ConversationRepository(db).get_conversation_by_creation_request_id(
            str(user.uid), creation_request_id
        )
        if stored is None or (
            not legacy_empty and (stored.extra_metadata or {}).get("public_creation_intent") != intent_hash
        ):
            raise
        raise HTTPException(
            status_code=503,
            detail={
                "code": "session_workdir_unavailable",
                "session_id": stored.thread_id,
                "message": "会话已持久化但工作目录暂不可用；请用相同 Idempotency-Key 重试",
            },
        ) from exc
    thread_id = created["id"]
    created_conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
    if not legacy_empty and (created_conversation.extra_metadata or {}).get("public_creation_intent") != intent_hash:
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 创建意图")
    return await get_public_session(thread_id=thread_id, user=user, app_id=app_id, db=db)


async def get_public_thread(*, thread_id: str, user: User, app_id: str | None, db: AsyncSession) -> dict:
    """只读取当前产品用户或 APP 命名空间的线程。"""
    scope = await get_public_thread_scope(thread_id=thread_id, user=user, app_id=app_id, db=db)

    latest = await AgentRunRequestRepository(db).get_latest_for_public_thread(
        uid=str(user.uid), app_id=app_id, thread_id=thread_id
    )
    request_result = (
        await get_agent_request_result(request_id=latest.request_id, current_uid=str(user.uid), app_id=app_id, db=db)
        if latest is not None
        else None
    )
    return {
        **scope,
        "status": request_result["status"] if request_result else "idle",
        "request_id": request_result["request_id"] if request_result else None,
        "run_id": request_result["run_id"] if request_result else None,
    }


async def get_public_thread_scope(*, thread_id: str, user: User, app_id: str | None, db: AsyncSession) -> dict:
    """仅校验线程可见性，不预载可能在等待锁时变化的执行状态。"""
    conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
    if (
        conversation is None
        or conversation.uid != str(user.uid)
        or conversation.status == "deleted"
        or conversation.app_id != app_id
    ):
        raise HTTPException(status_code=404, detail="线程不存在")
    project = await ProjectRepository(db).get_for_user(conversation.project_id, str(user.uid))
    if project is None or project.status != "active":
        raise HTTPException(status_code=404, detail="线程不存在")

    return {
        "thread_id": thread_id,
        "agent_id": conversation.agent_id,
        "project_id": conversation.project_id,
        "title": conversation.title,
    }


async def get_public_session(*, thread_id: str, user: User, app_id: str | None, db: AsyncSession) -> dict:
    """从独立 Turn 与其 Request/Run 投影当前 Session 状态。"""
    thread = await get_public_thread(thread_id=thread_id, user=user, app_id=app_id, db=db)
    run_repo = AgentRunRepository(db)
    active_run = await run_repo.get_active_run_by_thread_for_user(
        uid=str(user.uid), agent_slug=thread["agent_id"], conversation_thread_id=thread_id
    )
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=str(user.uid), agent_slug=thread["agent_id"], conversation_thread_id=thread_id
    )
    selected_turn_id = active_run.turn_id if active_run is not None else None
    if selected_turn_id is None and latest_run is not None and latest_run.status == "interrupted":
        selected_turn_id = latest_run.turn_id
    if selected_turn_id is None:
        queued = await AgentRunRequestRepository(db).get_first_queued(
            uid=str(user.uid), agent_slug=thread["agent_id"], conversation_thread_id=thread_id
        )
        selected_turn_id = queued.turn_id if queued is not None else None
    if selected_turn_id is None:
        latest_turn = await AgentTurnRepository(db).get_latest_for_thread(
            thread_id=thread_id, uid=str(user.uid), app_id=app_id
        )
        if latest_turn is not None:
            selected_turn_id = latest_turn.id

    if selected_turn_id is not None:
        selected = await get_public_turn(thread_id=thread_id, turn_id=selected_turn_id, user=user, app_id=app_id, db=db)
        status = selected["status"]
        session_status = (
            "requires_action"
            if status == "waiting"
            else "in_progress"
            if status in {"queued", "in_progress"}
            else "idle"
        )
        return {
            **thread,
            "status": session_status,
            "turn_id": selected["turn_id"],
            "request_id": selected["request_id"],
            "run_id": selected["run_id"],
        }

    # 未迁移的历史数据仍可由原有 Request/Run 视图读取。
    if latest_run is not None and latest_run.status == "interrupted":
        status = "requires_action"
    elif latest_run is not None and latest_run.status not in TERMINAL_RUN_STATUSES:
        status = "in_progress"
    elif thread["status"] in {"queued", "dispatched", "in_progress", "cancel_requested"}:
        status = "in_progress"
    elif thread["status"] in {"idle", "waiting", "completed", "failed", "cancelled", "rejected"}:
        queued = await AgentRunRequestRepository(db).has_queued_for_thread(
            uid=str(user.uid), agent_slug=thread["agent_id"], thread_id=thread_id
        )
        status = "in_progress" if queued else "idle"
    else:
        raise ValueError(f"未知的 Public Request 状态: {thread['status']}")
    return {**thread, "status": status}


async def get_public_turn(*, thread_id: str, turn_id: str, user: User, app_id: str | None, db: AsyncSession) -> dict:
    """只从明确关联的 Request/Run 生成本轮状态与最终结果。"""
    await get_public_thread(thread_id=thread_id, user=user, app_id=app_id, db=db)
    repo = AgentTurnRepository(db)
    turn = await repo.get(turn_id)
    if turn is None or (turn.conversation_thread_id, turn.uid, turn.app_id) != (thread_id, str(user.uid), app_id):
        raise HTTPException(status_code=404, detail="Turn 不存在")
    requests = await repo.list_requests(turn_id)
    runs = await repo.list_runs(turn_id)
    if not requests and not runs:
        original = await AgentRunRequestRepository(db).get_by_request_id(turn_id)
        if (
            original is not None
            and original.turn_id is not None
            and original.turn_id != turn_id
            and (original.conversation_thread_id, original.uid, original.app_id) == (thread_id, str(user.uid), app_id)
        ):
            return await get_public_turn(thread_id=thread_id, turn_id=original.turn_id, user=user, app_id=app_id, db=db)
        raise HTTPException(status_code=409, detail="Turn 没有关联的输入或运行")
    if any(
        (item.conversation_thread_id, item.uid, item.app_id) != (thread_id, str(user.uid), app_id)
        for item in [*requests, *runs]
    ):
        raise HTTPException(status_code=409, detail="Turn 关联的执行作用域不一致")

    latest_request = requests[-1] if requests else None
    latest_run = runs[-1] if runs else None
    if latest_run is None:
        status = "cancelled" if turn.cancelled_at else latest_request.status if latest_request else "queued"
        output = usage = error = None
    else:
        result = await get_agent_run_result(run_id=latest_run.id, current_uid=str(user.uid), db=db)
        run_status = latest_run.status
        status = (
            "in_progress"
            if run_status in {"pending", "running", "cancel_requested"}
            else "waiting"
            if run_status == "interrupted"
            else run_status
        )
        if status == "completed" and any(item.status == "queued" for item in requests):
            status = "in_progress"
        elif status == "completed" and turn.cancelled_at:
            status = "cancelled"
        output = result["output"] if result.get("final_message_id") is not None else None
        token_usage = result.get("token_usage") or {}
        usage = token_usage.get("total") if token_usage.get("complete") is True else None
        error = result.get("error")
    return {
        "turn_id": turn.id,
        "thread_id": thread_id,
        "status": status,
        "request_id": latest_request.request_id if latest_request else None,
        "request_status": latest_request.status if latest_request else None,
        "run_id": latest_run.id if latest_run else None,
        "run_status": latest_run.status if latest_run else None,
        "output": output,
        "usage": usage,
        "error": error,
        "request_ids": [item.request_id for item in requests],
        "run_ids": [item.id for item in runs],
    }


async def get_public_turn_items(
    *,
    thread_id: str,
    turn_id: str,
    user: User,
    app_id: str | None,
    after_id: int,
    limit: int,
    db: AsyncSession,
) -> dict:
    """从已持久化 Message 读取本轮可公开的输入与输出。"""
    turn = await get_public_turn(thread_id=thread_id, turn_id=turn_id, user=user, app_id=app_id, db=db)
    messages = await AgentTurnRepository(db).list_messages(
        thread_id=thread_id,
        request_ids=turn["request_ids"],
        run_ids=turn["run_ids"],
        after_id=after_id,
        limit=limit + 1,
    )
    visible = messages[:limit]
    return {
        "object": "list",
        "data": [
            {
                "id": f"msg_{message.id}",
                "object": "agent.session.item",
                "session_id": thread_id,
                "turn_id": turn["turn_id"],
                "role": message.role,
                "content": _public_item_content(message),
                "status": message.delivery_status,
                "request_id": message.request_id,
                "run_id": message.run_id,
                "created_at": format_utc_datetime(message.created_at),
            }
            for message in visible
        ],
        "has_more": len(messages) > limit,
        "next_after": visible[-1].id if len(messages) > limit else None,
    }


async def get_public_request(
    *, thread_id: str, request_id: str, user: User, app_id: str | None, db: AsyncSession
) -> dict:
    """按请求 ID 读取当前命名空间线程的持久结果。"""
    await get_public_thread(thread_id=thread_id, user=user, app_id=app_id, db=db)
    request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
    if request is None or request.app_id != app_id:
        raise HTTPException(status_code=404, detail="请求不存在")
    result = await get_agent_request_result(request_id=request_id, current_uid=str(user.uid), app_id=app_id, db=db)
    if result["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="请求不存在")
    return result


async def submit_public_message(
    *,
    agent_id: str,
    thread_id: str | None,
    text_parts: list[str],
    image_content: list[str] | None = None,
    ordered_content: list[dict] | None = None,
    creation_input_intent: list[dict] | None = None,
    idempotency_key: str,
    user: User,
    api_key: APIKey | None,
    db: AsyncSession,
    mode: str = "follow_up",
    mode_explicit: bool = True,
    project_id: str | None = None,
    title: str | None = None,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    attachment_file_ids: list[str] | None = None,
) -> dict:
    """将消息按显式模式提交，重放须保持原始输入与模式。"""
    app_id = api_key.app_id if api_key else None
    if api_key is not None and app_id is None:
        raise HTTPException(status_code=403, detail="API Key 未绑定 app_id")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key 长度必须为 1 至 128")
    image_content = image_content or []
    attachment_file_ids = attachment_file_ids or []
    if not text_parts and not image_content:
        raise HTTPException(status_code=422, detail="用户消息需要文本或图片")

    text = "\n".join(text_parts)
    try:
        normalize_image_contents(image_content)
        input_message = (
            build_chat_input_message_from_openai_content(ordered_content)
            if ordered_content is not None
            else build_chat_input_message(text, image_content)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scope = thread_id or "new-session"
    legacy_identity = f"{user.uid}:{app_id}:{scope}:{idempotency_key}"
    legacy_request_id = hash_id("pubreq_", legacy_identity, length=64)
    legacy_thread_id = thread_id or hash_id("pubsess_", legacy_identity, length=64)
    identity = json.dumps([str(user.uid), app_id, scope, idempotency_key], ensure_ascii=False, separators=(",", ":"))
    request_id = hash_id("pubreq_", identity, length=64)
    resolved_thread_id = thread_id or hash_id("pubsess_", identity, length=64)
    creation_request_id = None
    creation_intent_hash = None
    if thread_id is None:
        creation_request_id = hash_id("pubsess_", identity, length=64)
        creation_intent_hash = _public_creation_intent_hash(
            agent_id, project_id, title, model_spec, tool_approval_mode, creation_input_intent
        )
        existing_creation = await ConversationRepository(db).get_conversation_by_creation_request_id(
            str(user.uid), creation_request_id
        )
        if existing_creation is not None and (
            existing_creation.thread_id != resolved_thread_id
            or (existing_creation.extra_metadata or {}).get("public_creation_intent") != creation_intent_hash
        ):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 创建意图")
    if thread_id is not None:
        thread = await get_public_thread(thread_id=thread_id, user=user, app_id=app_id, db=db)
        if thread["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="线程不存在")

    legacy_request = await AgentRunRequestRepository(db).get_by_request_id(legacy_request_id) if api_key else None
    if (
        legacy_request is not None
        and legacy_request.uid == str(user.uid)
        and legacy_request.app_id == app_id
        and legacy_request.conversation_thread_id == legacy_thread_id
    ):
        request_id, resolved_thread_id = legacy_request_id, legacy_thread_id

    legacy_follow_up_hash = _public_intent_hash(
        agent_id,
        resolved_thread_id,
        text_parts,
        "follow_up",
        project_id,
        image_content,
        model_spec,
        tool_approval_mode,
        attachment_file_ids,
    )
    existing_request = (
        await AgentRunRequestRepository(db).get_by_request_id(request_id)
        if thread_id is not None and mode == "steer" and not mode_explicit
        else None
    )
    if existing_request is not None and existing_request.intent_hash == legacy_follow_up_hash:
        mode = "follow_up"
        intent_hash = legacy_follow_up_hash
    else:
        intent_hash = _public_intent_hash(
            agent_id,
            resolved_thread_id,
            text_parts,
            mode,
            project_id,
            image_content,
            model_spec,
            tool_approval_mode,
            attachment_file_ids,
            ordered_content,
        )
    if thread_id is not None:
        receipt_id = hash_id("pubevt_", identity, length=64)
        receipt_repo = AgentSessionInputReceiptRepository(db)
        receipt = await receipt_repo.get(receipt_id)
        if receipt is None:
            try:
                async with db.begin_nested():
                    receipt = await receipt_repo.create(
                        receipt_id=receipt_id,
                        uid=str(user.uid),
                        app_id=app_id,
                        thread_id=thread_id,
                        event_type="agent.session.input.message",
                        intent_hash=intent_hash,
                        turn_id=None,
                        run_id=None,
                    )
            except IntegrityError:
                receipt = await receipt_repo.get(receipt_id)
        if receipt is None or (
            receipt.uid,
            receipt.app_id,
            receipt.conversation_thread_id,
            receipt.event_type,
            receipt.intent_hash,
        ) != (str(user.uid), app_id, thread_id, "agent.session.input.message", intent_hash):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 输入")
    try:
        receipt = await submit_agent_request(
            request_input=AgentRequestInput(
                agent_slug=agent_id,
                thread_id=resolved_thread_id,
                request_id=request_id,
                input_message=input_message,
                origin=RunOrigin(source="public_api", channel="api" if api_key else "web", external_id=request_id),
                request_metadata={"attachment_file_ids": attachment_file_ids} if attachment_file_ids else {},
                model_spec=model_spec,
                tool_approval_mode=tool_approval_mode,
                queue_policy="steer" if mode == "steer" else "enqueue",
                create_conversation=thread_id is None,
                conversation_title=title or text[:100],
                conversation_project_id=project_id,
                conversation_creation_request_id=creation_request_id,
                conversation_metadata={"public_creation_intent": creation_intent_hash} if creation_intent_hash else {},
                intent_hash=intent_hash,
                app_id=app_id,
                api_key_id=api_key.id if api_key else None,
            ),
            current_user=user,
            db=db,
        )
    except IntegrityError as exc:
        if creation_request_id is None:
            raise
        await db.rollback()
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 创建意图") from exc
    await db.commit()
    return {
        "thread_id": resolved_thread_id,
        "turn_id": receipt.get("turn_id") or receipt["request_id"],
        "request_id": receipt["request_id"],
        "status": receipt["status"],
        "run_id": receipt.get("run_id"),
    }


async def stream_public_request_events(
    *, request_id: str, uid: str, after_seq: str, run_stream_url: str
) -> AsyncIterator[str]:
    """先跟随持久 Request 的派发，再跟随其唯一 Run 的事件流。"""
    async with pg_manager.get_async_session_context() as db:
        request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
        run_id = request.dispatched_run_id if request is not None else None
    already_dispatched = run_id is not None
    if run_id is None:
        async for event in stream_request_events(
            request_id=request_id,
            uid=uid,
            db_session_factory=pg_manager.get_async_session_context,
            run_stream_url=run_stream_url,
        ):
            yield event
        async with pg_manager.get_async_session_context() as db:
            request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
            run_id = request.dispatched_run_id if request is not None else None
    if run_id is not None:
        if already_dispatched:
            yield format_sse(
                {"request_id": request_id, "run_id": run_id, "stream_url": run_stream_url},
                event="run_created",
            )
        async for event in stream_agent_run_events(run_id=run_id, after_seq=after_seq, current_uid=uid, verbose=False):
            yield event


def _public_creation_intent_hash(
    agent_id: str,
    project_id: str | None,
    title: str | None,
    model_spec: str | None,
    tool_approval_mode: str | None,
    input_intent: list[dict] | None,
) -> str:
    """将 Session 创建参数及首条输入合成统一幂等意图。"""
    intent = {
        "agent_id": agent_id,
        "project_id": project_id,
        "title": title,
        "model_spec": model_spec,
        "tool_approval_mode": tool_approval_mode,
        "input": input_intent,
    }
    return hashlib.sha256(json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _public_intent_hash(
    agent_id: str,
    thread_id: str,
    text_parts: list[str],
    mode: str,
    project_id: str | None,
    image_content: list[str],
    model_spec: str | None,
    tool_approval_mode: str | None,
    attachment_file_ids: list[str],
    ordered_content: list[dict] | None = None,
) -> str:
    """将模式纳入幂等意图；旧 follow-up 请求维持原有摘要。"""
    payload = {"agent_id": agent_id, "session_id": thread_id, "text_parts": text_parts}
    if mode != "follow_up":
        payload["mode"] = mode
    if project_id is not None:
        payload["project_id"] = project_id
    if image_content:
        payload["image_content"] = image_content
    if model_spec is not None:
        payload["model_spec"] = model_spec
    if tool_approval_mode is not None:
        payload["tool_approval_mode"] = tool_approval_mode
    if attachment_file_ids:
        payload["attachment_file_ids"] = attachment_file_ids
    if ordered_content and any(part["type"] == "image_url" for part in ordered_content):
        payload["ordered_content"] = ordered_content
    intent = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()


def _public_item_content(message) -> list[dict]:
    """从持久消息的多模态原文投影公开内容块。"""
    content = []
    if message.role == "user":
        metadata = message.extra_metadata or {}
        raw_message = metadata.get("raw_message")
        raw_content = raw_message.get("content") if isinstance(raw_message, dict) else None
        if isinstance(raw_content, list):
            for part in raw_content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    content.append({"type": "input_text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else image_url
                    if isinstance(url, str) and url.startswith("data:image/"):
                        content.append({"type": "input_image", "image_url": url})
            if content:
                return content
        if message.content:
            content.append({"type": "input_text", "text": message.content})
        images = extract_image_contents(raw_message)
        if not images and message.image_content:
            images = [message.image_content]
        content.extend({"type": "input_image", "image_url": f"data:image/jpeg;base64,{image}"} for image in images)
    elif message.content:
        content.append({"type": "output_text", "text": message.content})
    return content
