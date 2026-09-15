"""文档回收站持久化；调用方拥有知识库权限检查与外部产物清理。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.document_retention import restore_window_open, retention_deadline
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile
from yuxi.utils.datetime_utils import utc_now


class DocumentTrashRepository:
    """以文件行和知识库树锁串行化删除、恢复与清理领取。"""

    async def trash(
        self, kb_id: str, file_ids: list[str], *, deleted_by: str, now: datetime | None = None
    ) -> list[KnowledgeFile]:
        """原子移入整批文件及目录后代；处理中任一项使整批失败。"""
        now = now or utc_now()
        deadline = retention_deadline(now)
        roots = set(file_ids)
        if not roots:
            return []
        async with pg_manager.get_async_session_context() as session:
            await self._lock_tree(session, kb_id)
            records = await self._lock_descendants(session, kb_id, roots)
            by_id = {row.file_id: row for row in records}
            if not roots.issubset(by_id):
                raise ValueError("File not found")
            active = [row for row in records if row.deleted_at is None]
            if any(
                row.status in {"processing", "waiting", "parsing", "indexing"} or row.processing_task_id
                for row in active
            ):
                raise ValueError("Processing files cannot be moved to trash")
            deletion_id = uuid4().hex
            for row in active:
                row.deleted_at = now
                row.purge_after = deadline
                row.deletion_id = deletion_id
                row.deleted_by = deleted_by
                row.updated_at = now
            if active:
                await session.execute(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.kb_id == kb_id)
                    .values(
                        mindmap=None,
                        mindmap_file_ids=None,
                        mindmap_metadata=None,
                        sample_questions=None,
                    )
                )
            await session.flush()
            return records

    async def get_trashed(self, kb_id: str, file_id: str) -> KnowledgeFile | None:
        """读取指定知识库回收站项，不暴露普通活跃文件。"""
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(KnowledgeFile).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.deleted_at.is_not(None),
                )
            )

    async def has_trashed(self, kb_id: str) -> bool:
        """判断整库删除是否会绕过尚未清理的回收站项。"""
        async with pg_manager.get_async_session_context() as session:
            return (
                await session.scalar(
                    select(KnowledgeFile.file_id)
                    .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_not(None))
                    .limit(1)
                )
            ) is not None

    async def list_trashed(self, kb_id: str, *, offset: int = 0, limit: int = 100) -> list[KnowledgeFile]:
        """分页列出回收站，包括等待重试的清理项。"""
        async with pg_manager.get_async_session_context() as session:
            return list(
                (
                    await session.scalars(
                        select(KnowledgeFile)
                        .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_not(None))
                        .order_by(KnowledgeFile.deleted_at.desc(), KnowledgeFile.file_id)
                        .offset(max(offset, 0))
                        .limit(min(max(limit, 1), 1000))
                    )
                ).all()
            )

    async def count_trashed(self, kb_id: str) -> int:
        """统计当前知识库回收站项总数。"""
        async with pg_manager.get_async_session_context() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeFile)
                    .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_not(None))
                )
                or 0
            )

    async def list_storage_references(self, kb_id: str | None = None) -> list[KnowledgeFile]:
        """清理专用：包含回收站的对象引用，禁止作为普通文件读取入口。"""
        async with pg_manager.get_async_session_context() as session:
            query = select(KnowledgeFile)
            if kb_id is not None:
                query = query.where(KnowledgeFile.kb_id == kb_id)
            return list((await session.scalars(query)).all())

    async def restore(self, kb_id: str, file_id: str, *, now: datetime | None = None) -> list[KnowledgeFile]:
        """恢复当前目录删除批次；已过期、开始清理或父目录不可用时拒绝。"""
        async with pg_manager.get_async_session_context() as session:
            await self._lock_tree(session, kb_id)
            records = await self._lock_descendants(session, kb_id, {file_id})
            root = next((row for row in records if row.file_id == file_id), None)
            if root is None or root.deleted_at is None:
                raise ValueError("Trashed file not found")
            now = now or utc_now()
            restoring = [row for row in records if row.deletion_id == root.deletion_id]
            if any(
                row.purge_started_at is not None
                or not restore_window_open(row.deleted_at, now=now)
                or row.purge_after <= now
                for row in restoring
            ):
                raise ValueError("Restore window closed or purge already started")
            ids = {row.file_id for row in restoring}
            for row in restoring:
                if row.parent_id and row.parent_id not in ids:
                    parent = await session.scalar(
                        select(KnowledgeFile).where(
                            KnowledgeFile.file_id == row.parent_id,
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.deleted_at.is_(None),
                            KnowledgeFile.is_folder.is_(True),
                        )
                    )
                    if parent is None:
                        raise ValueError("Restore parent is unavailable")
                conflict = await session.scalar(
                    select(KnowledgeFile.file_id)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.deleted_at.is_(None),
                        KnowledgeFile.parent_id == row.parent_id,
                        KnowledgeFile.filename == row.filename,
                        KnowledgeFile.file_id != row.file_id,
                    )
                    .limit(1)
                )
                if conflict:
                    raise ValueError("Restore name conflicts with an active file")
            for row in restoring:
                row.deleted_at = row.purge_after = None
                row.deletion_id = row.deleted_by = None
                row.purge_error = None
                row.updated_at = now
            await session.flush()
            return restoring

    async def claim_due(
        self, *, limit: int = 100, lease_seconds: int = 300, now: datetime | None = None
    ) -> list[KnowledgeFile]:
        """并发领取到期行；过期租约使用新token接管，清理开始后不再允许恢复。"""
        now = now or utc_now()
        retention_deadline(now)  # 要求带时区，避免数据库会话时区改变期限含义。
        if lease_seconds <= 0:
            raise ValueError("Purge lease must be positive")
        async with pg_manager.get_async_session_context() as session:
            rows = list(
                (
                    await session.scalars(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.deleted_at.is_not(None),
                            KnowledgeFile.purge_after <= now,
                            or_(KnowledgeFile.purge_lease_until.is_(None), KnowledgeFile.purge_lease_until <= now),
                        )
                        .order_by(KnowledgeFile.purge_after, KnowledgeFile.file_id)
                        .limit(min(max(limit, 1), 1000))
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                row.purge_started_at = row.purge_started_at or now
                row.purge_lease_until = now + timedelta(seconds=lease_seconds)
                row.purge_token = uuid4().hex
            await session.flush()
            return rows

    async def set_purge_objects(
        self, file_id: str, deletion_id: str, purge_token: str, objects: list[dict[str, str]]
    ) -> bool:
        """首次清理前保存对象清单；重试保留首次快照而非覆盖为不完整列表。"""
        async with pg_manager.get_async_session_context() as session:
            row = await session.scalar(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.deletion_id == deletion_id,
                    KnowledgeFile.deleted_at.is_not(None),
                    KnowledgeFile.purge_token == purge_token,
                    KnowledgeFile.purge_lease_until > func.now(),
                )
                .with_for_update()
            )
            if row is None:
                return False
            if row.purge_objects is None:
                row.purge_objects = objects
            return True

    async def complete_purge(self, file_id: str, deletion_id: str, purge_token: str) -> bool:
        """外部产物已清理后，以当前租约删除元数据；旧token不得完成新领取。"""
        async with pg_manager.get_async_session_context() as session:
            row = await session.scalar(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.deleted_at.is_not(None),
                    KnowledgeFile.deletion_id == deletion_id,
                    KnowledgeFile.purge_token == purge_token,
                    KnowledgeFile.purge_lease_until > func.now(),
                )
                .with_for_update()
            )
            if row is None:
                return False
            await session.delete(row)
            return True

    async def fail_purge(self, file_id: str, deletion_id: str, purge_token: str, error: str) -> bool:
        """保存失败并释放领取；保持开始标记，避免部分销毁后错误恢复。"""
        async with pg_manager.get_async_session_context() as session:
            row = await session.scalar(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.deleted_at.is_not(None),
                    KnowledgeFile.deletion_id == deletion_id,
                    KnowledgeFile.purge_token == purge_token,
                    KnowledgeFile.purge_lease_until > func.now(),
                )
                .with_for_update()
            )
            if row is None:
                return False
            row.purge_error = error[:2000]
            row.purge_token = None
            row.purge_lease_until = utc_now() + timedelta(hours=1)
            return True

    @staticmethod
    async def _lock_tree(session: AsyncSession, kb_id: str) -> None:
        """复用普通文件树的事务级advisory锁键。"""
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(kb_id))))

    @staticmethod
    async def _lock_descendants(session: AsyncSession, kb_id: str, roots: set[str]) -> list[KnowledgeFile]:
        """递归查找所选子树；UNION去重也避免坏数据中的环无限扩张。"""
        tree = (
            select(KnowledgeFile.file_id)
            .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id.in_(sorted(roots)))
            .cte("trash_tree", recursive=True)
        )
        tree = tree.union(
            select(KnowledgeFile.file_id)
            .join(tree, KnowledgeFile.parent_id == tree.c.file_id)
            .where(KnowledgeFile.kb_id == kb_id)
        )
        return list(
            (
                await session.scalars(
                    select(KnowledgeFile)
                    .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id.in_(select(tree.c.file_id)))
                    .order_by(KnowledgeFile.file_id)
                    .with_for_update()
                )
            ).all()
        )
