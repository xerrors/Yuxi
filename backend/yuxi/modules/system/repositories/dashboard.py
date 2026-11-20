"""Dashboard 统计读模型的数据访问层。"""

from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, case, cast, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.minio.client import normalize_public_minio_url
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.messages import AUDIT_MESSAGE_TYPES, TOOL_AUDIT_MESSAGE_TYPE, Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.identity.models import User
from yuxi.shared.datetime import UTC, ensure_shanghai, format_utc_datetime, shanghai_now, utc_now

UNREGISTERED_AGENT_PREFIX = "未登记智能体"


def unregistered_agent_name(slug: str | None) -> str:
    """会话指向的 Agent 行不存在时的展示名。

    智能体被删除后其历史会话仍留在 `sessions.agent_id`（无外键、无重指派）：这类
    悬空 slug 的会话必须照常计入统计，系列名也不能显示成裸 slug 让人以为是某个
    真实智能体。
    """
    return f"{UNREGISTERED_AGENT_PREFIX}（{slug or 'unknown'}）"


class DashboardRepository:
    """集中封装 Dashboard 的跨表统计查询与读模型聚合。"""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    @staticmethod
    def _time_group_format(column: Any, time_range: str) -> Any:
        """生成使用上海时区显示的 PostgreSQL 时间分组表达式。"""
        if time_range == "14hours":
            return func.to_char(func.timezone("Asia/Shanghai", column), "YYYY-MM-DD HH24:00")
        if time_range == "14weeks":
            return func.to_char(func.timezone("Asia/Shanghai", column), "YYYY-IW")
        return func.to_char(func.timezone("Asia/Shanghai", column), "YYYY-MM-DD")

    def _shanghai_date_group(self, column: Any) -> Any:
        """按上海日历日生成 PostgreSQL/SQLite 兼容分组表达式。"""
        bind = self.db_session.bind
        if bind is not None and bind.dialect.name == "sqlite":
            return func.date(column, "+8 hours")
        return func.date(func.timezone("Asia/Shanghai", column))

    @staticmethod
    def _counted_message_condition():
        """参与会话消息计数的事实消息：排除审计消息。"""
        return and_(
            Message.message_type.isnot(None),
            Message.message_type.notin_(AUDIT_MESSAGE_TYPES),
        )

    @staticmethod
    def _message_count_totals():
        """按会话统计消息数的事实子查询（与消息写入时的计数语义一致）。"""
        return (
            select(
                Message.session_record_id.label("session_record_id"),
                func.count(Message.id).label("message_count"),
            )
            .where(DashboardRepository._counted_message_condition())
            .group_by(Message.session_record_id)
            .subquery()
        )

    async def get_session_token_usage(self, session_record_id: int) -> dict[str, Any]:
        """读取与会话列表一致的实测 Token 汇总；无 Run 事实时暴露未知。"""
        totals = self._session_token_totals([session_record_id])
        row = (
            await self.db_session.execute(
                select(totals.c.total_tokens, totals.c.complete).where(totals.c.session_record_id == session_record_id)
            )
        ).one_or_none()
        if row is None:
            return {"total_tokens": None, "token_usage_complete": False}
        return {"total_tokens": row.total_tokens, "token_usage_complete": bool(row.complete)}

    async def list_sessions(
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
            filters.append(Session.uid == uid)
        if agent_id:
            filters.append(Session.agent_id == agent_id)
        if status and status != "all":
            filters.append(Session.status == status)
        else:
            filters.append(Session.status != "deleted")
        if search:
            search_term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Session.title.ilike(search_term),
                    Session.thread_id.ilike(search_term),
                    Session.uid.ilike(search_term),
                    User.username.ilike(search_term),
                )
            )

        total_result = await self.db_session.execute(
            select(func.count(Session.id)).select_from(Session).outerjoin(User, Session.uid == User.uid).where(*filters)
        )
        rows = (
            await self.db_session.execute(
                select(Session, User)
                .outerjoin(User, Session.uid == User.uid)
                .where(*filters)
                .order_by(Session.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        token_totals = self._session_token_totals([agent_session.id for agent_session, _ in rows])
        usage_by_session = {row.session_record_id: row for row in (await self.db_session.execute(select(token_totals))).all()}
        message_counts = self._message_count_totals()
        message_count_by_session = {
            row.session_record_id: row.message_count
            for row in (
                await self.db_session.execute(
                    select(message_counts).where(message_counts.c.session_record_id.in_([agent_session.id for agent_session, _ in rows]))
                )
            ).all()
        }
        agent_slugs = {agent_session.agent_id for agent_session, _ in rows if agent_session.agent_id}
        agents_by_slug: dict[str, Agent] = {}
        if agent_slugs:
            agents = (await self.db_session.scalars(select(Agent).where(Agent.slug.in_(agent_slugs)))).all()
            agents_by_slug = {agent.slug: agent for agent in agents}

        items = []
        for agent_session, user in rows:
            usage = usage_by_session.get(agent_session.id)
            agent = agents_by_slug.get(agent_session.agent_id)
            items.append(
                {
                    "thread_id": agent_session.thread_id,
                    "uid": agent_session.uid,
                    "username": user.username if user else agent_session.uid,
                    "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
                    "user_deleted": user is None or bool(user.is_deleted),
                    "agent_id": agent_session.agent_id,
                    "agent_name": agent.name if agent else unregistered_agent_name(agent_session.agent_id),
                    "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
                    "agent_deleted": agent is None,
                    "title": agent_session.title,
                    "status": agent_session.status,
                    "is_pinned": bool(agent_session.is_pinned),
                    "message_count": int(message_count_by_session.get(agent_session.id, 0)),
                    "total_tokens": usage.total_tokens if usage is not None else None,
                    "token_usage_complete": bool(usage.complete) if usage is not None else False,
                    "created_at": format_utc_datetime(agent_session.created_at) or "",
                    "updated_at": format_utc_datetime(agent_session.updated_at) or "",
                }
            )
        return {
            "items": items,
            "total": int(total_result.scalar() or 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_session_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        """读取完整会话审计可用的用户与 Agent 筛选项。"""
        user_rows = (
            await self.db_session.execute(
                select(Session.uid, User.username, User.avatar, User.is_deleted)
                .select_from(Session)
                .outerjoin(User, Session.uid == User.uid)
                .distinct()
            )
        ).all()
        agent_rows = (
            await self.db_session.execute(
                select(Session.agent_id, Agent.name, Agent.icon)
                .select_from(Session)
                .outerjoin(Agent, Session.agent_id == Agent.slug)
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
                "agent_name": row.name or unregistered_agent_name(row.agent_id),
                "avatar": normalize_public_minio_url(row.icon) if row.icon else None,
                "is_deleted": row.name is None,
            }
            for row in agent_rows
        ]
        users.sort(key=lambda item: (item["is_deleted"], item["username"].lower()))
        agents.sort(key=lambda item: (item["is_deleted"], item["agent_name"].lower()))
        return {"users": users, "agents": agents}

    async def get_session_audit_metadata(self, agent_session: Session) -> dict[str, Any]:
        """读取会话关联用户与 Agent 的当前审计状态。"""
        row = (
            await self.db_session.execute(
                select(User, Agent)
                .select_from(Session)
                .outerjoin(User, Session.uid == User.uid)
                .outerjoin(Agent, Session.agent_id == Agent.slug)
                .where(Session.id == agent_session.id)
            )
        ).one()
        user, agent = row
        return {
            "username": user.username if user else agent_session.uid,
            "user_avatar": normalize_public_minio_url(user.avatar) if user and user.avatar else None,
            "user_deleted": user is None or bool(user.is_deleted),
            "agent_name": agent.name if agent else unregistered_agent_name(agent_session.agent_id),
            "agent_avatar": normalize_public_minio_url(agent.icon) if agent and agent.icon else None,
            "agent_deleted": agent is None,
        }

    async def get_user_activity_stats(self, *, now: datetime | None = None) -> dict[str, Any]:
        """统计用户总量与近期开启对话的活跃用户。

        活跃只取决于会话与用户：不 join `agents`。悬空 slug（智能体删除后遗留）的会话
        同样证明该用户活跃，inner join 会把这些用户整天漏掉（24h/30d/日活三处同口径）。
        """
        query_now = now or utc_now()

        total_result = await self.db_session.execute(select(func.count(User.id)).where(User.is_deleted == 0))
        active_24h_result = await self.db_session.execute(
            select(func.count(distinct(User.id)))
            .select_from(Session)
            .join(User, Session.uid == User.uid)
            .where(
                Session.updated_at >= query_now - timedelta(days=1),
                Session.status != "deleted",
                User.is_deleted == 0,
            )
        )
        active_30d_result = await self.db_session.execute(
            select(func.count(distinct(User.id)))
            .select_from(Session)
            .join(User, Session.uid == User.uid)
            .where(
                Session.updated_at >= query_now - timedelta(days=30),
                Session.status != "deleted",
                User.is_deleted == 0,
            )
        )

        active_date = self._shanghai_date_group(Session.updated_at)
        daily_active_result = await self.db_session.execute(
            select(active_date.label("date"), func.count(distinct(User.id)).label("active_users"))
            .select_from(Session)
            .join(User, Session.uid == User.uid)
            .where(
                Session.updated_at >= query_now - timedelta(days=120),
                Session.updated_at < query_now,
                Session.status != "deleted",
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
        """一次聚合实际执行事实，派生总量、失败分布与原滚动日窗口。"""
        query_now = now or utc_now()
        tool_name = Message.extra_metadata["tool_name"].as_string()
        day_bucket = case(
            *[
                (
                    and_(
                        Message.created_at >= query_now - timedelta(days=index + 1), Message.created_at < query_now - timedelta(days=index)
                    ),
                    index,
                )
                for index in range(7)
            ],
            else_=None,
        )
        rows = (
            await self.db_session.execute(
                select(tool_name, Message.execution_status, day_bucket, func.count(Message.id))
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(
                    Session.status != "deleted",
                    User.is_deleted == 0,
                    Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                    Message.started_at.is_not(None),
                )
                .group_by(tool_name, Message.execution_status, day_bucket)
            )
        ).all()
        total_calls = successful_calls = failed_calls = 0
        tool_counts, error_counts = {}, {}
        daily_counts = [0] * 7
        for name, status, bucket, count in rows:
            total_calls += count
            tool_counts[name] = tool_counts.get(name, 0) + count
            if status == "completed":
                successful_calls += count
            elif status == "failed":
                failed_calls += count
                error_counts[name] = error_counts.get(name, 0) + count
            if bucket is not None:
                daily_counts[bucket] += count
        most_used = sorted(tool_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        return {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "success_rate": round(successful_calls / total_calls * 100, 2) if total_calls else 0,
            "most_used_tools": [{"tool_name": name, "count": count} for name, count in most_used],
            "tool_error_distribution": error_counts,
            "daily_tool_calls": [
                {"date": (query_now - timedelta(days=index + 1)).strftime("%Y-%m-%d"), "call_count": daily_counts[index]}
                for index in reversed(range(7))
            ],
        }

    async def get_agent_analytics(self) -> dict[str, Any]:
        """按智能体汇总有效用户与非删除会话的使用情况，含 agents 表未登记的 slug。"""
        agents = list((await self.db_session.execute(select(Agent).order_by(Agent.name.asc()))).scalars().all())
        valid_filters = [Session.status != "deleted", User.is_deleted == 0]

        session_rows = (
            await self.db_session.execute(
                select(Session.agent_id, func.count(Session.id))
                .join(User, Session.uid == User.uid)
                .where(*valid_filters)
                .group_by(Session.agent_id)
            )
        ).all()
        session_counts = {agent_id: int(count or 0) for agent_id, count in session_rows}

        tool_rows = (
            await self.db_session.execute(
                select(Session.agent_id, func.count(Message.id))
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(*valid_filters, Message.message_type == TOOL_AUDIT_MESSAGE_TYPE, Message.started_at.is_not(None))
                .group_by(Session.agent_id)
            )
        ).all()
        tool_counts = {agent_id: int(count or 0) for agent_id, count in tool_rows}

        session_stats = []
        tool_usage = []
        agent_names = {agent.slug: agent.name for agent in agents}
        for agent in agents:
            session_stats.append({"agent_id": agent.slug, "session_count": session_counts.get(agent.slug, 0)})
            tool_usage.append({"agent_id": agent.slug, "tool_usage_count": tool_counts.get(agent.slug, 0)})

        # 会话指向但 agents 表没有的 slug（智能体已删除）：补成独立系列，不丢弃
        for slug in sorted({*session_counts, *tool_counts} - set(agent_names)):
            if not slug:
                continue
            agent_names[slug] = unregistered_agent_name(slug)
            session_stats.append({"agent_id": slug, "session_count": session_counts.get(slug, 0)})
            tool_usage.append({"agent_id": slug, "tool_usage_count": tool_counts.get(slug, 0)})

        return {
            "total_agents": len(agent_names),
            "agent_session_counts": session_stats,
            "agent_tool_usage": tool_usage,
            "agent_names": agent_names,
        }

    async def get_basic_stats(self) -> dict[str, Any]:
        """读取有效用户与非删除会话的 Dashboard 基础计数。"""
        valid_filters = [Session.status != "deleted", User.is_deleted == 0]
        total_sessions_result = await self.db_session.execute(
            select(func.count(Session.id)).join(User, Session.uid == User.uid).where(*valid_filters)
        )
        active_sessions_result = await self.db_session.execute(
            select(func.count(Session.id)).join(User, Session.uid == User.uid).where(*valid_filters, Session.status == "active")
        )
        total_messages_result = await self.db_session.execute(
            select(func.count(Message.id))
            .join(Session, Message.session_record_id == Session.id)
            .join(User, Session.uid == User.uid)
            .where(
                *valid_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        total_users_result = await self.db_session.execute(select(func.count(User.id)).where(User.is_deleted == 0))
        return {
            "total_sessions": total_sessions_result.scalar() or 0,
            "active_sessions": active_sessions_result.scalar() or 0,
            "total_messages": total_messages_result.scalar() or 0,
            "total_users": total_users_result.scalar() or 0,
        }

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
        if metric_type == "tokens":
            return await self._get_token_timeseries(time_range=time_range, now=query_now)
        if metric_type == "sessions":
            return await self._get_session_timeseries(time_range=time_range, now=query_now)
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

        query_start_time = start_time
        message_group = self._time_group_format(Message.created_at, time_range)

        if metric_type == "models":
            category = cast(Message.extra_metadata["response_metadata"]["model_name"], String)
            result = await self.db_session.execute(
                select(
                    message_group.label("date"),
                    func.count(Message.id).label("count"),
                    category.label("category"),
                )
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(
                    Message.role == "assistant",
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                    Message.created_at >= query_start_time,
                    Message.extra_metadata.isnot(None),
                    Session.status != "deleted",
                    User.is_deleted == 0,
                )
                .group_by(message_group, category)
                .order_by(message_group)
            )
            rows = list(result.all())
        elif metric_type == "agents":
            session_group = self._time_group_format(Session.updated_at, time_range)
            result = await self.db_session.execute(
                select(
                    session_group.label("date"),
                    func.count(Session.id).label("count"),
                    Session.agent_id.label("category"),
                )
                .join(User, Session.uid == User.uid)
                .where(
                    Session.updated_at.isnot(None),
                    Session.updated_at >= query_start_time,
                    Session.status != "deleted",
                    User.is_deleted == 0,
                )
                .group_by(session_group, Session.agent_id)
                .order_by(session_group)
            )
            rows = list(result.all())

        else:
            tool_group = self._time_group_format(Message.created_at, time_range)
            tool_name = Message.extra_metadata["tool_name"].as_string()
            result = await self.db_session.execute(
                select(
                    tool_group.label("date"),
                    func.count(Message.id).label("count"),
                    tool_name.label("category"),
                )
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(
                    Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                    Message.started_at.is_not(None),
                    Message.created_at >= query_start_time,
                    Session.status != "deleted",
                    User.is_deleted == 0,
                )
                .group_by(tool_group, tool_name)
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
                    for agent in (await self.db_session.scalars(select(Agent).where(Agent.slug.in_(agent_slugs)))).all()
                }
                # 悬空 slug 与分布/分析同用未登记展示名，避免前端按 slug 裸显回退。
                agent_names.update({slug: unregistered_agent_name(slug) for slug in agent_slugs if slug not in agent_names})

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
                select(func.count(Message.id))
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(
                    Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                    Message.started_at.is_not(None),
                    Session.status != "deleted",
                    User.is_deleted == 0,
                )
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

    async def get_agent_session_distribution(self, *, time_range: str = "all", now: datetime | None = None) -> dict[str, Any]:
        """统计所选范围内新建会话的智能体分布和截至当前的平均消息数。"""
        query_now = (now or utc_now()).astimezone(UTC)
        local_today = ensure_shanghai(query_now).replace(hour=0, minute=0, second=0, microsecond=0)
        filters = [Session.status != "deleted", User.is_deleted == 0, Session.created_at <= query_now]
        if time_range != "all":
            if time_range == "3months":
                year, month = divmod(local_today.year * 12 + local_today.month - 1 - 3, 12)
                start = local_today.replace(year=year, month=month + 1, day=min(local_today.day, monthrange(year, month + 1)[1]))
            else:
                start = local_today - timedelta(days={"7days": 6, "30days": 29}[time_range])
            filters.append(Session.created_at >= start.astimezone(UTC))
        selected_sessions = (
            select(Session.id, Session.agent_id, Agent.name.label("agent_name"))
            .join(User, Session.uid == User.uid)
            .outerjoin(Agent, Session.agent_id == Agent.slug)
            .where(*filters)
            .subquery()
        )
        message_counts = (
            select(Message.session_record_id, func.count(Message.id).label("message_count"))
            .where(
                Message.session_record_id.in_(select(selected_sessions.c.id)),
                Message.created_at <= query_now,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
            .group_by(Message.session_record_id)
            .subquery()
        )
        rows = (
            await self.db_session.execute(
                select(
                    selected_sessions.c.agent_id,
                    selected_sessions.c.agent_name,
                    func.count(selected_sessions.c.id).label("thread_count"),
                    func.coalesce(func.sum(message_counts.c.message_count), 0).label("message_count"),
                )
                .outerjoin(message_counts, selected_sessions.c.id == message_counts.c.session_record_id)
                .group_by(selected_sessions.c.agent_id, selected_sessions.c.agent_name)
                .order_by(func.count(selected_sessions.c.id).desc(), selected_sessions.c.agent_id)
            )
        ).all()
        total_threads = sum(row.thread_count for row in rows)
        total_messages = sum(int(row.message_count) for row in rows)
        return {
            "total_threads": total_threads,
            "agent_count": len(rows),
            "avg_messages_per_thread": round(total_messages / total_threads, 1) if total_threads else 0.0,
            "agent_distribution": [
                {
                    "agent_id": row.agent_id,
                    "agent_name": row.agent_name or unregistered_agent_name(row.agent_id),
                    "thread_count": row.thread_count,
                    "message_count": int(row.message_count),
                    "avg_messages": round(int(row.message_count) / row.thread_count, 1),
                }
                for row in rows
            ],
        }

    async def get_thread_analytics(
        self,
        *,
        time_range: str = "30days",
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """统计会话（Thread）汇总、每日趋势、消息深度、Agent 与用户分布。"""
        raw_now = now or utc_now()
        query_now = raw_now.astimezone(UTC)
        days = {"7days": 7, "14days": 14, "30days": 30, "90days": 90}.get(time_range, 30)
        local_start_day = (query_now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
        local_start_day -= timedelta(days=days - 1)
        query_start_time = local_start_day - timedelta(hours=8)

        status_filter = Session.status != "deleted"
        session_filters = [
            Session.created_at.isnot(None),
            status_filter,
            User.is_deleted == 0,
        ]
        if agent_id:
            session_filters.append(Session.agent_id == agent_id)

        summary_result = await self.db_session.execute(
            select(
                func.count(Session.id).label("total_threads"),
                func.sum(case((Session.is_pinned.is_(True), 1), else_=0)).label("pinned_threads"),
            )
            .join(User, Session.uid == User.uid)
            .where(*session_filters)
        )
        summary_row = summary_result.one()

        message_summary_result = await self.db_session.execute(
            select(
                func.count(Message.id).label("total_messages"),
                func.count(distinct(case((Message.created_at >= query_start_time, Message.session_record_id), else_=None))).label(
                    "active_threads"
                ),
            )
            .select_from(Message)
            .join(Session, Message.session_record_id == Session.id)
            .join(User, Session.uid == User.uid)
            .where(
                *session_filters,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
            )
        )
        message_summary_row = message_summary_result.one()

        token_totals = self._session_token_totals()
        tokens_query = (
            select(
                func.sum(token_totals.c.total_tokens).label("total_tokens"),
                func.min(case((token_totals.c.complete.is_(True), 1), else_=0)).label("complete"),
            )
            .select_from(Session)
            .outerjoin(token_totals, token_totals.c.session_record_id == Session.id)
            .join(User, Session.uid == User.uid)
            .where(*session_filters)
        )
        token_summary = (await self.db_session.execute(tokens_query)).one()
        total_tokens = token_summary.total_tokens

        total_threads = int(summary_row.total_threads or 0)
        if not total_threads:
            total_tokens = 0
        active_threads = int(message_summary_row.active_threads or 0)
        pinned_threads = int(summary_row.pinned_threads or 0)
        total_messages = int(message_summary_row.total_messages or 0)

        new_thread_date = self._shanghai_date_group(Session.created_at)
        new_thread_rows = (
            await self.db_session.execute(
                select(new_thread_date.label("date"), func.count(Session.id).label("count"))
                .join(User, Session.uid == User.uid)
                .where(
                    *session_filters,
                    Session.created_at >= query_start_time,
                    Session.created_at <= query_now,
                )
                .group_by(new_thread_date)
            )
        ).all()
        new_threads_by_date = {str(row.date): int(row.count or 0) for row in new_thread_rows}

        message_date = self._shanghai_date_group(Message.created_at)
        activity_query = (
            select(
                message_date.label("date"),
                func.count(distinct(Message.session_record_id)).label("active_threads"),
                func.count(Message.id).label("message_count"),
            )
            .select_from(Message)
            .join(Session, Message.session_record_id == Session.id)
            .join(User, Session.uid == User.uid)
            .where(
                Message.created_at >= query_start_time,
                Message.created_at <= query_now,
                or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                *session_filters,
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
        message_counts = self._message_count_totals()
        depth_count = func.coalesce(message_counts.c.message_count, 0)
        depth_query = (
            select(
                func.sum(case((depth_count == 0, 1), else_=0)).label("d0"),
                func.sum(case(((depth_count >= 1) & (depth_count <= 2), 1), else_=0)).label("d1_2"),
                func.sum(case(((depth_count >= 3) & (depth_count <= 5), 1), else_=0)).label("d3_5"),
                func.sum(case(((depth_count >= 6) & (depth_count <= 10), 1), else_=0)).label("d6_10"),
                func.sum(case(((depth_count >= 11) & (depth_count <= 20), 1), else_=0)).label("d11_20"),
                func.sum(case((depth_count > 20, 1), else_=0)).label("d20_plus"),
            )
            .select_from(Session)
            .outerjoin(message_counts, Session.id == message_counts.c.session_record_id)
            .join(User, Session.uid == User.uid)
            .where(*session_filters)
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
                Session.agent_id,
                func.count(Session.id).label("thread_count"),
                func.coalesce(func.sum(message_counts.c.message_count), 0).label("message_count"),
                func.sum(token_totals.c.total_tokens).label("token_count"),
                func.min(case((token_totals.c.complete.is_(True), 1), else_=0)).label("token_usage_complete"),
            )
            .select_from(Session)
            .outerjoin(token_totals, Session.id == token_totals.c.session_record_id)
            .outerjoin(message_counts, Session.id == message_counts.c.session_record_id)
            .join(User, Session.uid == User.uid)
            .where(*session_filters)
            .group_by(Session.agent_id)
            .order_by(func.count(Session.id).desc())
        )
        agent_rows = (await self.db_session.execute(agent_group_query)).all()
        agent_slugs = [row.agent_id for row in agent_rows if row.agent_id]
        agent_names_map = {}
        agent_avatars_map = {}
        if agent_slugs:
            agents = (await self.db_session.scalars(select(Agent).where(Agent.slug.in_(agent_slugs)))).all()
            agent_names_map = {a.slug: a.name for a in agents}
            agent_avatars_map = {a.slug: normalize_public_minio_url(a.icon) if a.icon else None for a in agents}

        agent_distribution = [
            {
                "agent_id": row.agent_id,
                "agent_name": agent_names_map.get(row.agent_id) or unregistered_agent_name(row.agent_id),
                "agent_avatar": agent_avatars_map.get(row.agent_id),
                "thread_count": int(row.thread_count or 0),
                "message_count": int(row.message_count or 0),
                "token_count": row.token_count,
                "token_usage_complete": bool(row.token_usage_complete),
                "avg_messages": round(int(row.message_count or 0) / int(row.thread_count or 1), 1),
            }
            for row in agent_rows
        ]

        # 5. 高频用户活跃排行
        user_group_query = (
            select(
                Session.uid,
                User.username,
                User.avatar,
                func.count(Session.id).label("thread_count"),
                func.coalesce(func.sum(message_counts.c.message_count), 0).label("message_count"),
                func.max(Session.updated_at).label("last_active_at"),
            )
            .select_from(Session)
            .join(User, Session.uid == User.uid)
            .outerjoin(message_counts, Session.id == message_counts.c.session_record_id)
            .where(*session_filters)
            .group_by(Session.uid, User.username, User.avatar)
            .order_by(func.count(Session.id).desc())
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
            select(Session.status, func.count(Session.id))
            .join(User, Session.uid == User.uid)
            .where(*session_filters)
            .group_by(Session.status)
        )
        status_rows = (await self.db_session.execute(status_query)).all()
        status_distribution = {row[0] or "unknown": int(row[1] or 0) for row in status_rows}

        return {
            "summary": {
                "total_threads": total_threads,
                "active_threads": active_threads,
                "total_messages": total_messages,
                "total_tokens": total_tokens,
                "token_usage_complete": bool(token_summary.complete) if total_threads else True,
                "avg_messages_per_thread": round(total_messages / total_threads, 1) if total_threads else 0.0,
                "avg_tokens_per_thread": (round(total_tokens / total_threads, 0) if total_threads else 0.0)
                if total_tokens is not None
                else None,
                "pinned_threads": pinned_threads,
            },
            "daily_trends": daily_trends,
            "depth_distribution": depth_distribution,
            "agent_distribution": agent_distribution,
            "top_users": top_users,
            "status_distribution": status_distribution,
        }

    async def _get_session_timeseries(self, *, time_range: str, now: datetime) -> dict[str, Any]:
        """按上海时间补齐十四个桶，新增按创建时间、活跃按桶内去重消息会话计数。"""
        local_now = ensure_shanghai(now)
        unit = {"14hours": "hour", "14days": "day", "14weeks": "week"}[time_range]
        current_bucket = local_now.replace(minute=0, second=0, microsecond=0)
        if unit != "hour":
            current_bucket = current_bucket.replace(hour=0)
        if unit == "week":
            current_bucket -= timedelta(days=current_bucket.weekday())
        step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[unit]
        first_bucket = current_bucket - 13 * step
        start = first_bucket.astimezone(UTC)
        visible = (Session.status != "deleted", User.is_deleted == 0)

        created_bucket = func.date_trunc(unit, func.timezone("Asia/Shanghai", Session.created_at))
        created_rows = (
            await self.db_session.execute(
                select(created_bucket, func.count(Session.id))
                .join(User, Session.uid == User.uid)
                .where(*visible, Session.created_at >= start, Session.created_at <= now)
                .group_by(created_bucket)
            )
        ).all()
        active_bucket = func.date_trunc(unit, func.timezone("Asia/Shanghai", Message.created_at))
        active_rows = (
            await self.db_session.execute(
                select(active_bucket, func.count(distinct(Message.session_record_id)))
                .select_from(Message)
                .join(Session, Message.session_record_id == Session.id)
                .join(User, Session.uid == User.uid)
                .where(
                    *visible,
                    Message.created_at >= start,
                    Message.created_at <= now,
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                )
                .group_by(active_bucket)
            )
        ).all()
        date_format = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "week": "%G-%V"}[unit]
        created = {bucket.strftime(date_format): count for bucket, count in created_rows}
        active = {bucket.strftime(date_format): count for bucket, count in active_rows}
        data = []
        for index in range(14):
            key = (first_bucket + index * step).strftime(date_format)
            new_count = created.get(key, 0)
            data.append({"date": key, "data": {"new_threads": new_count, "active_threads": active.get(key, 0)}, "total": new_count})
        # 活跃和新增可以重叠，摘要 total/average/peak 只统计新增，不能相加。
        total = sum(item["total"] for item in data)
        peak = max(data, key=lambda item: item["total"])
        return {
            "data": data,
            "categories": ["new_threads", "active_threads"],
            "total_count": total,
            "average_count": round(total / 14, 2),
            "peak_count": peak["total"],
            "peak_date": peak["date"],
        }

    async def _get_token_timeseries(self, *, time_range: str, now: datetime) -> dict[str, Any]:
        """按 Run 创建时间统计持久用量，空桶为零，缺报桶保留未知和完整性。"""
        unit = {"14hours": "hour", "14weeks": "week"}.get(time_range, "day")
        current = ensure_shanghai(now).replace(minute=0, second=0, microsecond=0)
        if unit != "hour":
            current = current.replace(hour=0)
        if unit == "week":
            current -= timedelta(days=current.weekday())
        step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[unit]
        first = current - 13 * step
        usage = self._run_token_usage()
        bucket = func.date_trunc(unit, func.timezone("Asia/Shanghai", usage.c.created_at))
        rows = (
            await self.db_session.execute(
                select(
                    bucket.label("bucket"),
                    func.sum(usage.c.input_tokens).label("input_tokens"),
                    func.sum(usage.c.output_tokens).label("output_tokens"),
                    func.sum(usage.c.total_tokens).label("total_tokens"),
                    func.count(usage.c.id).label("run_count"),
                    func.sum(case((usage.c.complete, 0), else_=1)).label("incomplete_run_count"),
                )
                .select_from(usage)
                .join(Session, Session.id == usage.c.session_record_id)
                .join(User, User.uid == Session.uid)
                .where(
                    Session.status != "deleted",
                    User.is_deleted == 0,
                    usage.c.created_at >= first.astimezone(UTC),
                    usage.c.created_at <= now,
                )
                .group_by(bucket)
            )
        ).all()
        by_bucket = {row.bucket: row for row in rows}
        date_format = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "week": "%G-%V"}[unit]
        data = []
        for index in range(14):
            moment = first + index * step
            row = by_bucket.get(moment.replace(tzinfo=None))
            data.append(
                {
                    "date": moment.strftime(date_format),
                    "data": {"input_tokens": row.input_tokens if row else 0, "output_tokens": row.output_tokens if row else 0},
                    "total": row.total_tokens if row else 0,
                    "token_usage_complete": row.incomplete_run_count == 0 if row else True,
                    "run_count": row.run_count if row else 0,
                    "incomplete_run_count": row.incomplete_run_count if row else 0,
                }
            )
        run_count = sum(item["run_count"] for item in data)
        incomplete = sum(item["incomplete_run_count"] for item in data)
        # 空桶的零不能让全部缺报的范围产生一个看似可靠的零汇总。
        known = [row.total_tokens for row in rows if row.total_tokens is not None]
        total = sum(known) if known else (None if run_count else 0)
        peak = max((item for item in data if item["run_count"] and item["total"] is not None), key=lambda item: item["total"], default=None)
        return {
            "data": data,
            "categories": ["input_tokens", "output_tokens"],
            "total_count": total,
            "average_count": round(total / 14, 2) if total is not None else None,
            "peak_count": peak["total"] if peak else total,
            "peak_date": peak["date"] if peak else "",
            "token_usage_complete": incomplete == 0,
            "run_count": run_count,
            "incomplete_run_count": incomplete,
        }

    @staticmethod
    def _session_token_totals(session_record_ids: list[int] | None = None):
        """按会话汇总同一组 Run 实测用量，保留缺报标记。"""
        usage = DashboardRepository._run_token_usage()
        return (
            select(
                usage.c.session_record_id,
                func.sum(usage.c.total_tokens).label("total_tokens"),
                (func.min(case((usage.c.complete, 1), else_=0)) == 1).label("complete"),
            )
            .where(usage.c.session_record_id.in_(session_record_ids) if session_record_ids is not None else True)
            .group_by(usage.c.session_record_id)
            .subquery()
        )

    @staticmethod
    def _run_token_usage():
        """统一识别 Run 的已上报用量，禁止用缺报时的初始零冒充观测值。"""
        complete = AgentRun.token_usage["complete"].as_boolean().is_(True)
        reported = (AgentRun.token_usage["usage_reported_call_count"].as_integer() > 0) | complete
        values = {name: AgentRun.token_usage["total"][name].as_integer() for name in ("input_tokens", "output_tokens", "total_tokens")}
        return select(
            AgentRun.id,
            AgentRun.session_record_id,
            AgentRun.created_at,
            *(case((reported, value), else_=None).label(name) for name, value in values.items()),
            (complete & and_(*(value.isnot(None) for value in values.values()))).label("complete"),
        ).subquery()
