"""Thread 查询、归档和显式队列控制用例。

Session 保存长期交互的业务记录，与 Thread 一对一。
Thread 标识执行线程，沿用 checkpoint、队列和事件订阅身份。
"""

from __future__ import annotations

import uuid
from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES
from yuxi.modules.agents.receipts import control_intent_hash, require_idempotency_key, require_receipt_replay
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.input_config import (
    resolve_agent_run_model_spec,
    resolve_agent_run_tool_approval_mode,
)
from yuxi.modules.agents.services.public_resources import session_resource
from yuxi.modules.agents.services.queue_control import pause_pending_queue
from yuxi.modules.agents.services.scheduler import claim_next_input, deliver
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.workspace.repositories.projects import ProjectRepository
from yuxi.shared.datetime import format_utc_datetime, utc_now
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError


async def list_session_page(
    *,
    db: AsyncSession,
    scope: ActorScope,
    agent_slug: str | None,
    after: str | None,
    limit: int,
    order: str,
    archived: bool = False,
    is_pinned: bool | None = None,
) -> tuple[list[dict], bool]:
    """批量读取公开会话页的活动事实，不逐项加载完整历史。"""
    repo = SessionRepository(db)
    try:
        sessions, has_more = await repo.list_public_sessions(
            uid=scope.uid,
            app_id=scope.app_id,
            agent_id=agent_slug,
            after=after,
            limit=limit,
            order=order,
            archived=archived,
            is_pinned=is_pinned,
            exclude_personal=True,
        )
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc
    facts = await repo.public_facts(sessions, uid=scope.uid, app_id=scope.app_id)
    return [session_resource(*row) for row in facts], has_more


async def search_threads(
    *,
    db: AsyncSession,
    scope: ActorScope,
    query: str,
    agent_slug: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """仅搜索当前用户与 APP 命名空间中的会话标题和可见历史消息。"""
    normalized_query = query.strip()
    if not normalized_query:
        return {"items": [], "has_more": False, "limit": limit, "offset": offset}

    search_items, has_more = await SessionRepository(db).search_sessions(
        uid=scope.uid,
        app_id=scope.app_id,
        agent_id=agent_slug,
        query=normalized_query,
        exclude_personal=True,
        limit=limit,
        offset=offset,
    )
    facts = await SessionRepository(db).public_facts([item["agent_session"] for item in search_items], uid=scope.uid, app_id=scope.app_id)
    items = [
        {
            "session": session_resource(*row),
            "matched_count": item.get("matched_count", 0),
            "message_id": item.get("message_id"),
            "latest_match_at": format_utc_datetime(item.get("latest_match_at")),
            "snippets": [
                {
                    "message_id": snippet.get("message_id"),
                    "content": snippet.get("content") or "",
                    "created_at": format_utc_datetime(snippet.get("created_at")),
                }
                for snippet in item.get("snippets", [])
            ],
        }
        for item, row in zip(search_items, facts, strict=True)
    ]
    return {"items": items, "has_more": has_more, "limit": limit, "offset": offset}


async def mark_thread_viewed(*, db: AsyncSession, scope: ActorScope, thread_id: str) -> None:
    """仅将当前作用域最新的终态顶层 Run 标为已查看。"""
    await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    run_map = await AgentRunRepository(db).get_latest_top_level_runs_for_threads(scope.uid, [thread_id])
    run_id, run_status = run_map.get(thread_id, (None, None))
    if run_id and run_status in AGENT_RUN_TERMINAL_STATUSES:
        if await SessionRepository(db).mark_thread_viewed(thread_id, run_id):
            await db.commit()


async def update_thread(
    *,
    db: AsyncSession,
    scope: ActorScope,
    thread_id: str,
    title: str | None = None,
    is_pinned: bool | None = None,
    tool_approval_mode: str | None = None,
    model_spec: str | None = None,
) -> None:
    """在会话锁内更新展示或后续输入的执行配置。"""
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    if title is not None:
        normalized = title.strip()
        if not normalized or len(normalized) > 255:
            raise InvalidInputError("标题长度必须为 1 至 255")
        agent_session.title = normalized
        metadata = dict(agent_session.extra_metadata or {})
        metadata.pop("auto_title", None)
        agent_session.extra_metadata = metadata
    if is_pinned is not None:
        agent_session.is_pinned = is_pinned
    if tool_approval_mode is not None or model_spec is not None:
        if agent_session.status != "active":
            raise StateConflictError("非活跃 Thread 不能修改执行配置")
        if agent_session.config_snapshot is None:
            raise ValueError("Session 缺少配置快照")
        snapshot = deepcopy(agent_session.config_snapshot)
        if tool_approval_mode is not None:
            snapshot["tool_approval_mode"] = resolve_agent_run_tool_approval_mode(tool_approval_mode, None)
        if model_spec is not None:
            snapshot["model"] = await resolve_agent_run_model_spec(model_spec, None, db)
        agent_session.config_snapshot = snapshot
    agent_session.updated_at = utc_now()
    await db.commit()


async def archive_thread(*, db: AsyncSession, scope: ActorScope, thread_id: str) -> None:
    """归档前保留未结束工作的入口，根会话承担整棵协作树的可达性。"""
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id)
    from yuxi.modules.agents.repositories.personal_assistants import is_personal_main_thread

    if await is_personal_main_thread(db, thread_id):
        raise InvalidOperationError("个人助理主对话需要持续保留，不能归档")
    if agent_session.status == "archived":
        return
    repo = SessionRepository(db)
    if agent_session.parent_thread_id is None:
        # 与成员创建、项目删除共用 Project → 有序 Session 锁序，避免新增成员越过归档判定。
        project = await ProjectRepository(db).lock_active_for_user(agent_session.project_id, scope.uid)
        if project is None:
            raise StateConflictError("Project 已不可访问")
        members = await repo.lock_tree_sessions(tree_root_thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    else:
        agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
        members = [agent_session]
    if await repo.has_unfinished_work(thread_ids=[member.thread_id for member in members], uid=scope.uid, app_id=scope.app_id):
        raise StateConflictError("会话范围内仍有活跃执行或待处理输入，不能归档")
    agent_session.status = "archived"
    agent_session.updated_at = utc_now()
    await db.commit()


async def get_session_resource(*, db: AsyncSession, scope: ActorScope, thread_id: str, receipt=None) -> dict:
    """详情复用列表的批量事实与唯一 Session 投影。"""
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id)
    [facts] = await SessionRepository(db).public_facts([agent_session], uid=scope.uid, app_id=scope.app_id)
    return session_resource(*facts, receipt=receipt)


