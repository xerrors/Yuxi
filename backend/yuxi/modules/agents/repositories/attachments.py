"""附件行的隔离、归属、就绪与有界清理查询。"""

from sqlalchemy import exists, select

from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.shared.datetime import utc_now


class AttachmentRepository:
    """所有变更复用调用方事务，文件操作持有附件行锁。"""

    def __init__(self, db):
        """绑定持久事务。"""
        self.db = db

    async def create(self, **values) -> AgentAttachment:
        """持久化上传文件的信息和隔离归属。"""
        attachment = AgentAttachment(**values)
        self.db.add(attachment)
        await self.db.flush()
        return attachment

    async def get_for_scope(self, file_id, uid, app_id, *, lock=False) -> AgentAttachment | None:
        """文件 ID 只在所属用户与 APP 内可见。"""
        statement = select(AgentAttachment).where(
            AgentAttachment.id == file_id,
            AgentAttachment.uid == uid,
            AgentAttachment.app_id == app_id,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.db.scalar(statement.execution_options(populate_existing=lock))

    async def lock_for_scope(self, file_ids, uid, app_id) -> list[AgentAttachment]:
        """固定行锁顺序，绑定与解析、删除互斥。"""
        return list(
            await self.db.scalars(
                select(AgentAttachment)
                .where(AgentAttachment.id.in_(file_ids), AgentAttachment.uid == uid, AgentAttachment.app_id == app_id)
                .order_by(AgentAttachment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    async def list_for_thread(self, thread_id, uid, app_id) -> list[AgentAttachment]:
        """返回当前作用域中已提交到会话的文件。"""
        statement = (
            select(AgentAttachment)
            .join(AgentInput, AgentInput.id == AgentAttachment.input_id)
            .where(AgentInput.thread_id == thread_id, AgentAttachment.uid == uid, AgentAttachment.app_id == app_id)
        )
        return list(await self.db.scalars(statement.order_by(AgentAttachment.created_at, AgentAttachment.id)))

    async def list_for_input(self, input_id, *, lock=False) -> list[AgentAttachment]:
        """接收事务与准备事务都在所属 Session 锁内操作。"""
        statement = select(AgentAttachment).where(AgentAttachment.input_id == input_id).order_by(AgentAttachment.id)
        if lock:
            statement = statement.with_for_update()
        return list(await self.db.scalars(statement.execution_options(populate_existing=lock)))

    async def has_unready(self, input_id) -> bool:
        """调度和消费统一拒绝尚未准备的附件。"""
        return bool(await self.db.scalar(select(exists().where(AgentAttachment.input_id == input_id, AgentAttachment.status != "ready"))))

    async def statuses_for_inputs(self, input_ids) -> dict[str, dict]:
        """一次读取 Input 的准备结果，空附件集合天然就绪。"""
        result = {input_id: {"attachment_status": "ready", "attachment_error": None} for input_id in input_ids}
        rows = await self.db.execute(
            select(AgentAttachment.input_id, AgentAttachment.status, AgentAttachment.error).where(AgentAttachment.input_id.in_(input_ids))
        )
        for input_id, status, error in rows:
            if status != "ready":
                result[input_id] = {"attachment_status": "preparing", "attachment_error": error}
        return result

    async def for_messages(self, message_ids) -> dict[int, list[AgentAttachment]]:
        """按最终 Message 归属批量读取附件。"""
        result = {message_id: [] for message_id in message_ids}
        rows = await self.db.execute(
            select(AgentAttachment.message_id, AgentAttachment)
            .where(AgentAttachment.message_id.in_(message_ids))
            .order_by(AgentAttachment.created_at, AgentAttachment.id)
        )
        for message_id, attachment in rows:
            result[message_id].append(attachment)
        return result

    async def list_preparing_inputs(self, thread_id, uid, app_id) -> list[AgentInput]:
        """已接收的取消输入也完成文件准备，取消只影响执行。"""
        return list(
            await self.db.scalars(
                select(AgentInput)
                .where(
                    AgentInput.thread_id == thread_id,
                    AgentInput.uid == uid,
                    AgentInput.app_id == app_id,
                    exists().where(AgentAttachment.input_id == AgentInput.id, AgentAttachment.status == "preparing"),
                )
                .order_by(AgentInput.received_seq)
            )
        )

    async def expired_drafts(self, uid, app_id) -> list[AgentAttachment]:
        """直接领取最多 32 个到期草稿，不扫描对象存储。"""
        return list(
            await self.db.scalars(
                select(AgentAttachment)
                .where(
                    AgentAttachment.uid == uid,
                    AgentAttachment.app_id == app_id,
                    AgentAttachment.status == "draft",
                    AgentAttachment.expires_at <= utc_now(),
                )
                .order_by(AgentAttachment.expires_at, AgentAttachment.id)
                .limit(32)
                .with_for_update(skip_locked=True)
            )
        )

    async def prepared_sources(self, thread_id) -> list[AgentAttachment]:
        """就绪提交后清理临时来源；失败保留位置供恢复循环重试。"""
        return list(
            await self.db.scalars(
                select(AgentAttachment)
                .join(AgentInput, AgentInput.id == AgentAttachment.input_id)
                .where(
                    AgentInput.thread_id == thread_id,
                    AgentAttachment.status == "ready",
                    AgentAttachment.object_name.is_not(None),
                )
                .order_by(AgentAttachment.id)
                .with_for_update(of=AgentAttachment)
            )
        )
