"""AgentRunRequest repository.

The model has an autoincrement Integer ``id`` (cluster PK \u2014 used only for
FIFO ordering stability) and a unique String ``request_id`` (the idempotency
key shared with the Message and AgentRun tables).  All public lookups key on
``request_id``.

State transitions use ``SELECT \u2026 FOR UPDATE`` to serialise dispatch and cancel
contention on the same row.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRunRequest
from yuxi.utils.datetime_utils import utc_now_naive


class AgentRunRequestRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_by_request_id(self, request_id: str) -> AgentRunRequest | None:
        result = await self.db.execute(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
        return result.scalar_one_or_none()

    async def lock_by_request_id(self, request_id: str) -> AgentRunRequest | None:
        """``SELECT ... FOR UPDATE`` by request_id; caller decides status branch."""
        result = await self.db.execute(
            select(AgentRunRequest).where(AgentRunRequest.request_id == request_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        request_id: str,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
        source: str = "chat",
        queue_policy: str = "enqueue",
        input_message_id: int,
        input_payload: dict | None = None,
        status: str = "queued",
        target_run_id: str | None = None,
    ) -> AgentRunRequest:
        request = AgentRunRequest(
            request_id=request_id,
            uid=str(uid),
            agent_slug=agent_slug,
            conversation_thread_id=conversation_thread_id,
            source=source,
            queue_policy=queue_policy,
            status=status,
            input_message_id=input_message_id,
            input_payload=input_payload or {},
            target_run_id=target_run_id,
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
        """Base SELECT for queued requests in a (uid, agent, thread) tuple, ordered by FIFO."""
        return (
            select(AgentRunRequest)
            .where(
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.agent_slug == agent_slug,
                AgentRunRequest.conversation_thread_id == conversation_thread_id,
                AgentRunRequest.status == "queued",
                AgentRunRequest.queue_policy.in_(("enqueue", "reject")),
            )
            .order_by(AgentRunRequest.created_at.asc(), AgentRunRequest.id.asc())
        )

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

    async def list_pending(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
    ) -> list[AgentRunRequest]:
        """返回 Steer 优先、普通 FIFO 随后的待处理请求。"""
        result = await self.db.execute(
            select(AgentRunRequest)
            .where(
                AgentRunRequest.uid == str(uid),
                AgentRunRequest.agent_slug == agent_slug,
                AgentRunRequest.conversation_thread_id == conversation_thread_id,
                AgentRunRequest.status.in_(("queued", "steer_ready")),
            )
            .order_by(
                (AgentRunRequest.queue_policy != "steer").asc(),
                AgentRunRequest.created_at.asc(),
                AgentRunRequest.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_pending_steer(
        self,
        *,
        uid: str,
        agent_slug: str,
        conversation_thread_id: str,
        lock: bool = False,
    ) -> AgentRunRequest | None:
        """读取线程唯一的 queued/steer_ready Steer request。"""
        query = select(AgentRunRequest).where(
            AgentRunRequest.uid == str(uid),
            AgentRunRequest.agent_slug == agent_slug,
            AgentRunRequest.conversation_thread_id == conversation_thread_id,
            AgentRunRequest.queue_policy == "steer",
            AgentRunRequest.status.in_(("queued", "steer_ready")),
        )
        if lock:
            query = query.with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_pending_steer_for_target(self, target_run_id: str, *, lock: bool = False) -> AgentRunRequest | None:
        """按目标 Run 读取唯一待生效 Steer。"""
        query = select(AgentRunRequest).where(
            AgentRunRequest.target_run_id == target_run_id,
            AgentRunRequest.queue_policy == "steer",
            AgentRunRequest.status.in_(("queued", "steer_ready")),
        )
        if lock:
            query = query.with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_dispatched_steer_for_target(self, target_run_id: str) -> AgentRunRequest | None:
        """读取已为目标 Run 创建 replacement 的 Steer request。"""
        result = await self.db.execute(
            select(AgentRunRequest).where(
                AgentRunRequest.target_run_id == target_run_id,
                AgentRunRequest.queue_policy == "steer",
                AgentRunRequest.status == "dispatched",
            )
        )
        return result.scalar_one_or_none()

    async def get_queue_position_for(self, request: AgentRunRequest) -> int:
        """给定已加载的请求对象，返回 1-based FIFO 位置；不在 queued 队列返回 0。"""
        if request.status != "queued" or request.queue_policy != "enqueue":
            return 0
        result = await self.db.execute(
            select(func.count())
            .select_from(AgentRunRequest)
            .where(
                AgentRunRequest.uid == request.uid,
                AgentRunRequest.agent_slug == request.agent_slug,
                AgentRunRequest.conversation_thread_id == request.conversation_thread_id,
                AgentRunRequest.status == "queued",
                AgentRunRequest.queue_policy == "enqueue",
                (AgentRunRequest.created_at, AgentRunRequest.id) < (request.created_at, request.id),
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
        if request is None or request.status not in {"queued", "steer_ready"}:
            return None
        now = utc_now_naive()
        request.status = "dispatched"
        request.dispatched_run_id = run_id
        request.dispatched_at = now
        request.updated_at = now
        await self.db.flush()
        return request
