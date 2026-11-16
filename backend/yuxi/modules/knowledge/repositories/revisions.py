"""当前正文来源映射的登记与引用事实，事务由调用用例拥有。"""

from sqlalchemy import delete, select

from yuxi.modules.knowledge.models import (
    KnowledgeBase,
    KnowledgeDocumentRevision,
    KnowledgeFile,
    KnowledgeProjectionOutbox,
)
from yuxi.modules.knowledge.revision_identity import body_revision, original_revision, source_object_uri
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import InvalidOperationError, StateConflictError


class DocumentRevisionRepository:
    """登记当前正文、原件和媒体对象，并撤回被替代的映射。"""

    async def withdrawn_revision(self, session, kb_id: str, revision: str):
        """回收只接收本库已经撤回的版本，缺失表示无需再次处理。"""
        record = await session.get(KnowledgeDocumentRevision, revision)
        if record is not None and (record.kb_id != kb_id or record.deleted_at is None):
            raise InvalidOperationError("不能回收未撤回或不属于当前库的修订")
        return record

    async def deleted_file_artifacts(self, session, kb_id: str, file_id: str):
        """回收前核对文件 tombstone，并读取其对象映射。"""
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id, KnowledgeFile.kb_id == kb_id))
        if file is not None and file.deleted_at is None:
            raise InvalidOperationError("不能回收仍有效的文件")
        records = list(
            await session.scalars(
                select(KnowledgeDocumentRevision).where(
                    KnowledgeDocumentRevision.kb_id == kb_id,
                    KnowledgeDocumentRevision.file_id == file_id,
                )
            )
        )
        return file, records

    async def retire_kind(self, session, file, kind: str, *, keep_revision: str | None = None, operator_id: str | None = None) -> int:
        """撤回不再作为当前产物的映射，并在同一事务登记对象回收意图。"""
        conditions = [
            KnowledgeDocumentRevision.kb_id == file.kb_id,
            KnowledgeDocumentRevision.file_id == file.file_id,
            KnowledgeDocumentRevision.kind == kind,
            KnowledgeDocumentRevision.deleted_at.is_(None),
        ]
        if keep_revision is not None:
            conditions.append(KnowledgeDocumentRevision.revision != keep_revision)
        rows = await session.scalars(select(KnowledgeDocumentRevision).where(*conditions).with_for_update())
        retired = 0
        for record in rows:
            await self.retire_record(session, file, record, operator_id=operator_id)
            retired += 1
        if retired:
            await session.flush()
        return retired

    async def enqueue_derivative_cleanup(self, session, file, cleanup_id: str) -> None:
        """为一次正文撤回或发布登记可重试的文件派生对象扫描。"""
        event_key = f"document_derivatives_cleanup:{file.file_id}:{cleanup_id}"
        event = await session.scalar(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.event_key == event_key))
        if event is None:
            session.add(
                KnowledgeProjectionOutbox(
                    event_key=event_key,
                    kb_id=file.kb_id,
                    aggregate_id=file.file_id,
                    generation=int(file.generation or 0),
                    operation="document_derivatives_cleanup",
                    status="pending",
                )
            )

    async def retire_stale_media(self, session, file, *, basis_revision: str | None, operator_id: str | None = None) -> int:
        """正文变化后撤回依赖旧正文的媒体映射，独立原件媒体仍有效。"""
        conditions = [
            KnowledgeDocumentRevision.kb_id == file.kb_id,
            KnowledgeDocumentRevision.file_id == file.file_id,
            KnowledgeDocumentRevision.kind == "media",
            KnowledgeDocumentRevision.deleted_at.is_(None),
            KnowledgeDocumentRevision.basis_document_revision.is_not(None),
        ]
        if basis_revision is not None:
            conditions.append(KnowledgeDocumentRevision.basis_document_revision != basis_revision)
        rows = await session.scalars(select(KnowledgeDocumentRevision).where(*conditions).with_for_update())
        retired = 0
        for record in rows:
            await self.retire_record(session, file, record, operator_id=operator_id)
            retired += 1
        if retired:
            await session.flush()
        return retired

    async def retire_record(self, session, file, record, *, operator_id: str | None = None) -> None:
        """撤回一个映射并幂等登记其对象回收意图。"""
        if record.deleted_at is not None:
            return
        record.deleted_at, record.deleted_by = utc_now(), operator_id
        event_key = f"revision-deleted:{record.revision}"
        event = await session.scalar(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.event_key == event_key))
        if event is None:
            session.add(
                KnowledgeProjectionOutbox(
                    event_key=event_key,
                    kb_id=file.kb_id,
                    aggregate_id=record.revision,
                    generation=file.generation,
                    operation="document_revision_deleted",
                    status="pending",
                )
            )

    async def active_revisions(self, session, kb_id: str):
        """流式读取当前有效的对象映射。"""
        rows = await session.stream_scalars(
            select(KnowledgeDocumentRevision)
            .join(KnowledgeFile, KnowledgeFile.file_id == KnowledgeDocumentRevision.file_id)
            .join(KnowledgeBase, KnowledgeBase.kb_id == KnowledgeFile.kb_id)
            .where(
                KnowledgeDocumentRevision.kb_id == kb_id,
                KnowledgeDocumentRevision.deleted_at.is_(None),
                KnowledgeFile.deleted_at.is_(None),
                KnowledgeBase.deleted_at.is_(None),
            )
            .execution_options(yield_per=10)
        )
        async for record in rows:
            yield record

    async def purge_file_artifacts(self, session, file_id: str) -> None:
        """外部对象回收成功后，同事务移除该文件的映射、依据及双端关系。"""
        await session.execute(delete(KnowledgeDocumentRevision).where(KnowledgeDocumentRevision.file_id == file_id))

    async def is_registered(self, session, revision: str) -> bool:
        """失败产物回收也检查已撤回映射，不借失败任务删除已发布对象。"""
        return await session.get(KnowledgeDocumentRevision, revision) is not None

    async def register(self, session, file: KnowledgeFile, manifest: dict, *, retire_replaced: bool = True) -> KnowledgeDocumentRevision:
        """登记当前来源；发布新产物时同事务撤回替代映射。"""
        if file.deleted_at is not None or (file.kb_id, file.file_id) != (manifest["kb_id"], manifest["file_id"]):
            raise StateConflictError("文件归属已变化，不能发布来源映射", code="stale_source")
        uri = source_object_uri(file.minio_url, file.path, file.file_type)
        source_revision = original_revision(file.kb_id, file.file_id, uri) if uri else None
        if manifest["kind"] == "document":
            current = body_revision(file.kb_id, file.file_id, file.markdown_file)
            matches = file.markdown_file is not None and current == manifest["revision"]
        elif manifest["kind"] == "original":
            matches = source_revision is not None and source_revision == manifest["revision"]
        else:
            basis = manifest.get("basis_document_revision")
            matches = source_revision == manifest["source_revision"] and (
                basis is None or (file.markdown_file is not None and basis == body_revision(file.kb_id, file.file_id, file.markdown_file))
            )
        if not matches or source_revision != manifest.get("source_revision"):
            raise StateConflictError("来源读取后文件指针已变化", code="stale_source")
        existing = await session.get(KnowledgeDocumentRevision, manifest["revision"])
        if existing is not None:
            if existing.deleted_at is not None:
                raise StateConflictError("该修订已被明确清除，不能自动恢复", code="revision_deleted")
            immutable = (
                "kb_id",
                "file_id",
                "kind",
                "source_revision",
                "basis_document_revision",
                "markdown_object",
                "original_object",
                "original_hash",
                "manifest_object",
                "content_hash",
                "assets",
                "missing_assets",
            )
            if any(getattr(existing, key) != manifest.get(key) for key in immutable):
                raise StateConflictError("不可变正文对象的内容发生变化", code="immutable_source_changed")
            if retire_replaced:
                await self._retire_replaced(session, file, manifest)
            return existing
        record = KnowledgeDocumentRevision(**manifest)
        session.add(record)
        await session.flush()
        if retire_replaced:
            await self._retire_replaced(session, file, manifest, cleanup_id=manifest["revision"])
        return record

    async def _retire_replaced(self, session, file, manifest: dict, *, cleanup_id: str | None = None) -> None:
        """替代产物提交时撤回同类旧映射及旧正文依赖媒体。"""
        await self.retire_kind(session, file, manifest["kind"], keep_revision=manifest["revision"], operator_id=manifest.get("created_by"))
        if manifest["kind"] == "document":
            await self.retire_stale_media(session, file, basis_revision=manifest["revision"], operator_id=manifest.get("created_by"))
            if cleanup_id is not None:
                await self.enqueue_derivative_cleanup(session, file, cleanup_id)
