"""会话回退 (Undo) 服务 —— 逻辑归档 checkpoint + 标记删除业务消息

全部使用 SQLAlchemy ORM / expression language，无 text() 裸 SQL。
"""

import time

from sqlalchemy import and_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.checkpoint_repository import (
    build_descendants_cte,
    select_checkpoint_entry,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.checkpoint_tables import (
    checkpoint_blobs,
    checkpoints,
    checkpoint_writes,
)
from yuxi.storage.postgres.models_business import (
    AgentRun,
    Conversation,
    ConversationStats,
    Message,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


class UndoConflictError(Exception):
    """智能体正在运行中，拒绝 Undo"""


class UndoValidationError(Exception):
    """Undo 请求参数无效"""


async def _get_message(db: AsyncSession, message_id: int) -> Message | None:
    result = await db.execute(select(Message).where(Message.id == message_id))
    return result.scalar_one_or_none()


async def undo_thread(
    db: AsyncSession,
    thread_id: str,
    message_id: int,
    user_id: str,
) -> dict:
    """执行 Undo 操作，所有步骤在同一事务内完成。"""
    conv_repo = ConversationRepository(db)

    # ---- 1. 校验会话 ----
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(user_id) or conversation.status == "deleted":
        raise UndoValidationError("对话线程不存在")
    conv_id = conversation.id

    # ---- 2. 加行级排他锁 + 校验活跃 run ----
    await db.execute(
        select(Conversation.id).where(Conversation.thread_id == thread_id).with_for_update()
    )
    active_run_result = await db.execute(
        select(AgentRun.id)
        .where(
            AgentRun.thread_id == thread_id,
            AgentRun.status.in_(["running", "pending"]),
        )
        .with_for_update(),
    )
    if active_run_result.first() is not None:
        await db.rollback()
        raise UndoConflictError("智能体正在思考或执行任务中，请稍后再试")

    # ---- 3. 定位目标消息与 request_id ----
    target_msg = await _get_message(db, message_id)
    if not target_msg or target_msg.conversation_id != conv_id:
        raise UndoValidationError("消息不存在或不属于该会话")

    if target_msg.role == "assistant":
        user_msgs = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.role == "user", Message.id <= message_id)
            .order_by(Message.id.desc())
            .limit(1),
        )
        target_msg = user_msgs.scalar_one_or_none()
        if not target_msg:
            raise UndoValidationError("未找到对应的用户消息")
        message_id = target_msg.id

    target_request_id = (target_msg.extra_metadata or {}).get("request_id")
    if not target_request_id:
        raise UndoValidationError("目标消息缺少 request_id，无法定位 checkpoint")

    # ---- 4. 定位 checkpoint 入口 ----
    entry_result = await db.execute(select_checkpoint_entry(thread_id, target_request_id))
    entry_row = entry_result.fetchone()
    if not entry_row:
        raise UndoValidationError("未找到对应的 AI 推理状态记录")
    start_delete_checkpoint_id = entry_row[0]

    # ---- 5. 递归 CTE 查找所有后代 checkpoint ----
    descendants_cte = build_descendants_cte(thread_id, start_delete_checkpoint_id)
    descendants_result = await db.execute(select(descendants_cte.c.checkpoint_id))
    descendant_ids = [row[0] for row in descendants_result.fetchall()]
    if not descendant_ids:
        raise UndoValidationError("没有可撤销的 AI 推理状态")

    # ---- 6. 计算归档 ID ----
    thread_id_archived = f"{thread_id}_archived_{int(time.time())}"

    # ---- 7. 归档 checkpoint_writes ----
    await db.execute(
        sa_update(checkpoint_writes)
        .where(checkpoint_writes.c.thread_id == thread_id, checkpoint_writes.c.checkpoint_id.in_(descendant_ids))
        .values(thread_id=thread_id_archived)
    )

    # ---- 8. 归档 checkpoints ----
    await db.execute(
        sa_update(checkpoints)
        .where(checkpoints.c.thread_id == thread_id, checkpoints.c.checkpoint_id.in_(descendant_ids))
        .values(thread_id=thread_id_archived)
    )

    # ---- 9. 精确归档 checkpoint_blobs（Python 侧计算差集） ----
    # 现在被归档的 checkpoint 在 thread_id_archived 下，保留的在 thread_id 下
    archived_ckpts = await db.execute(
        select(checkpoints.c.checkpoint).where(checkpoints.c.thread_id == thread_id_archived)
    )
    kept_ckpts = await db.execute(
        select(checkpoints.c.checkpoint).where(checkpoints.c.thread_id == thread_id)
    )

    def _extract_channel_versions(rows):
        """从 checkpoint JSONB 行中提取所有 (channel, version) 对"""
        pairs = set()
        for (ckpt_json,) in rows:
            channel_versions = (ckpt_json or {}).get("channel_versions", {}) or {}
            for ch, ver in channel_versions.items():
                pairs.add((ch, ver))
        return pairs

    archived_pairs = _extract_channel_versions(archived_ckpts.fetchall())
    kept_pairs = _extract_channel_versions(kept_ckpts.fetchall())
    to_archive_pairs = archived_pairs - kept_pairs

    # 批量更新 blob（每个 pair 一条 UPDATE）
    for channel, version in to_archive_pairs:
        await db.execute(
            sa_update(checkpoint_blobs)
            .where(
                checkpoint_blobs.c.thread_id == thread_id,
                checkpoint_blobs.c.channel == channel,
                checkpoint_blobs.c.version == version,
            )
            .values(thread_id=thread_id_archived)
        )

    # ---- 10. 标记 agent_runs 为 cancelled ----
    # AgentRun 没有 extra_metadata 列，用 error_message 记录取消原因
    await db.execute(
        sa_update(AgentRun)
        .where(
            AgentRun.thread_id == thread_id,
            AgentRun.request_id.in_(
                select(Message.extra_metadata["request_id"].astext).where(
                    Message.conversation_id == conv_id,
                    Message.id >= message_id,
                )
            ),
        )
        .values(status="cancelled", error_message="cancelled_by_user_undo", updated_at=utc_now_naive())
    )

    # ---- 11. Python 侧标记消息为逻辑删除 ----
    msgs_result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id, Message.id >= message_id)
    )
    msgs_to_delete = msgs_result.scalars().all()
    deleted_message_count = len(msgs_to_delete)

    for msg in msgs_to_delete:
        meta = dict(msg.extra_metadata or {})
        meta["is_deleted"] = "true"
        msg.extra_metadata = meta

    # ---- 12. 重新计算会话统计（Python 侧过滤） ----
    all_msgs_result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id)
    )
    all_msgs = all_msgs_result.scalars().all()

    valid_msgs = [
        m for m in all_msgs if (m.extra_metadata or {}).get("is_deleted") != "true"
    ]
    valid_message_count = len(valid_msgs)
    valid_total_tokens = sum(m.token_count or 0 for m in valid_msgs)

    await db.execute(
        sa_update(ConversationStats)
        .where(ConversationStats.conversation_id == conv_id)
        .values(
            message_count=valid_message_count,
            total_tokens=valid_total_tokens,
            updated_at=utc_now_naive(),
        )
    )

    # ---- 13. 计算 cancelled run 数量 ----
    ar_count_result = await db.execute(
        select(AgentRun.id).where(
            AgentRun.thread_id == thread_id,
            AgentRun.status == "cancelled",
        )
    )
    deleted_agent_run_count = len(ar_count_result.fetchall())

    # ---- 14. 回退点消息 ID ----
    prev_msg_result = await db.execute(
        select(Message.id)
        .where(Message.conversation_id == conv_id, Message.id < message_id)
        .order_by(Message.id.desc())
        .limit(1),
    )
    undo_point_msg_id = prev_msg_result.scalar_one_or_none()

    await db.flush()

    logger.info(
        "Undo thread=%s, archived=%s, deleted_msg=%d, deleted_runs=%d",
        thread_id, thread_id_archived, deleted_message_count, deleted_agent_run_count,
    )

    return {
        "message": "回滚成功",
        "thread_id": thread_id,
        "deleted_message_count": deleted_message_count,
        "deleted_agent_run_count": deleted_agent_run_count,
        "undo_point_message_id": undo_point_msg_id,
    }
