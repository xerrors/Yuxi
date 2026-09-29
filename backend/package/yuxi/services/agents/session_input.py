"""Public Session 控制输入的固定目标与执行。"""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agents.input_receipt import AgentSessionInputReceiptRepository
from yuxi.repositories.agents.turn import AgentTurnRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_request_queue_service import dispatch_next_request
from yuxi.services.agent_run_service import create_resume_run_view
from yuxi.services.agents.public_api import get_public_session, get_public_thread_scope, get_public_turn
from yuxi.services.run_queue_service import publish_cancel_signals
from yuxi.storage.postgres.models_business import Message, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.hash_utils import hash_id


async def resume_public_turn(
    *,
    thread_id: str,
    turn_id: str,
    parent_run_id: str,
    resume: object,
    idempotency_key: str,
    user: User,
    app_id: str | None,
    db: AsyncSession,
) -> dict:
    """将产品审批或回答绑定到明确的等待 Turn，并持久创建恢复 Run。"""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key 长度必须为 1 至 128")
    thread = await get_public_thread_scope(thread_id=thread_id, user=user, app_id=app_id, db=db)
    identity = json.dumps([str(user.uid), app_id, thread_id, idempotency_key], separators=(",", ":"))
    receipt_id = hash_id("pubevt_", identity, length=64)
    resume_request_id = hash_id("pubresume_", identity, length=64)
    intent = json.dumps(["yuxi.session.input.resume", turn_id, parent_run_id, resume], sort_keys=True, default=str)
    intent_hash = hashlib.sha256(intent.encode()).hexdigest()
    receipt_repo = AgentSessionInputReceiptRepository(db)
    existing = await receipt_repo.get(receipt_id)
    if existing is not None:
        return await _resume_replay(
            existing,
            thread_id,
            str(user.uid),
            app_id,
            turn_id,
            parent_run_id,
            intent_hash,
            resume_request_id,
            db,
        )

    await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
    existing = await receipt_repo.get(receipt_id)
    if existing is not None:
        return await _resume_replay(
            existing,
            thread_id,
            str(user.uid),
            app_id,
            turn_id,
            parent_run_id,
            intent_hash,
            resume_request_id,
            db,
        )
    turn = await get_public_turn(thread_id=thread_id, turn_id=turn_id, user=user, app_id=app_id, db=db)
    if turn["status"] != "waiting" or turn["run_id"] != parent_run_id:
        raise HTTPException(status_code=409, detail="Turn 不在指定 Run 的等待状态")

    await receipt_repo.create(
        receipt_id=receipt_id,
        uid=str(user.uid),
        app_id=app_id,
        thread_id=thread_id,
        event_type="yuxi.session.input.resume",
        intent_hash=intent_hash,
        turn_id=turn_id,
        run_id=parent_run_id,
    )
    result = await create_resume_run_view(
        agent_slug=thread["agent_id"],
        thread_id=thread_id,
        meta={"request_id": resume_request_id},
        current_uid=str(user.uid),
        db=db,
        resume=resume,
        created_by_run_id=parent_run_id,
        source="public_api",
        channel="api" if app_id else "web",
    )
    return {
        "object": "agent.session.event.accepted",
        "session_id": thread_id,
        "turn_id": turn_id,
        "request_id": resume_request_id,
        "run_id": result["run_id"],
        "status": "accepted",
    }


