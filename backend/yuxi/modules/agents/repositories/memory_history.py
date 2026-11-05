"""Memory 历史的用户作用域、定位和原始行查询。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.messages import (
    MESSAGE_SEARCH_EXCLUDED_TYPES,
    MESSAGE_SEARCH_ROLES,
    message_search_conditions,
    state_proven_model_tool_call_condition,
)


class MemoryHistoryRepository:
    """只返回已按用户与消息可见性筛选的持久行。"""

    def __init__(self, db: AsyncSession):
        """绑定查询事务。"""
        self.db = db

    async def search(self, *, uid: str, query: str, limit: int) -> list:
        """读取名称与正文匹配的有界历史行。"""
        result = await self.db.execute(
            select(
                Session.thread_id,
                Session.title,
                Message.id,
                Message.role,
                Message.content,
                Message.extra_metadata["input_source"].label("input_source"),
            )
            .join(Session, Session.id == Message.session_record_id)
            .where(
                *self._memory_session_conditions(uid),
                *message_search_conditions(query),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )

        return list(result.all())

    async def read(self, *, uid: str, thread_id: str, message_id: int | None, limit: int, include_tools: bool):
        """验证线程与定位消息，读取前后窗口。"""
        session_result = await self.db.execute(
            select(Session.id, Session.thread_id, Session.title).where(
                Session.thread_id == str(thread_id),
                *self._memory_session_conditions(uid),
            )
        )
        agent_session = session_result.one_or_none()
        if agent_session is None:
            raise ValueError("历史线程不存在或不可见")

        message_type_condition = or_(
            Message.message_type.is_(None),
            Message.message_type.notin_(MESSAGE_SEARCH_EXCLUDED_TYPES),
        )
        if include_tools:
            message_type_condition = or_(message_type_condition, state_proven_model_tool_call_condition())
        message_conditions = [
            Message.session_record_id == agent_session.id,
            Message.role.in_(MESSAGE_SEARCH_ROLES),
            message_type_condition,
        ]
        if message_id is None:
            query = (
                self._memory_message_select().where(*message_conditions).order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
            )
            result = await self.db.execute(query)
            rows = list(result.all())
            rows.reverse()
        else:
            anchor_id = int(message_id)
            anchor_result = await self.db.execute(select(Message.id).where(Message.id == anchor_id, *message_conditions))
            if anchor_result.scalar_one_or_none() is None:
                raise ValueError("历史消息不存在或不属于该线程")

            before_limit = (limit + 1) // 2
            before_query = (
                self._memory_message_select()
                .where(*message_conditions, Message.id <= anchor_id)
                .order_by(Message.id.desc())
                .limit(before_limit)
            )
            before_result = await self.db.execute(before_query)
            before = list(before_result.all())
            before.reverse()

            after_limit = limit - len(before)
            after = []
            if after_limit:
                after_query = (
                    self._memory_message_select()
                    .where(*message_conditions, Message.id > anchor_id)
                    .order_by(Message.id.asc())
                    .limit(after_limit)
                )
                after_result = await self.db.execute(after_query)
                after = list(after_result.all())
            rows = before + after

        return agent_session, rows

    def _memory_session_conditions(self, uid: str) -> list:
        """构建用户可见主 Agent Session 条件。"""
        return [
            Session.uid == str(uid),
            Session.status == "active",
        ]

    @staticmethod
    def _memory_message_select():
        """选择历史协议需要的消息字段。"""
        return select(
            Message.id,
            Message.role,
            Message.content,
            Message.extra_metadata["input_source"].label("input_source"),
        )