async def get_queue_snapshot(*, db: AsyncSession, scope: ActorScope, thread_id: str) -> dict:
    """按调度顺序展示待消费输入与独立暂停标记。"""
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id)
    input_repo = AgentInputRepository(db)
    items = await input_repo.list_pending_inputs(thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    preparation = await AttachmentRepository(db).statuses_for_inputs([item.id for item in items])
    active = await AgentTurnRepository(db).get_active_for_thread(thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    return {
        "thread_id": thread_id,
        "queue_paused": bool(agent_session.queue_paused),
        "status": "paused" if agent_session.queue_paused else "running" if active else "ready",
        "inputs": [
            {
                "input_id": item.id,
                "status": item.status,
                "kind": item.kind,
                "turn_id": item.turn_id,
                "run_id": item.consumed_run_id,
                "received_seq": item.received_seq,
                **preparation[item.id],
                "content": "\n".join(part["text"] for message in item.messages for part in message["content"] if part["type"] == "text"),
            }
            for item in items
        ],
    }


async def continue_queue(*, db: AsyncSession, scope: ActorScope, thread_id: str, idempotency_key: str) -> dict:
    """显式解除失败或取消后的暂停，并在同一事务领取优先队头。"""
    require_idempotency_key(idempotency_key)
    event_type = "yuxi.session.input.continue"
    intent_hash = control_intent_hash(event_type)
    receipt_repo = AgentInputReceiptRepository(db)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, event_type, intent_hash)
        return control_accepted(existing)

    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, event_type, intent_hash)
        return control_accepted(existing)
    if not agent_session.queue_paused:
        raise StateConflictError("队列未暂停")
    if await AgentTurnRepository(db).lock_active_for_thread(thread_id=thread_id, uid=scope.uid, app_id=scope.app_id):
        raise StateConflictError("当前 Turn 尚未结束")

    agent_session.queue_paused = False
    dispatch = await claim_next_input(db=db, agent_session=agent_session)
    run = await AgentRunRepository(db).get_run(dispatch.run_id) if dispatch else None
    receipt = await receipt_repo.create(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        event_type=event_type,
        intent_hash=intent_hash,
        turn_id=run.turn_id if dispatch else None,
        run_id=dispatch.run_id if dispatch else None,
    )
    await db.commit()
    if dispatch:
        await deliver(dispatch)
    return control_accepted(receipt)