async def cancel_public_turn(
    *,
    thread_id: str,
    idempotency_key: str,
    user: User,
    app_id: str | None,
    db: AsyncSession,
    target_turn_id: str | None = None,
    target_run_id: str | None = None,
) -> dict:
    """固定当前 Turn 后取消其活跃执行及尚未派发的引导输入。"""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key 长度必须为 1 至 128")
    thread = await get_public_thread_scope(thread_id=thread_id, user=user, app_id=app_id, db=db)
    identity = json.dumps([str(user.uid), app_id, thread_id, idempotency_key], separators=(",", ":"))
    receipt_id = hash_id("pubevt_", identity, length=64)
    intent = (
        "agent.session.input.cancel"
        if target_turn_id is None and target_run_id is None
        else json.dumps(["agent.session.input.cancel", target_turn_id, target_run_id], separators=(",", ":"))
    )
    intent_hash = hashlib.sha256(intent.encode()).hexdigest()
    receipt_repo = AgentSessionInputReceiptRepository(db)

    existing = await receipt_repo.get(receipt_id)
    if existing is not None:
        return _checked_cancel_replay(existing, thread_id, str(user.uid), app_id, intent_hash)

    await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
    existing = await receipt_repo.get(receipt_id)
    if existing is not None:
        return _checked_cancel_replay(existing, thread_id, str(user.uid), app_id, intent_hash)

    session = await get_public_session(thread_id=thread_id, user=user, app_id=app_id, db=db)
    if session["status"] == "requires_action":
        raise HTTPException(status_code=409, detail="等待操作结果的 Turn 暂不支持取消")

    turn_id = session.get("turn_id") if session["status"] == "in_progress" else None
    if (target_turn_id is not None and target_turn_id != turn_id) or (target_run_id is not None and turn_id is None):
        raise HTTPException(status_code=409, detail="取消目标已不是当前 Turn")
    run_id = None
    cancelled_run_ids: list[str] = []
    dispatch_after_commit = False
    if turn_id:
        turn_repo = AgentTurnRepository(db)
        turn = await turn_repo.get(turn_id)
        if turn is None or (turn.uid, turn.app_id, turn.conversation_thread_id) != (str(user.uid), app_id, thread_id):
            raise HTTPException(status_code=409, detail="当前 Turn 归属不一致")

        requests = await turn_repo.list_requests(turn_id)
        runs = await turn_repo.list_runs(turn_id)
        active = next((run for run in reversed(runs) if run.status not in TERMINAL_RUN_STATUSES), None)
        if target_run_id is not None and (active is None or active.id != target_run_id):
            raise HTTPException(status_code=409, detail="取消目标 Run 已变化")
        for request in requests:
            if request.status != "queued":
                continue
            locked = await AgentRunRequestRepository(db).lock_by_request_id(request.request_id)
            if locked is None or locked.turn_id != turn_id or locked.status != "queued":
                raise HTTPException(status_code=409, detail="待取消输入的 Turn 关联已改变")
            locked.status = "cancelled"
            locked.updated_at = utc_now_naive()
            message = await db.get(Message, locked.input_message_id)
            if message is not None:
                message.delivery_status = "cancelled"

        if active is not None:
            run, cancelled_run_ids = await AgentRunRepository(db).request_cancel_execution_tree(
                run_id=active.id, uid=str(user.uid), cascade_descendants=True
            )
            if not cancelled_run_ids:
                if target_run_id is not None:
                    raise HTTPException(status_code=409, detail="取消目标 Run 已结束")
                run = None
            run_id = run.id if run else None
        else:
            turn.cancelled_at = utc_now_naive()
            dispatch_after_commit = True

    receipt = await receipt_repo.create(
        receipt_id=receipt_id,
        uid=str(user.uid),
        app_id=app_id,
        thread_id=thread_id,
        event_type="agent.session.input.cancel",
        intent_hash=intent_hash,
        turn_id=turn_id,
        run_id=run_id,
    )
    await db.commit()
    if cancelled_run_ids:
        await publish_cancel_signals(cancelled_run_ids)
    if dispatch_after_commit:
        await dispatch_next_request(uid=str(user.uid), agent_slug=thread["agent_id"], thread_id=thread_id)
    return _cancel_receipt_response(receipt)


def _checked_cancel_replay(receipt, thread_id: str, uid: str, app_id: str | None, intent_hash: str) -> dict:
    """重试只能返回首次固定目标，不重新解析 Session 当前 Turn。"""
    if (
        receipt.conversation_thread_id,
        receipt.uid,
        receipt.app_id,
        receipt.event_type,
        receipt.intent_hash,
    ) != (thread_id, uid, app_id, "agent.session.input.cancel", intent_hash):
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 输入")
    return _cancel_receipt_response(receipt)


async def _resume_replay(
    receipt,
    thread_id: str,
    uid: str,
    app_id: str | None,
    turn_id: str,
    parent_run_id: str,
    intent_hash: str,
    request_id: str,
    db: AsyncSession,
) -> dict:
    """恢复事件重试只返回第一次创建的恢复 Run。"""
    if (
        receipt.conversation_thread_id,
        receipt.uid,
        receipt.app_id,
        receipt.event_type,
        receipt.intent_hash,
        receipt.turn_id,
        receipt.run_id,
    ) != (thread_id, uid, app_id, "yuxi.session.input.resume", intent_hash, turn_id, parent_run_id):
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他 Session 输入")
    run = await AgentRunRepository(db).get_run_by_request_id(request_id)
    if run is None or (run.uid, run.app_id, run.turn_id) != (uid, app_id, turn_id):
        raise HTTPException(status_code=409, detail="已接收恢复事件缺少对应 Run")
    return {
        "object": "agent.session.event.accepted",
        "session_id": thread_id,
        "turn_id": turn_id,
        "request_id": request_id,
        "run_id": run.id,
        "status": "accepted",
    }


def _cancel_receipt_response(receipt) -> dict:
    """控制输入的接收回执不推测异步终态。"""
    return {
        "object": "agent.session.event.accepted",
        "session_id": receipt.conversation_thread_id,
        "turn_id": receipt.turn_id,
        "run_id": receipt.run_id,
        "status": "accepted",
    }
