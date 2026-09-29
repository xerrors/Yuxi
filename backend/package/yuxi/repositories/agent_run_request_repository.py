"""AgentRunRequest repository.

The model has an autoincrement Integer ``id`` (cluster PK \u2014 used only for
FIFO ordering stability) and a unique String ``request_id`` (the idempotency
key shared with the Message and AgentRun tables).  All public lookups key on
``request_id``.

State transitions use ``SELECT \u2026 FOR UPDATE`` to serialise dispatch and cancel
contention on the same row.
"""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRunRequest
from yuxi.utils.datetime_utils import utc_now_naive


class AgentRunRequestRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_by_request_id(self, request_id: str) -> AgentRunRequest | None:
        result = await self.db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
        return result.scalar_one_or_none()

    async def get_latest_for_public_thread(
        self, *, uid: str, app_id: str | None, thread_id: str
    ) -> AgentRunRequest | None:
        """读取当前资源命名空间最近接收的请求。"""
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(
                AgentRunRequest.uid == uid,
                AgentRunRequest.app_id == app_id,
                AgentRunRequest.conversation_thread_id == thread_id,
            )
            .order_by(AgentRunRequest.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_queued_for_thread(self, *, uid: str, agent_slug: str, thread_id: str) -> bool:
        """判断线程内是否仍有待派发请求。"""
        result = await self.db.execute(
            select(AgentRunRequest.id)
            .where(
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.agent_slug == agent_slug,
                AgentRunRequest.conversation_thread_id == thread_id,
                AgentRunRequest.status == "queued",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def lock_by_request_id(self, request_id: str) -> AgentRunRequest | None:
        """``SELECT ... FOR UPDATE`` by request_id; caller decides status branch."""
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(AgentRunRequest.request_id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        request_id: str,
        turn_id: str | None = None,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
        source: str = "chat",
        channel: str = "web",
        external_id: str | None = None,
        origin_metadata: dict | None = None,
        queue_policy: str = "enqueue",
        input_message_id: int,
        input_payload: dict | None = None,
        status: str = "queued",
        intent_hash: str | None = None,
        app_id: str | None = None,
        api_key_id: int | None = None,
    ) -> AgentRunRequest:
        request = AgentRunRequest(
            request_id=request_id,
            turn_id=turn_id,
            intent_hash=intent_hash,
            app_id=app_id,
            api_key_id=api_key_id,
            uid=str(uid),
            agent_slug=agent_slug,
            conversation_thread_id=conversation_thread_id,
            source=source,
            channel=channel,
            external_id=external_id,
            origin_metadata=origin_metadata or {},
            queue_policy=queue_policy,
            status=status,
            input_message_id=input_message_id,
            input_payload=input_payload or {},
        )
        self.db.add(request)
        await self.db.flush()
        return request

    def _queued_for_thread_query(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ):
        """返回线程待处理请求；Steer 优先，其余请求保持 FIFO。"""
        return (
            select(AgentRunRequest)
            .where(
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.agent_slug == agent_slug,
                AgentRunRequest.conversation_thread_id == conversation_thread_id,
                AgentRunRequest.status == "queued",
            )
            .order_by(
                (AgentRunRequest.queue_policy != "steer").asc(),
                AgentRunRequest.created_at.asc(),
                AgentRunRequest.id.asc(),
            )
        )

    async def get_pending_steer(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> AgentRunRequest | None:
        """读取线程内尚未派发的 Steer 请求。"""
        result = await self.db.execute(
            select(AgentRunRequest).where(
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.agent_slug == agent_slug,
                AgentRunRequest.conversation_thread_id == conversation_thread_id,
                AgentRunRequest.queue_policy == "steer",
                AgentRunRequest.status == "queued",
            )
        )
        return result.scalar_one_or_none()

    async def get_queue_head(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> AgentRunRequest | None:
        """Atomically read + lock the FIFO head (queued)."""
        result = await self.db.execute(
            self._queued_for_thread_query(uid=uid, agent_slug=agent_slug, conversation_thread_id=conversation_thread_id)
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_first_queued(
        self, *, uid: str, agent_slug: str, conversation_thread_id: str
    ) -> AgentRunRequest | None:
        """只读当前优先级最高的排队输入，不获取派发锁。"""
        result = await self.db.execute(
            self._queued_for_thread_query(
                uid=uid, agent_slug=agent_slug, conversation_thread_id=conversation_thread_id
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_queued(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> list[AgentRunRequest]:
        result = await self.db.execute(
            self._queued_for_thread_query(uid=uid, agent_slug=agent_slug, conversation_thread_id=conversation_thread_id)
        )
        return list(result.scalars().all())

    async def get_queue_position_for(self, request: AgentRunRequest) -> int:
        """给定已加载的请求对象，返回 1-based FIFO 位置；不在 queued 队列返回 0。"""
        if request.status != "queued":
            return 0
        if request.queue_policy == "steer":
            return 1

        result = await self.db.execute(
            select(func.count())
            .select_from(AgentRunRequest)
            .where(
                AgentRunRequest.uid == request.uid,
                AgentRunRequest.agent_slug == request.agent_slug,
                AgentRunRequest.conversation_thread_id == request.conversation_thread_id,
                AgentRunRequest.status == "queued",
                or_(
                    AgentRunRequest.queue_policy == "steer",
                    and_(
                        AgentRunRequest.queue_policy != "steer",
                        (AgentRunRequest.created_at, AgentRunRequest.id) < (request.created_at, request.id),
                    ),
                ),
            )
        )
        return int(result.scalar_one()) + 1

    async def get_queue_position(self, request_id: str) -> int:
        """1-based FIFO 位置；请求不在 queued 队列返回 0。

        用 COUNT(*) 统计排在前面的 queued 请求，O(1) 行扫描而非拉全量。
        """
        request = await self.get_by_request_id(request_id)
        if request is None:
            return 0
        return await self.get_queue_position_for(request)

    async def mark_dispatched(self, request_id: str, *, run_id: str) -> AgentRunRequest | None:
        request = await self.lock_by_request_id(request_id)
        if request is None or request.status != "queued":
            return None
        now = utc_now_naive()
        request.status = "dispatched"
        request.dispatched_run_id = run_id
        request.dispatched_at = now
        request.updated_at = now
        await self.db.flush()
        return request