async def cancel_input(*, db: AsyncSession, scope: ActorScope, thread_id: str, input_id: str, idempotency_key: str) -> dict:
    """取消未领取的 Input，不伪造尚未存在的 Turn。"""
    require_idempotency_key(idempotency_key)
    event_type = "yuxi.session.input.cancel_input"
    intent_hash = control_intent_hash(event_type, input_id)
    receipt_repo = AgentInputReceiptRepository(db)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, event_type, intent_hash)
        return control_accepted(existing)

    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    existing = await receipt_repo.get_for_scope(uid=scope.uid, app_id=scope.app_id, thread_id=thread_id, idempotency_key=idempotency_key)
    if existing is not None:
        require_receipt_replay(existing, event_type, intent_hash)
        return control_accepted(existing)
    input_repo = AgentInputRepository(db)
    input_item = await input_repo.get_for_scope(input_id=input_id, thread_id=thread_id, uid=scope.uid, app_id=scope.app_id, for_update=True)
    if input_item is None:
        raise ResourceNotFoundError("Input 不存在")
    if input_item.status != "pending":
        raise StateConflictError("Input 已被领取或取消")
    await input_repo.cancel(input_item)
    if agent_session.queue_paused:
        await pause_pending_queue(db, agent_session)
    receipt = await receipt_repo.create(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        event_type=event_type,
        intent_hash=intent_hash,
        input_id=input_id,
    )
    await db.commit()
    return control_accepted(receipt)


async def promote_input(*, db: AsyncSession, scope: ActorScope, thread_id: str, input_id: str, idempotency_key: str) -> dict:
    """在 Session 锁内提升未消费输入，暂停状态保持不变。"""
    require_idempotency_key(idempotency_key)
    event_type = "yuxi.session.input.promote"
    intent_hash = control_intent_hash(event_type, input_id)
    agent_session = await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
    receipt_repo = AgentInputReceiptRepository(db)
    existing = await receipt_repo.get_for_scope(
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        require_receipt_replay(existing, event_type, intent_hash)
        return control_accepted(existing)
    item = await AgentInputRepository(db).get_for_scope(
        input_id=input_id,
        thread_id=thread_id,
        uid=scope.uid,
        app_id=scope.app_id,
        for_update=True,
    )
    if item is None:
        raise ResourceNotFoundError("Input 不存在")
    if item.status != "pending" or agent_session.status != "active":
        raise StateConflictError("Input 已被领取、取消或会话已归档")
    active = await AgentTurnRepository(db).lock_active_for_thread(thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
    if active is not None and active.status in {"waiting", "cancelling"}:
        raise StateConflictError("当前任务正在等待或取消清理，不能转为引导")
    if await AttachmentRepository(db).has_unready(input_id):
        raise StateConflictError("附件尚未就绪")
    changed = item.kind != "steer"
    item.kind = "steer"
    receipt = await receipt_repo.create(
        receipt_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        uid=scope.uid,
        app_id=scope.app_id,
        thread_id=thread_id,
        event_type=event_type,
        intent_hash=intent_hash,
        input_id=input_id,
    )
    if changed:
        from yuxi.modules.agents.services.cooperation_notifications import notify_user_intervention

        await notify_user_intervention(db, agent_session, key=receipt.id, action="steer")
    await db.commit()
    if changed:
        from yuxi.modules.agents.services.scheduler import dispatch_next_input

        await dispatch_next_input(uid=scope.uid, agent_slug=agent_session.agent_id, thread_id=thread_id)
    return control_accepted(receipt)


def control_accepted(receipt) -> dict:
    """返回控制事件首次固定的目标事实。"""
    return {
        "event_id": receipt.id,
        "thread_id": receipt.thread_id,
        "input_id": receipt.input_id,
        "turn_id": receipt.turn_id,
        "run_id": receipt.run_id,
        "status": "accepted",
    }
