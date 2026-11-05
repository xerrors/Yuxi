"""持久原始输入、调度顺序与消费时消息投影。"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.input_messages import deserialize_input_message
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.shared.datetime import utc_now


class AgentInputRepository:
    """所有调度变更由调用方持有 Session 锁和事务。"""

    def __init__(self, db: AsyncSession):
        """绑定调用方事务。"""
        self.db = db

    async def create(self, *, input_id: str, messages: list[dict], **values) -> AgentInput:
        """每次提交创建一个独立输入并永久保存原始内容。"""
        if values["kind"] not in {"follow_up", "steer"} or not messages:
            raise ValueError("Input 需要有效类型及非空消息")
        item = AgentInput(id=input_id, messages=messages, status="pending", **values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def get_for_scope(
        self, *, input_id: str, thread_id: str, uid: str, app_id: str | None, for_update: bool = False
    ) -> AgentInput | None:
        """按完整资源作用域读取或锁定输入。"""
        statement = select(AgentInput).where(
            AgentInput.id == input_id, AgentInput.thread_id == thread_id, AgentInput.uid == uid, AgentInput.app_id == app_id
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.db.scalar(statement.execution_options(populate_existing=True))

    async def list_pending_inputs(self, *, thread_id: str, uid: str, app_id: str | None, turn_id: str | None = None) -> list[AgentInput]:
        """按引导优先、原始接收序号返回队列。"""
        cutoff = await self._resume_sequence(turn_id)
        pending = list(
            await self.db.scalars(
                select(AgentInput)
                .where(
                    AgentInput.thread_id == thread_id,
                    AgentInput.uid == uid,
                    AgentInput.app_id == app_id,
                    AgentInput.status == "pending",
                    AgentInput.received_seq > cutoff,
                )
                .order_by((AgentInput.kind == "steer").desc(), AgentInput.received_seq)
            )
        )
        return sorted(
            pending,
            key=lambda item: (
                not item.resumes_queue,
                -item.received_seq if item.resumes_queue else 0,
            ),
        )

    async def has_pending_inputs(self, *, thread_id: str, uid: str, app_id: str | None) -> bool:
        """读取授权 Thread 是否仍有待办，包括附件准备中的输入。"""
        return bool(
            await self.db.scalar(
                select(
                    exists().where(
                        AgentInput.thread_id == thread_id,
                        AgentInput.uid == uid,
                        AgentInput.app_id == app_id,
                        AgentInput.status == "pending",
                    )
                )
            )
        )

    async def get_pending_steer(
        self, *, thread_id: str, uid: str, app_id: str | None, for_update: bool = True, turn_id: str | None = None
    ) -> AgentInput | None:
        """读取最早的待消费引导，用于安全边界就绪检查。"""
        cutoff = await self._resume_sequence(turn_id)
        statement = (
            select(AgentInput)
            .where(
                AgentInput.received_seq > cutoff,
                AgentInput.thread_id == thread_id,
                AgentInput.uid == uid,
                AgentInput.app_id == app_id,
                AgentInput.kind == "steer",
                AgentInput.status == "pending",
            )
            .order_by(AgentInput.received_seq)
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.db.scalar(statement.execution_options(populate_existing=for_update))

    async def ready_batch(self, *, thread_id: str, uid: str, app_id: str | None, turn_id: str | None = None) -> list[AgentInput]:
        """Session 锁内选择就绪队头，steer 仅合并连续就绪前缀。"""
        pending = await self.list_pending_inputs(thread_id=thread_id, uid=uid, app_id=app_id, turn_id=turn_id)
        preparation = await AttachmentRepository(self.db).statuses_for_inputs([item.id for item in pending])
        batch = []
        for item in pending:
            if preparation[item.id]["attachment_status"] != "ready":
                break
            if batch and item.kind != "steer":
                break
            batch.append(item)
            if item.kind == "follow_up" or item.resumes_queue:
                break
        return batch

    async def list_messages(self, input_id: str) -> list[Message]:
        """返回消费后真实消息；待消费和取消输入没有投影。"""
        return list(await self.db.scalars(select(Message).where(Message.source_input_id == input_id).order_by(Message.input_position)))

    async def input_ids_for_runs(self, run_ids: list[str]) -> dict[str, list[str]]:
        """从消费关系批量派生 Run 的有序输入身份。"""
        result = {run_id: [] for run_id in run_ids}
        rows = await self.db.execute(
            select(AgentInput.consumed_run_id, AgentInput.id)
            .where(AgentInput.consumed_run_id.in_(run_ids))
            .order_by(AgentInput.received_seq)
        )
        for run_id, input_id in rows:
            result[run_id].append(input_id)
        return result

    async def count_by_app(self, app_ids: list[str]) -> dict[str, int]:
        """按 APP 统计外部持久输入，排除内部协作、HTTP 查询与幂等重试。"""
        if not app_ids:
            return {}
        rows = await self.db.execute(
            select(AgentInput.app_id, func.count(AgentInput.id))
            .where(AgentInput.app_id.in_(app_ids), AgentInput.source != "cooperation")
            .group_by(AgentInput.app_id)
        )
        return {app_id: total for app_id, total in rows if app_id}

    async def origins(self, *, thread_id: str, uid: str, app_id: str | None, input_ids: list[str]) -> list:
        """只返回授权输入及同主体来源会话，原始 metadata 不作为显示身份。"""
        return list(
            (
                await self.db.execute(
                    select(AgentInput.id, AgentInput.source, AgentInput.api_key_id, AgentRun.id.label("source_run_id"), Session)
                    .outerjoin(
                        AgentRun,
                        and_(
                            AgentInput.source == "cooperation",
                            AgentRun.id == AgentInput.origin_metadata["created_by_run_id"].as_string(),
                            AgentRun.uid == uid,
                            AgentRun.app_id == app_id,
                        ),
                    )
                    .outerjoin(
                        Session,
                        and_(
                            Session.id == AgentRun.session_record_id,
                            Session.uid == uid,
                            Session.app_id == app_id,
                            Session.status != "deleted",
                        ),
                    )
                    .where(
                        AgentInput.id.in_(input_ids), AgentInput.thread_id == thread_id, AgentInput.uid == uid, AgentInput.app_id == app_id
                    )
                )
            ).all()
        )

    async def consume(self, *, inputs: list[AgentInput], turn_id: str, run_id: str) -> list[Message]:
        """同一事务固定消费归属、创建消息并绑定附件，失败全部回滚。"""
        turn = await self.db.get(AgentTurn, turn_id)
        run = await self.db.get(AgentRun, run_id)
        if not inputs or turn is None or run is None or run.turn_id != turn_id:
            raise ValueError("消费需要非空输入与同一 Turn 的 Run")
        if await self.db.scalar(select(AgentInput.id).where(AgentInput.consumed_run_id == run_id).limit(1)):
            raise ValueError("Run 已领取输入")
        projected = []
        for item in inputs:
            await self.db.refresh(item, with_for_update=True)
            if item.status != "pending":
                raise ValueError("Input 已领取或取消")
            if (item.uid, item.app_id, item.thread_id) != (turn.uid, turn.app_id, turn.thread_id) or run.thread_id != item.thread_id:
                raise ValueError("Input、Turn 与 Run 作用域不一致")
            if await AttachmentRepository(self.db).has_unready(item.id):
                raise ValueError("Input 附件尚未就绪")
            now = utc_now()
            item.status = "consumed"
            item.turn_id = turn_id
            item.consumed_run_id = run_id
            item.consumed_at = now
            await self.db.flush()
            for position, raw in enumerate(item.messages):
                message = deserialize_input_message(raw)
                persisted = Message(
                    session_record_id=run.session_record_id,
                    role="user",
                    content=message.content,
                    message_type=message.message_type,
                    image_content=message.image_content,
                    extra_metadata={
                        **message.extra_metadata,
                        "raw_message": message.raw_message(),
                        "input_source": {"source": item.source, "channel": item.channel, "external_id": item.external_id},
                    },
                    source_input_id=item.id,
                    input_position=position,
                    received_at=item.created_at,
                    created_at=now,
                    turn_id=turn_id,
                    run_id=run_id,
                    delivery_status="dispatched",
                )
                self.db.add(persisted)
                await self.db.flush()
                projected.append(persisted)
            await self.db.execute(update(AgentAttachment).where(AgentAttachment.input_id == item.id).values(message_id=projected[-1].id))
        await self.db.flush()
        return projected

    async def cancel(self, input_item: AgentInput) -> AgentInput:
        """仅取消选中的待消费输入，保留正文与附件身份。"""
        if input_item.status != "pending":
            raise ValueError("只能取消待消费 Input")
        input_item.status = "cancelled"
        input_item.cancelled_at = utc_now()
        await self.db.flush()
        return input_item

    async def _resume_sequence(self, turn_id: str | None) -> int:
        """恢复轮次只接受其后的新引导，保留旧队列到本轮完成。"""
        if turn_id is None:
            return 0
        return (
            await self.db.scalar(
                select(func.max(AgentInput.received_seq)).where(
                    AgentInput.turn_id == turn_id,
                    AgentInput.resumes_queue,
                )
            )
        ) or 0
