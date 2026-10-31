"""
对话域持久化 Repository（Async）
"""

import uuid as uuid_lib

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.agents.message_preview import build_message_search_snippet
from yuxi.modules.agents.models.messages import (
    Message,
)
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES, AgentRun
from yuxi.modules.agents.models.sessions import UNVIEWED_RUN_MARKER, Session
from yuxi.modules.agents.repositories.messages import message_search_conditions
from yuxi.modules.agents.repositories.pagination import order_page

MAX_SESSION_TITLE_LENGTH = 255
MESSAGE_SEARCH_SNIPPETS_PER_THREAD = 2
ALL_APP_SCOPES = object()


class SessionRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    def _normalize_title(self, title: str | None) -> str | None:
        if title is None:
            return None
        normalized = str(title).strip()
        if not normalized:
            return ""
        if len(normalized) > MAX_SESSION_TITLE_LENGTH:
            logger.warning(f"Session title too long ({len(normalized)}), truncate to {MAX_SESSION_TITLE_LENGTH}")
            return normalized[:MAX_SESSION_TITLE_LENGTH]
        return normalized

    async def add_session(
        self,
        *,
        uid: str,
        agent_id: str,
        title: str | None = None,
        thread_id: str | None = None,
        metadata: dict | None = None,
        config_snapshot: dict | None = None,
        project_id: str,
        creation_request_id: str | None = None,
        app_id: str | None = None,
    ) -> Session:
        """创建对话和统计记录但只 flush，供外层事务继续绑定关系。"""
        if not thread_id:
            thread_id = str(uuid_lib.uuid4())

        metadata = (metadata or {}).copy()
        metadata.pop("attachments", None)

        normalized_title = self._normalize_title(title)
        metadata.pop("auto_title", None)
        if not normalized_title:
            metadata["auto_title"] = "pending"

        agent_session = Session(
            thread_id=thread_id,
            tree_root_thread_id=thread_id,
            creation_request_id=creation_request_id,
            uid=str(uid),
            app_id=app_id,
            agent_id=agent_id,
            title=normalized_title or "New Session",
            status="active",
            extra_metadata=metadata,
            config_snapshot=config_snapshot,
            last_viewed_run_id=UNVIEWED_RUN_MARKER,
            project_id=project_id,
        )

        self.db.add(agent_session)
        await self.db.flush()

        logger.info(f"Created agent_session: {agent_session.thread_id} for user {uid}")
        return agent_session

    async def public_facts(self, sessions, *, uid, app_id) -> list[tuple]:
        """批量读取资源事实，列表、搜索和详情共享同一查询集合。"""
        from yuxi.modules.agents.repositories.runs import AgentRunRepository
        from yuxi.modules.agents.repositories.turn import AgentTurnRepository

        ids = [session.thread_id for session in sessions]
        if not ids:
            return []
        views = await AgentTurnRepository(self.db).list_current_views(thread_ids=ids, uid=uid, app_id=app_id)
        latest = await AgentRunRepository(self.db).get_latest_top_level_runs_for_threads(uid, ids)
        timestamps = await self.get_last_activity_for_threads(uid=uid, app_id=app_id, thread_ids=ids)
        result = []
        for session in sessions:
            turn, run, count = views[session.thread_id]
            run_id, run_status = latest.get(session.thread_id, (None, None))
            unread = bool(run_id and run_status in AGENT_RUN_TERMINAL_STATUSES and run_id != session.last_viewed_run_id)
            result.append((session, turn, run, count, timestamps.get(session.thread_id, session.created_at), unread))
        return result

    async def get_session_by_thread_id(self, thread_id: str) -> Session | None:
        result = await self.db.execute(select(Session).where(Session.thread_id == thread_id))
        return result.scalar_one_or_none()

    async def lock_session_by_thread_id(self, thread_id: str) -> Session | None:
        """锁定线程根记录，串行化同一对话的调度决策。"""
        # 身份键不变；允许树通知的外键键共享锁，避免 Session → 树锁反向等待。
        result = await self.db.execute(
            select(Session).where(Session.thread_id == thread_id).with_for_update(key_share=True).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_tree_sessions(self, *, tree_root_thread_id: str, uid: str, app_id: str | None) -> list[Session]:
        """按固定次序锁定同一授权树，归档不能越过成员正在接收的输入。"""
        return list(
            (
                await self.db.scalars(
                    select(Session)
                    .where(Session.tree_root_thread_id == tree_root_thread_id, Session.uid == uid, Session.app_id == app_id)
                    .order_by(Session.thread_id)
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def has_unfinished_work(self, *, thread_ids: list[str], uid: str, app_id: str | None) -> bool:
        """统一判断授权会话范围内的输入、轮次、运行与清理责任。"""
        from yuxi.modules.agents.models.inputs import AgentInput
        from yuxi.modules.agents.models.turns import AgentTurn

        member_threads = select(Session.thread_id).where(Session.thread_id.in_(thread_ids), Session.uid == uid, Session.app_id == app_id)
        pending_input = select(AgentInput.id).where(AgentInput.thread_id.in_(member_threads), AgentInput.status == "pending").exists()
        active_turn = (
            select(AgentTurn.id)
            .where(AgentTurn.thread_id.in_(member_threads), AgentTurn.status.in_(("running", "waiting", "cancelling")))
            .exists()
        )
        active_run = (
            select(AgentRun.id)
            .where(
                AgentRun.thread_id.in_(member_threads),
                AgentRun.uid == uid,
                AgentRun.app_id == app_id,
                or_(AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES), AgentRun.runtime_cleanup_pending.is_(True)),
            )
            .exists()
        )
        return bool(await self.db.scalar(select(or_(pending_input, active_turn, active_run))))

    async def get_session_by_id(self, session_record_id: int) -> Session | None:
        result = await self.db.execute(select(Session).where(Session.id == session_record_id))
        return result.scalar_one_or_none()

    async def mark_thread_viewed(self, thread_id: str, run_id: str) -> bool:
        """更新查看目标并报告是否有持久变化，由用例提交。"""
        agent_session = await self.get_session_by_thread_id(thread_id)
        if agent_session is None or agent_session.last_viewed_run_id == run_id:
            return False
        agent_session.last_viewed_run_id = run_id
        await self.db.flush()
        return True

    async def list_public_sessions(
        self,
        *,
        uid: str,
        app_id: str | None,
        agent_id: str | None,
        after: str | None,
        limit: int,
        order: str,
        archived: bool = False,
        is_pinned: bool | None = None,
        exclude_personal: bool = False,
    ) -> tuple[list[Session], bool]:
        """按完整作用域和稳定创建顺序读取有界会话页。"""
        conditions = [
            Session.uid == uid,
            Session.app_id == app_id,
            Session.status == ("archived" if archived else "active"),
        ]
        if exclude_personal:
            conditions.append(exclude_personal_sessions())
        if agent_id:
            conditions.append(Session.agent_id == agent_id)
        if is_pinned is not None:
            conditions.append(Session.is_pinned == is_pinned)
        query = select(Session).where(*conditions)
        boundary = None
        if after:
            anchor = (
                await self.db.execute(select(Session).where(Session.uid == uid, Session.app_id == app_id, Session.thread_id == after))
            ).scalar_one_or_none()
            if anchor is None:
                raise ValueError("after 不属于当前会话作用域")
            boundary = (anchor.created_at, anchor.thread_id)
        query = order_page(query, columns=(Session.created_at, Session.thread_id), anchor=boundary, order=order)
        rows = list((await self.db.execute(query.limit(limit + 1))).scalars())
        return rows[:limit], len(rows) > limit

    async def get_last_activity_for_threads(self, *, uid: str, app_id: str | None, thread_ids: list[str]) -> dict:
        """从已接收输入与执行时间计算活动时间，不受标题或已读更新影响。"""
        from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt

        result = {}
        for model, times in (
            (AgentInputReceipt, (AgentInputReceipt.created_at,)),
            (AgentInput, (AgentInput.created_at, AgentInput.consumed_at, AgentInput.cancelled_at)),
            (AgentRun, (AgentRun.created_at, AgentRun.started_at, AgentRun.finished_at)),
        ):
            query = (
                select(model.thread_id, *(func.max(column) for column in times))
                .where(model.uid == uid, model.app_id == app_id, model.thread_id.in_(thread_ids))
                .group_by(model.thread_id)
            )
            for thread_id, *values in (await self.db.execute(query)).all():
                timestamp = max((value for value in values if value is not None), default=None)
                if timestamp is not None and (thread_id not in result or timestamp > result[thread_id]):
                    result[thread_id] = timestamp
        return result

    async def search_sessions(
        self,
        *,
        uid: str,
        query: str,
        agent_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        app_id: str | None | object = ALL_APP_SCOPES,
        title_only: bool = False,
        exclude_personal: bool = False,
    ) -> tuple[list[dict], bool]:
        """搜索当前作用域的标题，可选联合可见消息并返回实际命中片段。"""
        normalized_query = str(query or "").strip()
        if not normalized_query and not title_only:
            return [], False

        session_conditions = [
            Session.uid == str(uid),
            Session.status == "active",
        ]
        if exclude_personal:
            session_conditions.append(exclude_personal_sessions())
        if agent_id:
            session_conditions.append(Session.agent_id == agent_id)
        if app_id is not ALL_APP_SCOPES:
            session_conditions.append(Session.app_id == app_id)

        escaped_title_query = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        title_pattern = f"%{escaped_title_query}%"
        if title_only:
            if normalized_query:
                session_conditions.append(Session.title.ilike(title_pattern, escape="\\"))
            result = await self.db.scalars(
                select(Session)
                .where(*session_conditions)
                .order_by(Session.updated_at.desc(), Session.id.desc())
                .limit(limit + 1)
                .offset(offset)
            )
            sessions = list(result)
            return [{"agent_session": session} for session in sessions[:limit]], len(sessions) > limit

        message_conditions = message_search_conditions(normalized_query)
        summary = (
            select(
                Message.session_record_id.label("session_record_id"),
                func.count(Message.id).label("matched_count"),
                func.max(Message.created_at).label("latest_match_at"),
            )
            .join(Session, Session.id == Message.session_record_id)
            .where(*session_conditions, *message_conditions)
            .group_by(Message.session_record_id)
            .subquery()
        )

        result = await self.db.execute(
            select(Session, summary.c.matched_count, summary.c.latest_match_at)
            .outerjoin(summary, Session.id == summary.c.session_record_id)
            .where(
                *session_conditions,
                or_(
                    Session.title.ilike(title_pattern, escape="\\"),
                    summary.c.session_record_id.is_not(None),
                ),
            )
            .order_by(func.coalesce(summary.c.latest_match_at, Session.updated_at).desc(), Session.updated_at.desc(), Session.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )
        rows = list(result.all())
        has_more = len(rows) > limit
        rows = rows[:limit]

        if not rows:
            return [], has_more
        matches = (
            select(
                Message.session_record_id,
                Message.id,
                Message.content,
                Message.created_at,
                func.row_number()
                .over(
                    partition_by=Message.session_record_id,
                    order_by=(Message.created_at.desc(), Message.id.desc()),
                )
                .label("rank"),
            )
            .where(Message.session_record_id.in_([session.id for session, *_ in rows]), *message_conditions)
            .subquery()
        )
        snippets_by_session = {session.id: [] for session, *_ in rows}
        snippet_result = await self.db.execute(
            select(matches)
            .where(matches.c.rank <= MESSAGE_SEARCH_SNIPPETS_PER_THREAD)
            .order_by(matches.c.session_record_id, matches.c.rank)
        )
        for session_id, message_id, content, created_at, _rank in snippet_result:
            snippets_by_session[session_id].append(
                {
                    "message_id": message_id,
                    "content": build_message_search_snippet(content, normalized_query),
                    "created_at": created_at,
                }
            )
        items: list[dict] = []
        for agent_session, matched_count, latest_match_at in rows:
            snippets = snippets_by_session[agent_session.id]

            items.append(
                {
                    "agent_session": agent_session,
                    "matched_count": int(matched_count or 0),
                    "latest_match_at": latest_match_at,
                    "message_id": snippets[0]["message_id"] if snippets else None,
                    "snippets": snippets,
                }
            )

        return items, has_more


def exclude_personal_sessions():
    """在查询分页前排除个人助理会话。"""
    from yuxi.modules.agents.models.definitions import Agent
    from yuxi.modules.agents.models.personal_assistants import PersonalAssistant

    return (
        ~select(PersonalAssistant.uid)
        .join(Agent, Agent.id == PersonalAssistant.agent_id)
        .where(or_(Agent.slug == Session.agent_id, PersonalAssistant.main_thread_id == Session.tree_root_thread_id))
        .exists()
    )
