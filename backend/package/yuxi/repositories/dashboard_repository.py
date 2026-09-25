"""Dashboard 统计读模型的数据访问层。"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, String, case, cast, distinct, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_repository import AgentRepository
from yuxi.storage.minio.client import normalize_public_minio_url
from yuxi.storage.postgres.models_business import (
    AUDIT_MESSAGE_TYPES,
    Agent,
    AgentRun,
    Conversation,
    ConversationStats,
    Message,
    MessageFeedback,
    ToolCall,
    User,
)
from yuxi.utils.datetime_utils import UTC, ensure_shanghai, format_utc_datetime, shanghai_now, utc_now


class DashboardRepository:
    """集中封装 Dashboard 的跨表统计查询与读模型聚合。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    @staticmethod
    def _time_group_format(column: Any, time_range: str) -> Any:
        """生成使用上海时区显示的 PostgreSQL 时间分组表达式。"""
        if time_range == "14hours":
            return func.to_char(column + text("INTERVAL '8 hours'"), "YYYY-MM-DD HH24:00")
        if time_range == "14weeks":
            return func.to_char(column + text("INTERVAL '8 hours'"), "YYYY-IW")
        return func.to_char(column + text("INTERVAL '8 hours'"), "YYYY-MM-DD")

    def _shanghai_date_group(self, column: Any) -> Any:
        """按上海日历日生成 PostgreSQL/SQLite 兼容分组表达式。"""
        bind = self.db_session.bind
        if bind is not None and bind.dialect.name == "sqlite":
            return func.date(column, "+8 hours")
        return func.date(column + text("INTERVAL '8 hours'"))

    @staticmethod
    def _conversation_token_totals(conversation_ids: list[int] | None = None):
        """按会话汇总 Run 实测用量，保留缺失标记和无 Run 历史汇总。"""
        reported = AgentRun.token_usage["usage_reported_call_count"].as_integer() > 0
        complete = AgentRun.token_usage["complete"].as_boolean().is_(True)
        value = AgentRun.token_usage["total"]["total_tokens"].as_integer()
        run_totals = (
            select(
                AgentRun.conversation_id,
                func.sum(case((reported | complete, value), else_=None)).label("total_tokens"),
                func.min(case((complete & value.isnot(None), 1), else_=0)).label("complete"),
            )
            .where(AgentRun.conversation_id.in_(conversation_ids) if conversation_ids is not None else True)
            .group_by(AgentRun.conversation_id)
            .subquery()
        )
        has_runs = run_totals.c.conversation_id.isnot(None)
        legacy_tokens = func.nullif(ConversationStats.total_tokens, 0)
        return (
            select(
                Conversation.id.label("conversation_id"),
                case((has_runs, run_totals.c.total_tokens), else_=legacy_tokens).label("total_tokens"),
                case((has_runs, run_totals.c.complete == 1), else_=legacy_tokens.isnot(None)).label("complete"),
            )
            .where(Conversation.id.in_(conversation_ids) if conversation_ids is not None else True)
            .outerjoin(run_totals, Conversation.id == run_totals.c.conversation_id)
            .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
            .subquery()
        )

    async def get_conversation_token_usage(self, conversation_id: int) -> dict[str, Any]:
        """读取与会话列表一致的实测 Token 汇总。"""
        totals = self._conversation_token_totals([conversation_id])
        row = (
            await self.db_session.execute(
                select(totals.c.total_tokens, totals.c.complete).where(totals.c.conversation_id == conversation_id)
            )
        ).one()
        return {"total_tokens": row.total_tokens, "token_usage_complete": bool(row.complete)}

    async def list_conversations(
        self,
        *,
        uid: str | None = None,
        agent_id: str | None = None,
        status: str = "all",
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页查询 Dashboard 对话，并装配用户与 Agent 展示名称。"""
        filters = []
        if uid:
            filters.append(Conversation.uid == uid)
        if agent_id:
            filters.append(Conversation.agent_id == agent_id)
        if status and status != "all":
            filters.append(Conversation.status == status)
        else:
            filters.append(Conversation.status != "deleted")
        if search:
            search_term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Conversation.title.ilike(search_term),
                    Conversation.thread_id.ilike(search_term),
                    Conversation.uid.ilike(search_term),
                    User.username.ilike(search_term),
                )
            )

        total_result = await self.db_session.execute(
            select(func.count(Conversation.id))
            .select_from(Conversation)
            .outerjoin(User, Conversation.uid == User.uid)
            .where(*filters)
        )
        rows = (
            await self.db_session.execute(
                select(Conversation, ConversationStats, User)
                .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
                .outerjoin(User, Conversation.uid == User.uid)
                .where(*filters)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        token_totals = self._conversation_token_totals([conversation.id for conversation, _, _ in rows])
        usage_by_conversation = {
            row.conversation_id: row for row in (await self.db_session.execute(select(token_totals))).all()
        }
        agent_slugs = {conversation.agent_id for conversation, _, _ in rows if conversation.agent_id}
        agents_by_slug: dict[str, Agent] = {}
        if agent_slugs:
            agents = await AgentRepository(self.db_session).list_by_slugs(list(agent_slugs))
            agents_by_slug = {agent.slug: agent for agent in agents}

        items = []
        for conversation, stats, user in rows:
            usage = usage_by_conversation[conversation.id]
            agent = agents_by_slug.get(conversation.agent_id)
            items.append(
                {
                    "thread_id": conversation.thread_id,
                    "uid": conversation.uid,
                    "username": user.username if user else conversation.uid,
                    "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
                    "user_deleted": user is None or bool(user.is_deleted),
                    "agent_id": conversation.agent_id,
                    "agent_name": agent.name if agent else conversation.agent_id,
                    "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
                    "agent_deleted": agent is None,
                    "title": conversation.title,
                    "status": conversation.status,
                    "is_pinned": bool(conversation.is_pinned),
                    "message_count": stats.message_count if stats else 0,
                    "total_tokens": usage.total_tokens,
                    "token_usage_complete": bool(usage.complete),
                    "created_at": format_utc_datetime(conversation.created_at) or "",
                    "updated_at": format_utc_datetime(conversation.updated_at) or "",
                }
            )
        return {
            "items": items,
            "total": int(total_result.scalar() or 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_conversation_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        """读取完整会话审计可用的用户与 Agent 筛选项。"""
        user_rows = (
            await self.db_session.execute(
                select(Conversation.uid, User.username, User.avatar, User.is_deleted)
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .distinct()
            )
        ).all()
        agent_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, Agent.name, Agent.icon)
                .select_from(Conversation)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .distinct()
            )
        ).all()

        users = [
            {
                "uid": row.uid,
                "username": row.username or row.uid,
                "avatar": normalize_public_minio_url(row.avatar) if row.avatar else None,
                "is_deleted": row.username is None or bool(row.is_deleted),
            }
            for row in user_rows
        ]
        agents = [
            {
                "agent_id": row.agent_id,
                "agent_name": row.name or row.agent_id,
                "avatar": normalize_public_minio_url(row.icon) if row.icon else None,
                "is_deleted": row.name is None,
            }
            for row in agent_rows
        ]
        users.sort(key=lambda item: (item["is_deleted"], item["username"].lower()))
        agents.sort(key=lambda item: (item["is_deleted"], item["agent_name"].lower()))
        return {"users": users, "agents": agents}

    async def get_conversation_audit_metadata(self, conversation: Conversation) -> dict[str, Any]:
        """读取会话关联用户与 Agent 的当前审计状态。"""
        row = (
            await self.db_session.execute(
                select(User, Agent)
                .select_from(Conversation)
                .outerjoin(User, Conversation.uid == User.uid)
                .outerjoin(Agent, Conversation.agent_id == Agent.slug)
                .where(Conversation.id == conversation.id)
            )
        ).one()
        user, agent = row
        return {
            "username": user.username if user else conversation.uid,
            "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
            "user_deleted": user is None or bool(user.is_deleted),
            "agent_name": agent.name if agent else conversation.agent_id,
            "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
            "agent_deleted": agent is None,
        }

    async def get_user_activity_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """统计用户总量与近期开启对话的活跃用户。"""
        query_now = (now or utc_now()).replace(tzinfo=None)

        total_result = await self.db_session.execute(select(func.count(User.id)).where(User.is_deleted == 0))
        active_24h_result = await self.db_session.execute(
            select(func.count(distinct(User.id)))
            .select_from(Conversation)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Conversation.updated_at >= query_now - timedelta(days=1),
                Conversation.status.notin_(("deleted", "subagent")),
                User.is_deleted == 0,
            )
        )
        active_30d_result = await self.db_session.execute(
            select(func.count(distinct(User.id)))
            .select_from(Conversation)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Conversation.updated_at >= query_now - timedelta(days=30),
                Conversation.status.notin_(("deleted", "subagent")),
                User.is_deleted == 0,
            )
        )

        active_date = self._shanghai_date_group(Conversation.updated_at)
        daily_active_result = await self.db_session.execute(
            select(active_date.label("date"), func.count(distinct(User.id)).label("active_users"))
            .select_from(Conversation)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Conversation.updated_at >= query_now - timedelta(days=120),
                Conversation.updated_at < query_now,
                Conversation.status.notin_(("deleted", "subagent")),
                User.is_deleted == 0,
            )
            .group_by(active_date)
        )
        daily_active_by_date = {str(row.date): int(row.active_users or 0) for row in daily_active_result.all()}
        local_today = (query_now + timedelta(hours=8)).date()
        daily_active_users = []
        for day_offset in range(119, -1, -1):
            date = (local_today - timedelta(days=day_offset)).strftime("%Y-%m-%d")
            daily_active_users.append({"date": date, "active_users": daily_active_by_date.get(date, 0)})

        return {
            "total_users": total_result.scalar() or 0,
            "active_users_24h": active_24h_result.scalar() or 0,
            "active_users_30d": active_30d_result.scalar() or 0,
            "daily_active_users": daily_active_users,
        }

    async def get_tool_call_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """统计有效用户与非删除会话中的工具调用。"""
        query_now = (now or utc_now()).replace(tzinfo=None)
        valid_filters = [Conversation.status.notin_(("deleted", "subagent")), User.is_deleted == 0]
        total_result = await self.db_session.execute(
            select(func.count(ToolCall.id))
            .join(Message, ToolCall.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters)
        )
        successful_result = await self.db_session.execute(
            select(func.count(ToolCall.id))
            .join(Message, ToolCall.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters, ToolCall.status == "success")
        )
        total_calls = total_result.scalar() or 0
        successful_calls = successful_result.scalar() or 0

        most_used_result = await self.db_session.execute(
            select(ToolCall.tool_name, func.count(ToolCall.id).label("count"))
            .join(Message, ToolCall.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters)
            .group_by(ToolCall.tool_name)
            .order_by(func.count(ToolCall.id).desc())
            .limit(10)
        )
        error_result = await self.db_session.execute(
            select(ToolCall.tool_name, func.count(ToolCall.id).label("error_count"))
            .join(Message, ToolCall.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters, ToolCall.status == "error")
            .group_by(ToolCall.tool_name)
        )

        daily_tool_calls = []
        for day_offset in range(7):
            day_start = query_now - timedelta(days=day_offset + 1)
            day_end = query_now - timedelta(days=day_offset)
            daily_result = await self.db_session.execute(
                select(func.count(ToolCall.id))
                .join(Message, ToolCall.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    *valid_filters,
                    ToolCall.created_at >= day_start,
                    ToolCall.created_at < day_end,
                )
            )
            daily_tool_calls.append({"date": day_start.strftime("%Y-%m-%d"), "call_count": daily_result.scalar() or 0})

        return {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": total_calls - successful_calls,
            "success_rate": round(successful_calls / total_calls * 100, 2) if total_calls else 0,
            "most_used_tools": [{"tool_name": name, "count": count} for name, count in most_used_result.all()],
            "tool_error_distribution": {name: count for name, count in error_result.all()},
            "daily_tool_calls": list(reversed(daily_tool_calls)),
        }

    async def get_agent_analytics(self) -> dict[str, Any]:
        """汇总仍存在 Agent 在有效用户与非删除会话中的使用情况。"""
        agents = list((await self.db_session.execute(select(Agent).order_by(Agent.name.asc()))).scalars().all())
        valid_filters = [Conversation.status.notin_(("deleted", "subagent")), User.is_deleted == 0]

        conversation_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, func.count(Conversation.id))
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(*valid_filters)
                .group_by(Conversation.agent_id)
            )
        ).all()
        conversation_counts = {agent_id: int(count or 0) for agent_id, count in conversation_rows}

        feedback_rows = (
            await self.db_session.execute(
                select(
                    Conversation.agent_id,
                    func.count(MessageFeedback.id).label("total"),
                    func.sum(case((MessageFeedback.rating == "like", 1), else_=0)).label("positive"),
                )
                .join(Message, MessageFeedback.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    *valid_filters,
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                )
                .group_by(Conversation.agent_id)
            )
        ).all()
        feedback_by_agent = {row.agent_id: (int(row.total or 0), int(row.positive or 0)) for row in feedback_rows}

        tool_rows = (
            await self.db_session.execute(
                select(Conversation.agent_id, func.count(ToolCall.id))
                .join(Message, ToolCall.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(*valid_filters)
                .group_by(Conversation.agent_id)
            )
        ).all()
        tool_counts = {agent_id: int(count or 0) for agent_id, count in tool_rows}

        conversation_stats = []
        satisfaction_stats = []
        tool_usage = []
        for agent in agents:
            conversation_count = conversation_counts.get(agent.slug, 0)
            total_feedbacks, positive_feedbacks = feedback_by_agent.get(agent.slug, (0, 0))
            satisfaction_rate = round(positive_feedbacks / total_feedbacks * 100, 2) if total_feedbacks else 100
            conversation_stats.append({"agent_id": agent.slug, "conversation_count": conversation_count})
            satisfaction_stats.append(
                {
                    "agent_id": agent.slug,
                    "satisfaction_rate": satisfaction_rate,
                    "total_feedbacks": total_feedbacks,
                }
            )
            tool_usage.append({"agent_id": agent.slug, "tool_usage_count": tool_counts.get(agent.slug, 0)})

        return {
            "total_agents": len(agents),
            "agent_conversation_counts": conversation_stats,
            "agent_satisfaction_rates": satisfaction_stats,
            "agent_tool_usage": tool_usage,
            "agent_names": {agent.slug: agent.name for agent in agents},
        }

    async def get_basic_stats(self) -> dict[str, Any]:
        """读取有效用户与非删除会话的 Dashboard 基础计数。"""
        valid_filters = [Conversation.status.notin_(("deleted", "subagent")), User.is_deleted == 0]
        total_conversations_result = await self.db_session.execute(
            select(func.count(Conversation.id))
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters)
        )
        active_conversations_result = await self.db_session.execute(
            select(func.count(Conversation.id))
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*valid_filters, Conversation.status == "active")
        )
        total_messages_result = await self.db_session.execute(
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                *valid_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        total_users_result = await self.db_session.execute(select(func.count(User.id)).where(User.is_deleted == 0))
        total_feedbacks_result = await self.db_session.execute(
            select(func.count(MessageFeedback.id))
            .join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                *valid_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        like_count_result = await self.db_session.execute(
            select(func.count(MessageFeedback.id))
            .join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                *valid_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                MessageFeedback.rating == "like",
            )
        )
        total_feedbacks = total_feedbacks_result.scalar() or 0
        like_count = like_count_result.scalar() or 0
        return {
            "total_conversations": total_conversations_result.scalar() or 0,
            "active_conversations": active_conversations_result.scalar() or 0,
            "total_messages": total_messages_result.scalar() or 0,
            "total_users": total_users_result.scalar() or 0,
            "feedback_stats": {
                "total_feedbacks": total_feedbacks,
                "satisfaction_rate": round(like_count / total_feedbacks * 100, 2) if total_feedbacks else 100,
            },
        }

    async def list_feedbacks(
        self, *, rating: str | None, agent_id: str | None
    ) -> list[tuple[MessageFeedback, Message, Conversation, User | None]]:
        """按可选评分和智能体过滤反馈关联数据。"""
        query = (
            select(MessageFeedback, Message, Conversation, User)
            .join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, MessageFeedback.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Conversation.status.notin_(("deleted", "subagent")),
                User.is_deleted == 0,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        if rating and rating in {"like", "dislike"}:
            query = query.where(MessageFeedback.rating == rating)
        if agent_id:
            query = query.where(Conversation.agent_id == agent_id)
        query = query.order_by(MessageFeedback.created_at.desc())
        result = await self.db_session.execute(query)
        return list(result.all())

    async def get_call_timeseries(
        self,
        *,
        metric_type: str,
        time_range: str,
        now: datetime | None = None,
        local_now: datetime | None = None,
    ) -> dict[str, Any]:
        """查询并补齐十四个时间区间的调用分析序列。"""
        query_now = now or utc_now()
        query_local_now = local_now or shanghai_now()
        intervals = 14

        if time_range == "14hours":
            start_time = query_now - timedelta(hours=intervals - 1)
            base_local_time = ensure_shanghai(start_time)
        elif time_range == "14weeks":
            base_local_time = query_local_now - timedelta(weeks=intervals - 1)
            base_local_time = base_local_time - timedelta(days=base_local_time.weekday())
            base_local_time = base_local_time.replace(hour=0, minute=0, second=0, microsecond=0)
            start_time = base_local_time.astimezone(UTC)
        else:
            start_time = query_now - timedelta(days=intervals - 1)
            base_local_time = ensure_shanghai(start_time)

        query_start_time = start_time.replace(tzinfo=None)
        message_group = self._time_group_format(Message.created_at, time_range)

        if metric_type == "models":
            category = cast(Message.extra_metadata["response_metadata"]["model_name"], String)
            result = await self.db_session.execute(
                select(
                    message_group.label("date"),
                    func.count(Message.id).label("count"),
                    category.label("category"),
                )
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    Message.role == "assistant",
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                    Message.created_at >= query_start_time,
                    Message.extra_metadata.isnot(None),
                    Conversation.status.notin_(("deleted", "subagent")),
                    User.is_deleted == 0,
                )
                .group_by(message_group, category)
                .order_by(message_group)
            )
            rows = list(result.all())
        elif metric_type == "agents":
            conversation_group = self._time_group_format(Conversation.updated_at, time_range)
            result = await self.db_session.execute(
                select(
                    conversation_group.label("date"),
                    func.count(Conversation.id).label("count"),
                    Conversation.agent_id.label("category"),
                )
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    Conversation.updated_at.isnot(None),
                    Conversation.updated_at >= query_start_time,
                    Conversation.status.notin_(("deleted", "subagent")),
                    User.is_deleted == 0,
                )
                .group_by(conversation_group, Conversation.agent_id)
                .order_by(conversation_group)
            )
            rows = list(result.all())
        elif metric_type == "tokens":
            rows = []
            for token_name in ("input_tokens", "output_tokens"):
                result = await self.db_session.execute(
                    select(
                        message_group.label("date"),
                        func.sum(
                            func.coalesce(
                                cast(cast(Message.extra_metadata["usage_metadata"][token_name], String), Integer),
                                0,
                            )
                        ).label("count"),
                        literal(token_name).label("category"),
                    )
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .join(User, Conversation.uid == User.uid)
                    .join(Agent, Conversation.agent_id == Agent.slug)
                    .where(
                        Message.created_at >= query_start_time,
                        or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                        Message.extra_metadata.isnot(None),
                        Message.extra_metadata["usage_metadata"].isnot(None),
                        Conversation.status.notin_(("deleted", "subagent")),
                        User.is_deleted == 0,
                    )
                    .group_by(message_group)
                    .order_by(message_group)
                )
                rows.extend(result.all())
        else:
            tool_group = self._time_group_format(ToolCall.created_at, time_range)
            result = await self.db_session.execute(
                select(
                    tool_group.label("date"),
                    func.count(ToolCall.id).label("count"),
                    ToolCall.tool_name.label("category"),
                )
                .join(Message, ToolCall.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    ToolCall.created_at >= query_start_time,
                    Conversation.status.notin_(("deleted", "subagent")),
                    User.is_deleted == 0,
                )
                .group_by(tool_group, ToolCall.tool_name)
                .order_by(tool_group)
            )
            rows = list(result.all())

        categories = sorted({row.category for row in rows if row.category})
        if not categories:
            categories = {
                "models": ["unknown_model"],
                "agents": ["unknown_agent"],
                "tokens": ["input_tokens", "output_tokens"],
                "tools": ["unknown_tool"],
            }[metric_type]

        agent_names = None
        if metric_type == "agents":
            agent_slugs = [category for category in categories if category]
            if agent_slugs:
                agent_names = {
                    agent.slug: agent.name
                    for agent in await AgentRepository(self.db_session).list_by_slugs(agent_slugs)
                }

        time_data: dict[str, dict[str, int]] = {}
        for row in rows:
            date_key = row.date
            if time_range == "14weeks":
                base_date = datetime.strptime(f"{date_key}-1", "%Y-%W-%w")
                iso_year, iso_week, _ = base_date.isocalendar()
                date_key = f"{iso_year}-{iso_week:02d}"
            time_data.setdefault(date_key, {})[row.category] = row.count

        if time_range == "14hours":
            delta = timedelta(hours=1)
        elif time_range == "14weeks":
            delta = timedelta(weeks=1)
        else:
            delta = timedelta(days=1)

        data = []
        current_time = base_local_time
        for _ in range(intervals):
            if time_range == "14hours":
                date_key = current_time.strftime("%Y-%m-%d %H:00")
            elif time_range == "14weeks":
                iso_year, iso_week, _ = current_time.isocalendar()
                date_key = f"{iso_year}-{iso_week:02d}"
            else:
                date_key = current_time.strftime("%Y-%m-%d")

            interval_data = dict(time_data.get(date_key, {}))
            interval_total = sum(interval_data.values())
            for category in categories:
                interval_data.setdefault(category, 0)
            data.append({"date": date_key, "data": interval_data, "total": interval_total})
            current_time += delta

        if metric_type == "tools":
            total_result = await self.db_session.execute(
                select(func.count(ToolCall.id))
                .join(Message, ToolCall.message_id == Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(Conversation.status.notin_(("deleted", "subagent")), User.is_deleted == 0)
            )
            total_count = total_result.scalar() or 0
        else:
            total_count = sum(item["total"] for item in data)
        peak = max(data, key=lambda item: item["total"]) if data else {"total": 0, "date": ""}
        return {
            "data": data,
            "categories": categories,
            "total_count": total_count,
            "average_count": round(total_count / intervals, 2) if intervals else 0,
            "peak_count": peak["total"],
            "peak_date": peak["date"],
            "agent_names": agent_names,
        }

    async def get_thread_analytics(
        self,
        *,
        time_range: str = "30days",
        agent_id: str | None = None,
        include_subagents: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """统计会话（Thread）汇总、每日趋势、消息深度、Agent 与用户分布。"""
        raw_now = now or utc_now()
        query_now = raw_now.astimezone(UTC).replace(tzinfo=None) if raw_now.tzinfo else raw_now
        days = {"7days": 7, "14days": 14, "30days": 30, "90days": 90}.get(time_range, 30)
        local_start_day = (query_now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
        local_start_day -= timedelta(days=days - 1)
        query_start_time = local_start_day - timedelta(hours=8)

        status_filter = (
            Conversation.status != "deleted"
            if include_subagents
            else Conversation.status.notin_(("deleted", "subagent"))
        )
        conversation_filters = [
            Conversation.created_at.isnot(None),
            status_filter,
            User.is_deleted == 0,
        ]
        if agent_id:
            conversation_filters.append(Conversation.agent_id == agent_id)

        summary_result = await self.db_session.execute(
            select(
                func.count(Conversation.id).label("total_threads"),
                func.sum(case((Conversation.is_pinned.is_(True), 1), else_=0)).label("pinned_threads"),
            )
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
        )
        summary_row = summary_result.one()

        message_summary_result = await self.db_session.execute(
            select(
                func.count(Message.id).label("total_messages"),
                func.count(
                    distinct(case((Message.created_at >= query_start_time, Message.conversation_id), else_=None))
                ).label("active_threads"),
            )
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                *conversation_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        message_summary_row = message_summary_result.one()

        token_totals = self._conversation_token_totals()
        tokens_query = (
            select(func.coalesce(func.sum(token_totals.c.total_tokens), 0))
            .select_from(token_totals)
            .join(Conversation, token_totals.c.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
        )
        total_tokens = int((await self.db_session.execute(tokens_query)).scalar() or 0)

        total_threads = int(summary_row.total_threads or 0)
        active_threads = int(message_summary_row.active_threads or 0)
        pinned_threads = int(summary_row.pinned_threads or 0)
        total_messages = int(message_summary_row.total_messages or 0)

        new_thread_date = self._shanghai_date_group(Conversation.created_at)
        new_thread_rows = (
            await self.db_session.execute(
                select(new_thread_date.label("date"), func.count(Conversation.id).label("count"))
                .join(User, Conversation.uid == User.uid)
                .join(Agent, Conversation.agent_id == Agent.slug)
                .where(
                    *conversation_filters,
                    Conversation.created_at >= query_start_time,
                    Conversation.created_at <= query_now,
                )
                .group_by(new_thread_date)
            )
        ).all()
        new_threads_by_date = {str(row.date): int(row.count or 0) for row in new_thread_rows}

        message_date = self._shanghai_date_group(Message.created_at)
        activity_query = (
            select(
                message_date.label("date"),
                func.count(distinct(Message.conversation_id)).label("active_threads"),
                func.count(Message.id).label("message_count"),
            )
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(
                Message.created_at >= query_start_time,
                Message.created_at <= query_now,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                *conversation_filters,
            )
            .group_by(message_date)
        )
        activity_rows = (await self.db_session.execute(activity_query)).all()
        activity_by_date = {
            str(row.date): {
                "active_threads": int(row.active_threads or 0),
                "message_count": int(row.message_count or 0),
            }
            for row in activity_rows
        }

        daily_trends = []
        for day_offset in range(days):
            date_key = (local_start_day + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            activity = activity_by_date.get(date_key, {})
            daily_trends.append(
                {
                    "date": date_key,
                    "new_threads": new_threads_by_date.get(date_key, 0),
                    "active_threads": activity.get("active_threads", 0),
                    "message_count": activity.get("message_count", 0),
                }
            )

        # 3. 消息深度分布 (0条, 1-2条, 3-5条, 6-10条, 11-20条, 20+条)
        depth_query = (
            select(
                func.sum(case((func.coalesce(ConversationStats.message_count, 0) == 0, 1), else_=0)).label("d0"),
                func.sum(
                    case(
                        (
                            (func.coalesce(ConversationStats.message_count, 0) >= 1)
                            & (func.coalesce(ConversationStats.message_count, 0) <= 2),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d1_2"),
                func.sum(
                    case(
                        (
                            (func.coalesce(ConversationStats.message_count, 0) >= 3)
                            & (func.coalesce(ConversationStats.message_count, 0) <= 5),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d3_5"),
                func.sum(
                    case(
                        (
                            (func.coalesce(ConversationStats.message_count, 0) >= 6)
                            & (func.coalesce(ConversationStats.message_count, 0) <= 10),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d6_10"),
                func.sum(
                    case(
                        (
                            (func.coalesce(ConversationStats.message_count, 0) >= 11)
                            & (func.coalesce(ConversationStats.message_count, 0) <= 20),
                            1,
                        ),
                        else_=0,
                    )
                ).label("d11_20"),
                func.sum(case((func.coalesce(ConversationStats.message_count, 0) > 20, 1), else_=0)).label("d20_plus"),
            )
            .select_from(Conversation)
            .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
        )
        depth_row = (await self.db_session.execute(depth_query)).one()
        depth_distribution = {
            "0 条": int(depth_row.d0 or 0),
            "1-2 条": int(depth_row.d1_2 or 0),
            "3-5 条": int(depth_row.d3_5 or 0),
            "6-10 条": int(depth_row.d6_10 or 0),
            "11-20 条": int(depth_row.d11_20 or 0),
            "20+ 条": int(depth_row.d20_plus or 0),
        }

        # 4. 各智能体会话分布
        agent_group_query = (
            select(
                Conversation.agent_id,
                func.count(Conversation.id).label("thread_count"),
                func.coalesce(func.sum(ConversationStats.message_count), 0).label("message_count"),
                func.coalesce(func.sum(token_totals.c.total_tokens), 0).label("token_count"),
            )
            .select_from(Conversation)
            .join(token_totals, Conversation.id == token_totals.c.conversation_id)
            .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
            .group_by(Conversation.agent_id)
            .order_by(func.count(Conversation.id).desc())
        )
        agent_rows = (await self.db_session.execute(agent_group_query)).all()
        agent_slugs = [row.agent_id for row in agent_rows if row.agent_id]
        agent_names_map = {}
        agent_avatars_map = {}
        if agent_slugs:
            agents = await AgentRepository(self.db_session).list_by_slugs(agent_slugs)
            agent_names_map = {a.slug: a.name for a in agents}
            agent_avatars_map = {a.slug: normalize_public_minio_url(a.icon) if a.icon else None for a in agents}

        agent_distribution = [
            {
                "agent_id": row.agent_id,
                "agent_name": agent_names_map.get(row.agent_id, row.agent_id),
                "agent_avatar": agent_avatars_map.get(row.agent_id),
                "thread_count": int(row.thread_count or 0),
                "message_count": int(row.message_count or 0),
                "token_count": int(row.token_count or 0),
                "avg_messages": round(int(row.message_count or 0) / int(row.thread_count or 1), 1),
            }
            for row in agent_rows
        ]

        # 5. 高频用户活跃排行
        user_group_query = (
            select(
                Conversation.uid,
                User.username,
                User.avatar,
                func.count(Conversation.id).label("thread_count"),
                func.coalesce(func.sum(ConversationStats.message_count), 0).label("message_count"),
                func.max(Conversation.updated_at).label("last_active_at"),
            )
            .select_from(Conversation)
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .outerjoin(ConversationStats, Conversation.id == ConversationStats.conversation_id)
            .where(*conversation_filters)
            .group_by(Conversation.uid, User.username, User.avatar)
            .order_by(func.count(Conversation.id).desc())
            .limit(10)
        )
        user_rows = (await self.db_session.execute(user_group_query)).all()
        top_users = [
            {
                "uid": row.uid,
                "username": row.username or row.uid,
                "avatar": normalize_public_minio_url(row.avatar) if row.avatar else None,
                "thread_count": int(row.thread_count or 0),
                "message_count": int(row.message_count or 0),
                "last_active_at": format_utc_datetime(row.last_active_at),
            }
            for row in user_rows
        ]

        # 6. 状态分布
        status_query = (
            select(Conversation.status, func.count(Conversation.id))
            .join(User, Conversation.uid == User.uid)
            .join(Agent, Conversation.agent_id == Agent.slug)
            .where(*conversation_filters)
            .group_by(Conversation.status)
        )
        status_rows = (await self.db_session.execute(status_query)).all()
        status_distribution = {row[0] or "unknown": int(row[1] or 0) for row in status_rows}

        return {
            "summary": {
                "total_threads": total_threads,
                "active_threads": active_threads,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "avg_messages_per_thread": round(total_messages / total_threads, 1) if total_threads else 0.0,
                "avg_tokens_per_thread": round(total_tokens / total_threads, 0) if total_threads else 0.0,
                "pinned_threads": pinned_threads,
            },
            "daily_trends": daily_trends,
            "depth_distribution": depth_distribution,
            "agent_distribution": agent_distribution,
            "top_users": top_users,
            "status_distribution": status_distribution,
        }
