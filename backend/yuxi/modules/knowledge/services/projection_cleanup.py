"""消费知识投影清理 outbox，独立拥有事务与锁。"""

from sqlalchemy import select

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.models import KnowledgeProjectionOutbox
from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock
from yuxi.modules.knowledge.runtime import knowledge_base
from yuxi.shared.datetime import utc_now


async def process_knowledge_projections(_context: dict | None = None) -> list[str]:
    """由知识 worker 直接处理待清理意图，不创建 JobTracker 任务或恢复执行上下文。"""
    async with pg_manager.get_async_session_context() as session:
        keys = list(
            (
                await session.scalars(
                    select(KnowledgeProjectionOutbox.id)
                    .where(KnowledgeProjectionOutbox.status == "pending")
                    .order_by(KnowledgeProjectionOutbox.updated_at, KnowledgeProjectionOutbox.id)
                    .limit(100)
                )
            ).all()
        )
    applied = []
    for event_id in keys:
        async with pg_manager.get_async_session_context() as session:
            event = await session.scalar(
                select(KnowledgeProjectionOutbox)
                .where(KnowledgeProjectionOutbox.id == event_id, KnowledgeProjectionOutbox.status == "pending")
                .with_for_update(skip_locked=True)
            )
            if event is None:
                continue
            try:
                async with knowledge_projection_lock(event.kb_id):
                    if event.operation == "knowledge_base_deleted":
                        await knowledge_base.cleanup_deleted_knowledge_base(event.kb_id)
                    elif event.operation == "media_build_cleanup":
                        from yuxi.modules.knowledge.media.jobs import cleanup_failed_media_build

                        await cleanup_failed_media_build(session, event)
                    elif event.operation == "document_revision_deleted":
                        from yuxi.modules.knowledge.services.revision_cleanup import cleanup_retired_revision

                        await cleanup_retired_revision(event.kb_id, event.aggregate_id)
                    elif event.operation == "document_derivatives_cleanup":
                        from yuxi.modules.knowledge.services.revision_cleanup import cleanup_file_derivatives

                        await cleanup_file_derivatives(event.kb_id, event.aggregate_id)
                    elif event.operation in {"file_deleted", "generation_cleanup"}:
                        executor = await knowledge_base.get_cleanup_executor(event.kb_id)
                        if executor is not None:
                            generation = event.generation if event.operation == "generation_cleanup" else None
                            await executor.delete_file_chunks_only(event.kb_id, event.aggregate_id, generation=generation)
                            if event.operation == "file_deleted":
                                await executor.cleanup_file_resources(event.kb_id, event.aggregate_id)
                    else:
                        raise ValueError(f"Unknown projection operation: {event.operation}")
                event.status = "applied"
                event.last_error = None
                event.applied_at = utc_now()
                applied.append(event.event_key)
            except Exception as exc:
                event.last_error = str(exc)[:4000]
                event.updated_at = utc_now()
                logger.error("Knowledge projection failed: event_id={}, error_type={}", event.id, type(exc).__name__)
    return applied
