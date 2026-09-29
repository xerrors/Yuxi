"""Session 输入事件的幂等接收记录。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentSessionInputReceipt


class AgentSessionInputReceiptRepository:
    """持久记录消息意图并固定控制事件的首次目标。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, receipt_id: str) -> AgentSessionInputReceipt | None:
        """按幂等身份读取首次接收记录。"""
        return await self.db.get(AgentSessionInputReceipt, receipt_id)

    async def create(
        self,
        *,
        receipt_id: str,
        uid: str,
        app_id: str | None,
        thread_id: str,
        event_type: str,
        intent_hash: str,
        turn_id: str | None,
        run_id: str | None,
    ) -> AgentSessionInputReceipt:
        """在执行副作用的同一事务中保存接收事实。"""
        receipt = AgentSessionInputReceipt(
            id=receipt_id,
            uid=uid,
            app_id=app_id,
            conversation_thread_id=thread_id,
            event_type=event_type,
            intent_hash=intent_hash,
            turn_id=turn_id,
            run_id=run_id,
        )
        self.db.add(receipt)
        await self.db.flush()
        return receipt
