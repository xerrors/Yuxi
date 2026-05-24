import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_business import ScheduleDefinition, ScheduleLog
from yuxi.utils import logger


class ScheduleTriggerError(Exception):
    """定时任务触发异常"""

    pass


class ScheduleService:
    """定时任务触发相关核心业务服务层"""

    async def create_scheduled_run(
        self,
        *,
        schedule: ScheduleDefinition,
        db: AsyncSession,
    ) -> tuple[str, str]:
        """
        为定时任务创建新对话(Thread)与运行任务(AgentRun)并推入ARQ队列。
        本方法由隔离事务(T2)对应的独立 Session 调用，内部执行 commit / rollback。
        """
        try:
            # 1. 解析 agent_config 获取 agent_id
            config_repo = AgentConfigRepository(db)
            config_item = await config_repo.get_by_id(config_id=schedule.agent_config_id)
            if config_item is None:
                raise ScheduleTriggerError(f"agent_config {schedule.agent_config_id} 不存在")

            agent_id = config_item.agent_id

            # 2. 创建新 thread
            thread_id = str(uuid.uuid4())
            conv_repo = ConversationRepository(db)
            await conv_repo.create_conversation(
                user_id=str(schedule.user_id),
                agent_id=agent_id,
                title=f"[定时] {schedule.name}",
                thread_id=thread_id,
                metadata={"agent_config_id": schedule.agent_config_id, "schedule_id": schedule.id},
            )

            # 3. 创建 AgentRun（在 input_payload 中写入定时与自动审批标记）
            run_id = str(uuid.uuid4())
            request_id = str(uuid.uuid4())
            input_payload = {
                "query": schedule.query,
                "config": {
                    "thread_id": thread_id,
                    "agent_config_id": schedule.agent_config_id,
                },
                "image_content": schedule.image_content,
                "agent_id": agent_id,
                "thread_id": thread_id,
                "user_id": str(schedule.user_id),
                "request_id": request_id,
                "created_at": datetime.now(UTC).isoformat(),
                "scheduled": True,
                "auto_approve": True,
            }

            run_repo = AgentRunRepository(db)
            await run_repo.create_run(
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
                user_id=str(schedule.user_id),
                request_id=request_id,
                input_payload=input_payload,
            )

            # 先 flush 保证数据落地，ID 生效
            await db.flush()

            # 4. 入队 ARQ 队列
            queue = await get_arq_pool()
            await queue.enqueue_job("process_agent_run", run_id, _job_id=f"run:{run_id}")

            # 提交 T2 子事务
            await db.commit()
            return thread_id, run_id

        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to create scheduled run for schedule {schedule.id}: {e}")
            raise ScheduleTriggerError(str(e)) from e

    async def manual_trigger_schedule(
        self,
        *,
        schedule: ScheduleDefinition,
        db: AsyncSession,
    ) -> tuple[str, str]:
        """
        手动立即触发定时任务。由于是手动触发，不属于后台 Cron 轮询热路径，
        可以直接在当前的 Session 事务中执行，但逻辑与 create_scheduled_run 保持一致。
        """
        # 解析 agent_config 获取 agent_id
        config_repo = AgentConfigRepository(db)
        config_item = await config_repo.get_by_id(config_id=schedule.agent_config_id)
        if config_item is None:
            raise ScheduleTriggerError(f"agent_config {schedule.agent_config_id} 不存在")

        agent_id = config_item.agent_id

        # 创建新 thread
        thread_id = str(uuid.uuid4())
        conv_repo = ConversationRepository(db)
        await conv_repo.create_conversation(
            user_id=str(schedule.user_id),
            agent_id=agent_id,
            title=f"[手动触发] {schedule.name}",
            thread_id=thread_id,
            metadata={"agent_config_id": schedule.agent_config_id, "schedule_id": schedule.id},
        )

        # 创建 AgentRun（自动审批）
        run_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        input_payload = {
            "query": schedule.query,
            "config": {
                "thread_id": thread_id,
                "agent_config_id": schedule.agent_config_id,
            },
            "image_content": schedule.image_content,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "user_id": str(schedule.user_id),
            "request_id": request_id,
            "created_at": datetime.now(UTC).isoformat(),
            "scheduled": True,
            "auto_approve": True,
        }

        run_repo = AgentRunRepository(db)
        await run_repo.create_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            user_id=str(schedule.user_id),
            request_id=request_id,
            input_payload=input_payload,
        )

        await db.flush()

        # 写入触发日志
        log = ScheduleLog(
            id=str(uuid.uuid4()),
            schedule_id=schedule.id,
            run_id=run_id,
            thread_id=thread_id,
            status="triggered",
            execution_status="pending",
            trigger_delay_ms=0,
            created_at=datetime.now(UTC),
        )
        db.add(log)

        # 入队 ARQ 队列
        queue = await get_arq_pool()
        await queue.enqueue_job("process_agent_run", run_id, _job_id=f"run:{run_id}")
        return thread_id, run_id
