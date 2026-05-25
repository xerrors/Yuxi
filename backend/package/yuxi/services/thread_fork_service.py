"""会话分叉 (Fork) 服务 —— 克隆 checkpoint 状态 + 复制业务消息到新会话

全部使用 SQLAlchemy ORM / expression language，无 text() 裸 SQL。
"""

import uuid as uuid_lib

from sqlalchemy import func as sa_func, insert as sa_insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.checkpoint_repository import (
    build_ancestors_cte,
    select_checkpoint_entry,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.checkpoint_tables import (
    checkpoint_blobs,
    checkpoints,
    checkpoint_writes,
)
from yuxi.storage.postgres.models_business import (
    Conversation,
    ConversationStats,
    Message,
    MessageFeedback,
    ToolCall,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


class ForkValidationError(Exception):
    """Fork 请求参数无效"""


async def _get_message(db: AsyncSession, message_id: int) -> Message | None:
    result = await db.execute(select(Message).where(Message.id == message_id))
    return result.scalar_one_or_none()


async def _clone_physical_files(thread_id_a: str, thread_id_b: str) -> None:
    """Phase 1: 克隆物理文件（Sandbox + MinIO）。

    当前为占位实现，等待 Sandbox Provisioner / MinIO 集成。
    """
    logger.info("Physical file clone skipped (not yet implemented): %s -> %s", thread_id_a, thread_id_b)


async def _cleanup_cloned_files(thread_id_b: str) -> None:
    """Best-effort 清理克隆失败后的残留物理文件。"""
    logger.info("Physical file cleanup skipped (not yet implemented): %s", thread_id_b)


async def fork_thread(
    db: AsyncSession,
    thread_id_a: str,
    message_id: int,
    title: str | None,
    user_id: str,
) -> dict:
    """执行 Fork 操作：从原会话指定消息处分叉出新会话。

    流程：Phase 1 物理文件克隆 → Phase 2 数据库事务克隆。
    """
    conv_repo = ConversationRepository(db)
    thread_id_b = str(uuid_lib.uuid4())

    # ---- Phase 1: 物理资源克隆 ----
    try:
        await _clone_physical_files(thread_id_a, thread_id_b)
    except Exception as e:
        raise ForkValidationError(f"文件克隆失败: {e}")

    # ---- 校验原会话 ----
    conversation_a = await conv_repo.get_conversation_by_thread_id(thread_id_a)
    if not conversation_a or conversation_a.user_id != str(user_id) or conversation_a.status == "deleted":
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("对话线程不存在")
    conv_id_a = conversation_a.id

    # ---- 检查是否存在 checkpoint（旧会话可能没有） ----
    ckpt_count_result = await db.execute(
        select(sa_func.count()).select_from(checkpoints).where(checkpoints.c.thread_id == thread_id_a)
    )
    if ckpt_count_result.scalar() == 0:
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("此对话没有保存 AI 推理记录，无法分叉（可能是较早创建的会话）")

    # ---- 加锁原会话 ----
    await db.execute(
        select(Conversation.id).where(Conversation.thread_id == thread_id_a).with_for_update()
    )

    # ---- 定位目标消息与 request_id ----
    target_msg = await _get_message(db, message_id)
    if not target_msg or target_msg.conversation_id != conv_id_a:
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("消息不存在或不属于该会话")

    # ---- 计算克隆边界 ----
    # 核心语义：用户在哪 fork，就 fork 哪里（含）之前的全部内容
    # 前端 fork 按钮只在 assistant 消息上，传来的 message_id 是 AI 回复的 id
    # user 消息不支持 fork（只有 undo），所以只处理 assistant 消息场景
    #
    # 消息序列：Q1 → A1 → Q2 → A2 → Q3 → A3(用户点fork)
    # 期望结果：克隆 Q1+A1+Q2+A2+Q3+A3 = 6 条，AI 记忆也应该是 6 条
    #
    # 关键：消息边界和 checkpoint 边界必须包含相同轮次

    if target_msg.role != "assistant":
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("Fork 仅支持在 AI 回复消息上操作")

    # Fork 点是 AI 回复 → 包含这条 AI 回复及其之前的所有消息
    fork_boundary_id = target_msg.id

    # 找到这条 AI 回复对应的 user 消息(Q3)
    user_msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id_a, Message.role == "user", Message.id < message_id)
        .order_by(Message.id.desc())
        .limit(1),
    )
    user_msg = user_msg_result.scalar_one_or_none()
    if not user_msg:
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("未找到对应的用户消息")

    target_request_id = (user_msg.extra_metadata or {}).get("request_id")
    if not target_request_id:
        await _cleanup_cloned_files(thread_id_b)
        raise ForkValidationError("目标消息缺少 request_id，无法定位 checkpoint")

    # ---- 定位包含 A3 状态的 checkpoint ----
    # 优先查找 source='output' 的 checkpoint（包含 AI 回复的完整状态）
    output_ckpt_result = await db.execute(
        select(checkpoints.c.checkpoint_id)
        .where(
            checkpoints.c.thread_id == thread_id_a,
            checkpoints.c.metadata["request_id"].astext == target_request_id,
            checkpoints.c.metadata["source"].astext == "output",
        )
        .limit(1)
    )
    output_ckpt_row = output_ckpt_result.fetchone()

    if output_ckpt_row and output_ckpt_row[0]:
        # 有 output checkpoint，直接用
        target_checkpoint_id = output_ckpt_row[0]
    else:
        # 没有 output checkpoint（LangGraph 配置问题），需要换思路：
        # ckpt_in_Q3 的状态 = Q1+A1+Q2+A2（不含 A3）
        # ckpt_in_Q4 的状态 = Q1+A1+Q2+A2+Q3+A3（含 A3）
        # 所以要找 ckpt_in_Q3 的"子节点"（parent 指向 ckpt_in_Q3 的那条 checkpoint）
        entry_result = await db.execute(
            select_checkpoint_entry(thread_id_a, target_request_id)
        )
        entry_row = entry_result.fetchone()
        if not entry_row or not entry_row[0]:
            await _cleanup_cloned_files(thread_id_b)
            raise ForkValidationError("该消息之前没有可用的 AI 推理状态")

        # 找 ckpt_in_Q3 的子节点（状态包含 A3）
        child_ckpt_result = await db.execute(
            select(checkpoints.c.checkpoint_id)
            .where(
                checkpoints.c.thread_id == thread_id_a,
                checkpoints.c.parent_checkpoint_id == entry_row[0],
            )
            .limit(1)
        )
        child_ckpt_row = child_ckpt_result.fetchone()
        if child_ckpt_row and child_ckpt_row[0]:
            # 找到子节点，用子节点（状态包含 A3）
            target_checkpoint_id = child_ckpt_row[0]
        else:
            # 没有子节点，说明 A3 是最后一条消息，还没有后续的 input checkpoint
            # 此时用 ckpt_in_Q3 本身 + checkpoint_writes 来恢复状态
            # checkpoint_writes 表里存储了 A3 的输出，会被一并克隆
            target_checkpoint_id = entry_row[0]

    # ---- 递归 CTE 向上查找所有祖先 checkpoint ----
    ancestors_cte = build_ancestors_cte(thread_id_a, target_checkpoint_id)
    ancestors_result = await db.execute(select(ancestors_cte.c.checkpoint_id))
    ancestor_ids = [row[0] for row in ancestors_result.fetchall()]
    cloned_checkpoint_count = len(ancestor_ids)

    # ---- Phase 2: 数据库事务级克隆 ----
    try:
        # 1. 创建新会话
        fork_title = title or f"{conversation_a.title or '未命名'}_分叉"
        new_conversation = Conversation(
            thread_id=thread_id_b,
            user_id=str(user_id),
            agent_id=conversation_a.agent_id,
            title=fork_title,
            status="active",
            extra_metadata=dict(conversation_a.extra_metadata or {}),
        )
        db.add(new_conversation)
        await db.flush()
        conv_id_b = new_conversation.id

        # 2. 克隆 checkpoints —— INSERT ... SELECT，thread_id 替换为新 thread_id_b
        await db.execute(
            sa_insert(checkpoints).from_select(
                ["thread_id", "checkpoint_ns", "checkpoint_id",
                 "parent_checkpoint_id", "type", "checkpoint", "metadata"],
                select(
                    literal(thread_id_b).label("thread_id"),
                    checkpoints.c.checkpoint_ns,
                    checkpoints.c.checkpoint_id,
                    checkpoints.c.parent_checkpoint_id,
                    checkpoints.c.type,
                    checkpoints.c.checkpoint,
                    checkpoints.c.metadata,
                ).where(
                    checkpoints.c.thread_id == thread_id_a,
                    checkpoints.c.checkpoint_id.in_(ancestor_ids),
                ),
            )
        )

        # 3. 克隆 checkpoint_writes，thread_id 替换为新 thread_id_b
        await db.execute(
            sa_insert(checkpoint_writes).from_select(
                ["thread_id", "checkpoint_ns", "checkpoint_id",
                 "task_id", "idx", "channel", "type", "blob", "task_path"],
                select(
                    literal(thread_id_b).label("thread_id"),
                    checkpoint_writes.c.checkpoint_ns,
                    checkpoint_writes.c.checkpoint_id,
                    checkpoint_writes.c.task_id,
                    checkpoint_writes.c.idx,
                    checkpoint_writes.c.channel,
                    checkpoint_writes.c.type,
                    checkpoint_writes.c.blob,
                    checkpoint_writes.c.task_path,
                ).where(
                    checkpoint_writes.c.thread_id == thread_id_a,
                    checkpoint_writes.c.checkpoint_id.in_(ancestor_ids),
                ),
            )
        )

        # 4. 克隆 checkpoint_blobs —— 从祖先 checkpoint 提取 (channel, version)，去重后克隆
        ancestor_ckpts_result = await db.execute(
            select(checkpoints.c.checkpoint).where(
                checkpoints.c.thread_id == thread_id_a,
                checkpoints.c.checkpoint_id.in_(ancestor_ids),
            )
        )
        blob_pairs: set[tuple[str, str]] = set()
        for (ckpt_json,) in ancestor_ckpts_result.fetchall():
            channel_versions = (ckpt_json or {}).get("channel_versions", {}) or {}
            for ch, ver in channel_versions.items():
                blob_pairs.add((ch, ver))

        if blob_pairs:
            # 查询原 thread 下匹配的 blobs
            conditions = []
            for ch, ver in blob_pairs:
                conditions.append((checkpoint_blobs.c.channel == ch) & (checkpoint_blobs.c.version == ver))
            blob_rows_result = await db.execute(
                select(checkpoint_blobs).where(
                    checkpoint_blobs.c.thread_id == thread_id_a,
                    conditions[0] if len(conditions) == 1 else None,
                )
            )
            # 对多种 pair 的情况，逐对查询
            if len(blob_pairs) > 1:
                all_rows = []
                for ch, ver in blob_pairs:
                    pair_result = await db.execute(
                        select(checkpoint_blobs).where(
                            checkpoint_blobs.c.thread_id == thread_id_a,
                            checkpoint_blobs.c.channel == ch,
                            checkpoint_blobs.c.version == ver,
                        )
                    )
                    all_rows.extend(pair_result.fetchall())
            else:
                all_rows = blob_rows_result.fetchall()

            # 插入新 thread_id
            for row in all_rows:
                await db.execute(
                    sa_insert(checkpoint_blobs).values(
                        thread_id=thread_id_b,
                        checkpoint_ns=row.checkpoint_ns,
                        channel=row.channel,
                        version=row.version,
                        type=row.type,
                        blob=row.blob,
                    )
                )

        # 5. 查询源消息（Python 侧过滤已删除），包含 fork 点 user 消息对应的 assistant 回复
        src_messages_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id_a, Message.id <= fork_boundary_id)
            .order_by(Message.id.asc()),
        )
        src_messages = [
            m for m in src_messages_result.scalars().all()
            if (m.extra_metadata or {}).get("is_deleted") != "true"
        ]

        # 6. 逐条克隆消息，建立 old_id → new_id 映射
        id_map: dict[int, int] = {}
        for msg in src_messages:
            new_msg = Message(
                conversation_id=conv_id_b,
                role=msg.role,
                content=msg.content,
                message_type=msg.message_type,
                image_content=msg.image_content,
                extra_metadata=dict(msg.extra_metadata or {}),
                token_count=msg.token_count,
                created_at=msg.created_at,
            )
            db.add(new_msg)
            await db.flush()
            id_map[msg.id] = new_msg.id

        # 7. 克隆 tool_calls
        if src_messages:
            old_ids = list(id_map.keys())
            src_tool_calls_result = await db.execute(
                select(ToolCall).where(ToolCall.message_id.in_(old_ids))
            )
            for tc in src_tool_calls_result.scalars().all():
                db.add(ToolCall(
                    message_id=id_map[tc.message_id],
                    tool_name=tc.tool_name,
                    tool_input=tc.tool_input,
                    tool_output=tc.tool_output,
                    status=tc.status,
                    error_message=tc.error_message,
                    langgraph_tool_call_id=tc.langgraph_tool_call_id,
                    created_at=tc.created_at,
                ))

        # 8. 克隆 message_feedbacks
        if src_messages:
            src_feedbacks_result = await db.execute(
                select(MessageFeedback).where(MessageFeedback.message_id.in_(old_ids))
            )
            for fb in src_feedbacks_result.scalars().all():
                db.add(MessageFeedback(
                    message_id=id_map[fb.message_id],
                    user_id=fb.user_id,
                    rating=fb.rating,
                    reason=fb.reason,
                    created_at=fb.created_at,
                ))

        # 9. 计算克隆统计（Python 侧）
        cloned_msg_count = len(src_messages)
        cloned_token_count = sum(m.token_count or 0 for m in src_messages)

        # 10. 初始化新会话统计
        stats_a_result = await db.execute(
            select(ConversationStats).where(ConversationStats.conversation_id == conv_id_a)
        )
        stats_a = stats_a_result.scalar_one_or_none()

        db.add(ConversationStats(
            conversation_id=conv_id_b,
            message_count=cloned_msg_count,
            total_tokens=cloned_token_count,
            model_used=stats_a.model_used if stats_a else None,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        ))

        await db.flush()

        logger.info(
            "Fork thread %s -> %s, cloned %d messages, %d checkpoints",
            thread_id_a, thread_id_b, cloned_msg_count, cloned_checkpoint_count,
        )

        return {
            "message": "分叉成功",
            "new_thread_id": thread_id_b,
            "cloned_message_count": cloned_msg_count,
            "cloned_checkpoint_count": cloned_checkpoint_count,
        }

    except Exception:
        await _cleanup_cloned_files(thread_id_b)
        raise
