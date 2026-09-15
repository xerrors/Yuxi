import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.models.utils import parse_assistant_message_body
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import INVOCATION_CONVERSATION_SOURCES, ConversationRepository
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.services.attachment_service import serialize_attachment
from yuxi.services.project_service import create_implicit_project
from yuxi.services.workdir_service import (
    ensure_conversation_workdir_available,
    resolve_conversation_workdir_path,
    workdir_binding_from_project,
)
from yuxi.storage.postgres.models_business import (
    AGENT_RUN_TERMINAL_STATUSES,
    AgentRun,
    User,
    build_agent_run_timing,
)
from yuxi.utils.datetime_utils import format_utc_datetime
from yuxi.utils.logging_config import logger
from yuxi.workspace.paths import ensure_bound_user_workdir
from yuxi.workspace.workdir import Workdir

MESSAGE_AUDIT_LIMIT = 500
AGENT_RUN_TRACE_LIMIT = 500
MODEL_HISTORY_METADATA_KEYS = frozenset(
    {"attachments", "source", "error_type", "error_message", "langfuse_trace_id", "model"}
)


async def require_user_conversation(conv_repo: ConversationRepository, thread_id: str, uid: str):
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


async def get_thread_message_audits_view(
    *,
    thread_id: str,
    current_uid: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """返回当前用户线程内最新的有界 Model/Tool 审计时间线。"""
    repository = ConversationRepository(db)
    conversation = await require_user_conversation(
        repository,
        thread_id,
        str(current_uid),
    )
    messages, truncated = await repository.list_message_audits(
        conversation.id,
        limit=MESSAGE_AUDIT_LIMIT,
    )
    runs, runs_truncated = await repository.list_agent_runs_for_trace(
        conversation.id,
        limit=AGENT_RUN_TRACE_LIMIT,
    )
    return {
        "audits": [_serialize_message_audit(message) for message in messages],
        "runs": [_serialize_run_trace(run) for run in runs],
        "runs_truncated": runs_truncated,
        "truncated": truncated,
    }


async def create_thread_view(
    *,
    agent_slug: str,
    request_id: str | None,
    title: str | None,
    metadata: dict | None,
    project_id: str | None = None,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    if metadata and ({"attachments", "memory_history_started_at"} & metadata.keys()):
        raise HTTPException(status_code=400, detail="附件和个人文件来源时间是服务端保留字段")

    user_result = await db.execute(select(User).where(User.uid == str(current_uid)))
    current_user = user_result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(slug=agent_slug, user=current_user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    conv_repo = ConversationRepository(db)
    project_repo = ProjectRepository(db)
    normalized_request_id = str(request_id or "").strip() or None
    if normalized_request_id:
        existing = await conv_repo.get_conversation_by_creation_request_id(str(current_uid), normalized_request_id)
        if existing is not None:
            existing_project = await project_repo.get_for_user(existing.project_id, str(current_uid))
            _require_matching_thread_creation_intent(
                existing,
                existing_project,
                agent_slug=agent_item.slug,
                project_id=project_id,
            )
            workdir_binding = workdir_binding_from_project(
                conversation=existing,
                uid=str(current_uid),
                project=existing_project,
            )
            await ensure_conversation_workdir_available(
                conversation=existing,
                uid=str(current_uid),
                db=db,
                workdir_binding=workdir_binding,
            )
            return await _serialize_thread(
                existing,
                thread_status="done",
                db=db,
                workdir_path=workdir_binding.workdir_path,
            )

    thread_id = str(uuid.uuid4())
    thread_metadata = dict(metadata or {})
    thread_metadata["backend_id"] = agent_item.backend_id
    if project_id:
        project = await project_repo.lock_active_selectable_for_user(
            project_id,
            str(current_uid),
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project 不存在")
        try:
            Workdir.open_existing(str(current_uid), project.workdir_path)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="项目目录不可用") from exc
    else:
        try:
            project = await create_implicit_project(
                uid=str(current_uid),
                db=db,
                idempotency_key=f"thread:{normalized_request_id}" if normalized_request_id else None,
            )
        except IntegrityError:
            await db.rollback()
            if not normalized_request_id:
                raise
            project = await project_repo.get_by_idempotency_key(f"thread:{normalized_request_id}", str(current_uid))
            if project is None or project.selection_status != "implicit":
                raise HTTPException(status_code=409, detail="request_id 已用于其他 Conversation 创建意图")
    try:
        conversation = await conv_repo.add_conversation(
            uid=str(current_uid),
            agent_id=agent_item.slug,
            title=title or "新的对话",
            thread_id=thread_id,
            metadata=thread_metadata,
            project_id=project.id,
            creation_request_id=normalized_request_id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if not normalized_request_id:
            raise
        conversation = await conv_repo.get_conversation_by_creation_request_id(str(current_uid), normalized_request_id)
        if conversation is None:
            implicit_project = await project_repo.get_by_idempotency_key(
                f"thread:{normalized_request_id}", str(current_uid)
            )
            if implicit_project is None or project_id:
                raise
            project = implicit_project
            conversation = await conv_repo.add_conversation(
                uid=str(current_uid),
                agent_id=agent_item.slug,
                title=title or "新的对话",
                thread_id=thread_id,
                metadata=thread_metadata,
                project_id=project.id,
                creation_request_id=normalized_request_id,
            )
            await db.commit()
        existing_project = await project_repo.get_for_user(conversation.project_id, str(current_uid))
        _require_matching_thread_creation_intent(
            conversation,
            existing_project,
            agent_slug=agent_item.slug,
            project_id=project_id,
        )
        project = existing_project

    workdir_binding = workdir_binding_from_project(
        conversation=conversation,
        uid=str(current_uid),
        project=project,
    )
    try:
        if workdir_binding.materialize_managed:
            ensure_bound_user_workdir(workdir_binding.uid, workdir_binding.workdir_path)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await _serialize_thread(
        conversation,
        thread_status="done",
        db=db,
        workdir_path=workdir_binding.workdir_path,
    )


async def list_threads_view(
    *,
    agent_slug: str | None,
    db: AsyncSession,
    current_uid: str,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    conv_repo = ConversationRepository(db)
    conversations = await conv_repo.list_conversations(
        uid=str(current_uid),
        agent_id=agent_slug,
        status="active",
        limit=limit,
        offset=offset,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    run_repo = AgentRunRepository(db)
    thread_ids = [conv.thread_id for conv in conversations]
    run_map = await run_repo.get_latest_top_level_runs_for_threads(str(current_uid), thread_ids)

    return [
        await _serialize_thread(
            conv,
            thread_status=_thread_status(
                *run_map.get(conv.thread_id, (None, None)),
                conv.last_viewed_run_id,
            ),
            db=db,
            workdir_path=conv.project.workdir_path,
        )
        for conv in conversations
    ]


async def search_threads_view(
    *,
    query: str,
    agent_id: str | None,
    db: AsyncSession,
    current_uid: str,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {"items": [], "has_more": False, "limit": limit, "offset": offset}

    conv_repo = ConversationRepository(db)
    search_items, has_more = await conv_repo.search_conversations_by_message_content(
        uid=str(current_uid),
        agent_id=agent_id,
        query=normalized_query,
        limit=limit,
        offset=offset,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    items = []
    for item in search_items:
        conv = item["conversation"]
        snippets = [
            {
                "message_id": snippet.get("message_id"),
                "content": snippet.get("content") or "",
                "created_at": format_utc_datetime(snippet.get("created_at")),
            }
            for snippet in item.get("snippets", [])
        ]
        items.append(
            {
                "id": conv.thread_id,
                "thread_id": conv.thread_id,
                "uid": conv.uid,
                "agent_id": conv.agent_id,
                "title": conv.title,
                "is_pinned": bool(conv.is_pinned),
                "created_at": format_utc_datetime(conv.created_at),
                "updated_at": format_utc_datetime(conv.updated_at),
                "metadata": conv.extra_metadata or {},
                "matched_count": item.get("matched_count", 0),
                "message_id": item.get("message_id"),
                "latest_match_at": format_utc_datetime(item.get("latest_match_at")),
                "snippets": snippets,
            }
        )

    return {"items": items, "has_more": has_more, "limit": limit, "offset": offset}


async def delete_thread_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_uid))
    deleted = await conv_repo.delete_conversation(thread_id, soft_delete=True)
    if not deleted:
        raise HTTPException(status_code=404, detail="对话线程不存在")

    return {"message": "删除成功"}


async def update_thread_view(
    *,
    thread_id: str,
    title: str | None = None,
    is_pinned: bool | None = None,
    tool_approval_mode: str | None = None,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    conv_repo = ConversationRepository(db)
    await require_user_conversation(conv_repo, thread_id, str(current_uid))
    metadata = {"tool_approval_mode": tool_approval_mode} if tool_approval_mode is not None else None
    updated_conv = await conv_repo.update_conversation(
        thread_id,
        title=title,
        is_pinned=is_pinned,
        metadata=metadata,
    )
    if not updated_conv:
        raise HTTPException(status_code=500, detail="更新失败")

    run_repo = AgentRunRepository(db)
    run_map = await run_repo.get_latest_top_level_runs_for_threads(str(current_uid), [updated_conv.thread_id])
    run_id, run_status = run_map.get(updated_conv.thread_id, (None, None))

    return await _serialize_thread(
        updated_conv,
        thread_status=_thread_status(run_id, run_status, updated_conv.last_viewed_run_id),
        db=db,
    )


async def mark_thread_viewed_view(
    *,
    thread_id: str,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    """记录用户已查看该线程的最新顶层 run，使未读状态转为已读。"""
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))

    run_repo = AgentRunRepository(db)
    run_map = await run_repo.get_latest_top_level_runs_for_threads(str(current_uid), [thread_id])
    run_id, run_status = run_map.get(thread_id, (None, None))

    if run_id and run_status in AGENT_RUN_TERMINAL_STATUSES:
        conversation = await conv_repo.mark_thread_viewed(thread_id, run_id)

    return await _serialize_thread(
        conversation,
        thread_status=_thread_status(run_id, run_status, conversation.last_viewed_run_id),
        db=db,
    )


async def get_thread_history_view(
    *,
    thread_id: str,
    current_uid: str,
    db: AsyncSession,
) -> dict:
    """读取线程、Run 与历史消息，保留独立的已读写操作。"""
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.uid != str(current_uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    messages = await conv_repo.get_messages(conversation.id)
    messages = [
        message
        for message in messages
        if not (message.role == "user" and message.delivery_status in {"queued", "cancelled", "rejected"})
    ]

    runs = await conv_repo.list_agent_runs_for_history(conversation.id)
    run_created_at = {run.id: run.created_at for run in runs}
    latest_run = next((run for run in reversed(runs) if run.run_type in {"chat", "resume"}), None)
    thread = await _serialize_thread(
        conversation,
        thread_status=_thread_status(
            latest_run.id if latest_run else None,
            latest_run.status if latest_run else None,
            conversation.last_viewed_run_id,
        ),
        db=db,
    )
    messages.sort(
        key=lambda message: (
            run_created_at.get(message.run_id) or message.created_at,
            0 if message.role == "user" else 1,
            message.created_at,
            message.id,
        )
    )
    message_request_ids = set()
    for msg in messages:
        request_id = (msg.extra_metadata or {}).get("request_id")
        if msg.role == "user" and request_id:
            message_request_ids.add(str(request_id))
    attachments_by_request_id: dict[str, list[dict]] = {}
    if message_request_ids:
        for attachment in await conv_repo.get_attachments(conversation.id):
            request_id = attachment.get("request_id")
            if not request_id or str(request_id) not in message_request_ids:
                continue
            attachments_by_request_id.setdefault(str(request_id), []).append(
                serialize_attachment(attachment, thread_id=thread_id)
            )

    history: list[dict] = []
    role_type_map = {"user": "human", "assistant": "ai", "tool": "tool", "system": "system"}

    for msg in messages:
        user_feedback = None
        if msg.feedbacks:
            for feedback in msg.feedbacks:
                if feedback.uid == str(current_uid):
                    user_feedback = {
                        "id": feedback.id,
                        "rating": feedback.rating,
                        "reason": feedback.reason,
                        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
                    }
                    break

        extra_metadata = _serialize_history_metadata(msg)
        request_id = extra_metadata.get("request_id")
        if msg.role == "user" and request_id and not extra_metadata.get("attachments"):
            extra_metadata["attachments"] = attachments_by_request_id.get(str(request_id), [])

        msg_dict = {
            "id": msg.id,
            "type": role_type_map.get(msg.role, msg.role),
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "run_id": msg.run_id,
            "request_id": msg.request_id,
            "delivery_status": msg.delivery_status,
            "error_type": extra_metadata.get("error_type"),
            "error_message": extra_metadata.get("error_message"),
            "extra_metadata": extra_metadata,
            "message_type": msg.message_type,
            "image_content": msg.image_content,
            "feedback": user_feedback,
        }

        if msg.role == "assistant":
            msg_dict.update(parse_assistant_message_body(msg.content, msg.extra_metadata or {}))

        if msg.tool_calls:
            msg_dict["tool_calls"] = [_serialize_tool_call(tool_call) for tool_call in msg.tool_calls]

        history.append(msg_dict)

    logger.info(f"Loaded {len(history)} messages with feedback for thread {thread_id}")
    return {
        "thread": thread,
        "runs": [
            {
                **_serialize_run_trace(run),
                "request_id": run.request_id,
                "run_type": run.run_type,
                "created_by_run_id": run.created_by_run_id,
            }
            for run in runs
        ],
        "history": history,
    }


def _thread_status(run_id: str | None, run_status: str | None, last_viewed_run_id: str | None) -> str:
    """将线程最新顶层 run 与查看记录映射为侧边栏三态。

    loading: 顶层 run 进行中；ready: run 已终态且未查看；done: 无 run 或已查看。
    """
    if run_id is None:
        return "done"
    if run_status not in AGENT_RUN_TERMINAL_STATUSES:
        return "loading"
    if run_id == last_viewed_run_id:
        return "done"
    return "ready"


async def _serialize_thread(
    conversation: Any,
    *,
    thread_status: str,
    db,
    workdir_path: str | None = None,
) -> dict:
    """序列化线程，列表调用方可传入已联查的 Project Workdir。"""
    resolved_workdir_path = workdir_path
    if resolved_workdir_path is None:
        resolved_workdir_path = await resolve_conversation_workdir_path(
            conversation=conversation,
            uid=str(conversation.uid),
            db=db,
        )
    return {
        "id": conversation.thread_id,
        "uid": conversation.uid,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "is_pinned": bool(conversation.is_pinned),
        "project_id": conversation.project_id,
        "workdir_path": resolved_workdir_path,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "metadata": conversation.extra_metadata or {},
        "thread_status": thread_status,
    }


def _require_matching_thread_creation_intent(
    conversation,
    project,
    *,
    agent_slug: str,
    project_id: str | None,
) -> None:
    """要求已有 Conversation 仍有效且匹配当前幂等创建意图。"""
    if conversation.status == "deleted" or project is None or project.status == "deleted":
        raise HTTPException(status_code=409, detail="request_id 已用于已删除的 Conversation")
    same_project_intent = (
        conversation.project_id == project_id
        if project_id
        else project is not None and project.selection_status == "implicit"
    )
    if conversation.agent_id != agent_slug or not same_project_intent:
        raise HTTPException(status_code=409, detail="request_id 已用于其他 Conversation 创建意图")


def _serialize_history_metadata(message: Any) -> dict[str, Any]:
    """从普通 History 中移除 Model lifecycle 内部字段。"""
    metadata = dict(message.extra_metadata or {})
    if message.operation_id is None:
        return metadata
    return {key: metadata[key] for key in MODEL_HISTORY_METADATA_KEYS if key in metadata}


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    """序列化普通历史和审计共用的 ToolCall 结构。"""
    return {
        "id": tool_call.langgraph_tool_call_id or str(tool_call.id),
        "name": tool_call.tool_name,
        "function": {"name": tool_call.tool_name},
        "args": tool_call.tool_input or {},
        "tool_call_result": {"content": tool_call.tool_output or ""} if tool_call.status == "success" else None,
        "status": tool_call.status,
        "error_message": tool_call.error_message,
    }


def _serialize_message_audit(message: Any) -> dict[str, Any]:
    """将 Message 审计事实分派到显式 Model/Tool DTO。"""
    if message.role == "tool":
        return _serialize_tool_audit(message)
    return _serialize_model_audit(message)


def _serialize_run_trace(run: AgentRun) -> dict[str, Any]:
    """从 AgentRun Owner 投影调试面板所需的状态与阶段时间。"""
    return {
        "run_id": run.id,
        "status": run.status,
        "timing": build_agent_run_timing(
            created_at=run.created_at,
            started_at=run.started_at,
            prepared_at=run.prepared_at,
            first_output_at=run.first_output_at,
            finished_at=run.finished_at,
            first_model_request_at=getattr(run, "first_model_request_at", None),
        ),
    }


def _serialize_model_audit(message: Any) -> dict[str, Any]:
    """将 Model 审计事实收敛为前端调试 DTO。"""
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    content_blocks = metadata.get("content")
    model_run_id = metadata.get("model_run_id")
    return {
        **_serialize_audit_base(message, metadata),
        **parse_assistant_message_body(message.content, metadata),
        "type": "ai",
        "usage": dict(message.usage) if isinstance(message.usage, dict) else None,
        "model_run_id": model_run_id if isinstance(model_run_id, str) else None,
        "content_blocks": content_blocks if isinstance(content_blocks, list) else [],
        "tool_calls": [_serialize_tool_call(tool_call) for tool_call in message.tool_calls],
    }


def _serialize_tool_audit(message: Any) -> dict[str, Any]:
    """将 ToolMessage 审计事实收敛为前端调试 DTO。"""
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    return {
        **_serialize_audit_base(message, metadata),
        "type": "tool",
        "tool_call_id": metadata.get("tool_call_id"),
        "tool_name": metadata.get("tool_name"),
        "tool_input": dict(metadata["input"]) if isinstance(metadata.get("input"), dict) else {},
        "tool_output": metadata.get("output"),
        "error_message": metadata.get("error_message"),
        "source_model_operation_id": metadata.get("source_model_operation_id"),
        "usage": None,
    }


def _serialize_audit_base(message: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    """序列化 Model/Tool 审计共有字段。"""
    namespace = metadata.get("namespace")
    finished_sequence = metadata.get("finished_sequence")
    if not isinstance(finished_sequence, int) or isinstance(finished_sequence, bool):
        finished_sequence = None
    return {
        "id": message.id,
        "content": message.content,
        "created_at": format_utc_datetime(message.created_at),
        "run_id": message.run_id,
        "request_id": message.request_id,
        "message_type": message.message_type,
        "operation_id": message.operation_id,
        "started_at": format_utc_datetime(message.started_at),
        "finished_at": format_utc_datetime(message.finished_at),
        "duration_ms": message.duration_ms,
        "sequence": message.sequence,
        "finished_sequence": finished_sequence,
        "execution_status": message.execution_status,
        "namespace": [item for item in namespace if isinstance(item, str)] if isinstance(namespace, list) else [],
    }
