"""Turn 身份及其明确关联的 Request/Run 查询。"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    AUDIT_MESSAGE_TYPES,
    AgentRun,
    AgentRunRequest,
    AgentTurn,
    Conversation,
    Message,
)


class AgentTurnRepository:
    """只保存轮次身份；执行状态从绑定的 Run 与 Request 投影。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, *, turn_id: str, thread_id: str, uid: str, app_id: str | None) -> AgentTurn:
        """在接收首个普通输入的事务中创建 Turn。"""
        turn = AgentTurn(id=turn_id, conversation_thread_id=thread_id, uid=uid, app_id=app_id)
        self.db.add(turn)
        await self.db.flush()
        return turn

    async def get(self, turn_id: str) -> AgentTurn | None:
        """按不可变 ID 读取 Turn。"""
        return await self.db.get(AgentTurn, turn_id)

    async def get_latest_for_thread(self, *, thread_id: str, uid: str, app_id: str | None) -> AgentTurn | None:
        """读取线程最近建立的 Turn，供空闲 Session 状态投影。"""
        result = await self.db.execute(
            select(AgentTurn)
            .where(
                AgentTurn.conversation_thread_id == thread_id,
                AgentTurn.uid == uid,
                AgentTurn.app_id == app_id,
            )
            .order_by(AgentTurn.created_at.desc(), AgentTurn.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_requests(self, turn_id: str) -> list[AgentRunRequest]:
        """读取明确绑定到本轮的输入请求。"""
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(AgentRunRequest.turn_id == turn_id)
            .order_by(AgentRunRequest.id)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def list_runs(self, turn_id: str) -> list[AgentRun]:
        """读取明确绑定到本轮的顶层执行段。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.turn_id == turn_id, AgentRun.run_type.in_(("chat", "resume")))
            .order_by(AgentRun.created_at, AgentRun.id)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def list_messages(
        self, *, thread_id: str, request_ids: list[str], run_ids: list[str], after_id: int, limit: int
    ) -> list[Message]:
        """只读取本轮输入与明确发布的最终助手输出。"""
        associations = []
        if request_ids:
            associations.append(and_(Message.role == "user", Message.request_id.in_(request_ids)))
        if run_ids:
            associations.append(
                and_(
                    Message.role == "assistant",
                    Message.id.in_(
                        select(AgentRun.output_message_id).where(
                            AgentRun.id.in_(run_ids), AgentRun.output_message_id.is_not(None)
                        )
                    ),
                )
            )
        if not associations:
            return []
        result = await self.db.execute(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.thread_id == thread_id,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                Message.id > after_id,
                or_(*associations),
            )
            .order_by(Message.id)
            .limit(limit)
        )
        return list(result.scalars())
