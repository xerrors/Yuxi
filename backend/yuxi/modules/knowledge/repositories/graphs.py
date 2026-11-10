from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, exists, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.file_status import FileStatus
from yuxi.modules.knowledge.models import (
    KnowledgeChunk,
    KnowledgeFile,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
    KnowledgeGraphTriple,
    KnowledgeGraphTripleMention,
)
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.projections import acquire_knowledge_projection_lock
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError


class KnowledgeGraphRepository:
    """知识图谱 PG 投影仓储：实体/三元组/提及的持久化查询与写入、向量租约与两阶段构建状态。

    Neo4j 与 Milvus 侧的图谱操作由 graphs/service.py 编排，本类只拥有 PG 投影事实。
    """

    # 向量回填达到该尝试次数后进入 failed 终态，只能经 reconcile 重新入队。
    VECTOR_MAX_ATTEMPTS = 3

    # ─── 构建状态读取与统计 ────────────────────────────────────────

    @asynccontextmanager
    async def file_graph_snapshot(self, kb_id: str, file_id: str):
        """锁定文件代次，并以同一 SQL 快照读取结构分块及实体、三元组来源。"""
        async with pg_manager.get_async_session_context() as session:
            # 与构建、清理保持投影锁在前、文件行锁在后的顺序，且不额外占用连接。
            await acquire_knowledge_projection_lock(session, kb_id, shared=True)
            file = await session.scalar(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id, KnowledgeFile.deleted_at.is_(None))
                .with_for_update(read=True)
            )
            if not file or file.is_folder:
                yield None
                return
            queries = []
            for kind, model, id_column in (
                ("entity", KnowledgeGraphEntityMention, KnowledgeGraphEntityMention.entity_id),
                ("triple", KnowledgeGraphTripleMention, KnowledgeGraphTripleMention.triple_id),
            ):
                queries.append(
                    select(literal(kind), id_column, model.chunk_id)
                    .join(KnowledgeChunk, KnowledgeChunk.chunk_id == model.chunk_id)
                    .where(model.kb_id == kb_id, model.file_id == file_id, KnowledgeChunkRepository.visible_scope())
                )
            queries.append(
                select(literal("chunk"), KnowledgeChunk.chunk_id, KnowledgeChunk.chunk_id).where(
                    KnowledgeChunk.kb_id == kb_id,
                    KnowledgeChunk.file_id == file_id,
                    KnowledgeChunk.graph_structure_indexed.is_(True),
                    KnowledgeChunkRepository.visible_scope(),
                )
            )
            entity_mentions, triple_mentions = [], []
            chunk_ids = set()
            for kind, identifier, chunk_id in await session.execute(union_all(*queries)):
                if kind == "chunk":
                    chunk_ids.add(chunk_id)
                    continue
                target = entity_mentions if kind == "entity" else triple_mentions
                target.append({"id": identifier, "chunk_id": chunk_id})
                chunk_ids.add(chunk_id)
            yield entity_mentions, triple_mentions, list(chunk_ids)

    @asynccontextmanager
    async def file_build_scope(self, kb_id: str, file_id: str, generation: int):
        """持有投影锁和文件读锁，防止构建期间文件代次切换。"""
        async with pg_manager.get_async_session_context() as session:
            await acquire_knowledge_projection_lock(session, kb_id, shared=True)
            file = await session.scalar(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id, KnowledgeFile.deleted_at.is_(None))
                .with_for_update(read=True)
            )
            if file is None or file.is_folder or file.active_generation != generation:
                raise ValueError("文件不存在、不可构建或文件代次已改变")
            count = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.kb_id == kb_id, KnowledgeChunk.file_id == file_id, KnowledgeChunk.generation == generation)
            )
            if not count:
                raise ValueError("文件当前没有可构建的分块")
            yield

    @asynccontextmanager
    async def file_clear_scope(self, kb_id: str, file_id: str, generation: int):
        """清理独占投影并锁住目标文件，拒绝排队期间发生的删除和代次替换。"""
        async with pg_manager.get_async_session_context() as session:
            await acquire_knowledge_projection_lock(session, kb_id)
            file = await session.scalar(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id, KnowledgeFile.deleted_at.is_(None))
                .with_for_update(read=True)
            )
            if file is None:
                raise ResourceNotFoundError("文件不存在或不属于该知识库")
            self.require_clearable_file(file)
            if file.active_generation != generation:
                raise StateConflictError("文件代次已改变，请刷新后重新清除图谱")
            yield

    @staticmethod
    def require_clearable_file(file) -> None:
        """文件图谱撤销只接受有稳定正文的非目录文件。"""
        if file.is_folder:
            raise InvalidOperationError("目录不能清除图谱")
        # failed 是历史入库失败值，与 error_indexing 一样可以清掉已有图谱。
        if file.status not in {FileStatus.PARSED, FileStatus.INDEXED, FileStatus.ERROR_INDEXING, "done", "failed"}:
            raise StateConflictError("文件当前状态不能清除图谱，请等待处理完成")

    @staticmethod
    def _vector_file_scope(model, file_id: str | None):
        """按当前文件分块的提及定位共享实体或三元组。"""
        if file_id is None:
            return True
        mention = KnowledgeGraphEntityMention if model is KnowledgeGraphEntity else KnowledgeGraphTripleMention
        record_id = model.entity_id if model is KnowledgeGraphEntity else model.triple_id
        mention_id = mention.entity_id if model is KnowledgeGraphEntity else mention.triple_id
        return exists().where(
            mention_id == record_id,
            mention.file_id == file_id,
            KnowledgeChunk.chunk_id == mention.chunk_id,
            KnowledgeChunkRepository.visible_scope(),
        )

    async def visible_graph_ids(self, kb_id: str, chunk_ids: list[str], entity_ids: list[str]) -> tuple[set[str], set[str]]:
        """图谱响应仅保留仍有 active 文件依据的节点和边。"""
        chunk_query = select(KnowledgeChunk.chunk_id).where(
            KnowledgeChunk.kb_id == kb_id,
            KnowledgeChunk.chunk_id.in_(chunk_ids),
            KnowledgeChunkRepository.visible_scope(),
        )
        # 实体在任一可见 chunk 中被提及即视为可见。
        entity_query = (
            select(KnowledgeGraphEntityMention.entity_id)
            .join(KnowledgeChunk, KnowledgeChunk.chunk_id == KnowledgeGraphEntityMention.chunk_id)
            .where(
                KnowledgeChunk.kb_id == kb_id,
                KnowledgeGraphEntityMention.entity_id.in_(entity_ids),
                KnowledgeChunkRepository.visible_scope(),
            )
            .distinct()
        )
        async with pg_manager.get_async_session_context() as session:
            # 入参为空时跳过查询，避免发出空 IN 条件的 SQL。
            chunks = set(await session.scalars(chunk_query)) if chunk_ids else set()
            entities = set(await session.scalars(entity_query)) if entity_ids else set()
            return chunks, entities

    async def count_by_kb_id(self, kb_id: str) -> tuple[int, int]:
        """返回知识库的实体与三元组总量。"""
        async with pg_manager.get_async_session_context() as session:
            entity_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id)
            )
            triple_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id)
            )
            return int(entity_count or 0), int(triple_count or 0)

    async def count_vector_statuses_by_kb_id(self, kb_id: str, *, file_id: str | None = None) -> dict[str, int]:
        """统计实体与三元组的向量状态计数，键覆盖全部已知状态。"""
        # 预置四个已知状态键让调用方可直接索引；未知状态经 get 容错累计，不因缺键失败。
        counts = {"pending": 0, "processing": 0, "indexed": 0, "failed": 0}
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeGraphEntity, KnowledgeGraphTriple):
                rows = (
                    await session.execute(
                        select(model.vector_status, func.count())
                        .where(model.kb_id == kb_id, self._vector_file_scope(model, file_id))
                        .group_by(model.vector_status)
                    )
                ).all()
                for status, count in rows:
                    counts[str(status)] = counts.get(str(status), 0) + int(count or 0)
        return counts

    # ─── 投影写入：结构 upsert → 向量认领回填 → 两阶段收尾 ────────

    async def upsert_chunk_graph(
        self,
        *,
        kb_id: str,
        file_id: str,
        chunk_id: str,
        entities: list[dict[str, Any]],
        triples: list[dict[str, Any]],
    ) -> None:
        """写入单个 chunk 抽取出的实体、三元组及其提及投影。"""
        async with pg_manager.get_async_session_context() as session:
            if entities:
                # content 只是向量载荷输入，实体表没有该列。
                entity_rows = [{key: value for key, value in entity.items() if key != "content"} for entity in entities]
                entity_stmt = insert(KnowledgeGraphEntity).values(entity_rows)
                # 冲突只刷新语义字段，不触碰向量租约列：重复抽取不得重置向量进度。
                await session.execute(
                    entity_stmt.on_conflict_do_update(
                        index_elements=["entity_id"],
                        set_={
                            "name": entity_stmt.excluded.name,
                            "attributes": entity_stmt.excluded.attributes,
                            "updated_at": func.now(),
                        },
                    )
                )
                # 提及幂等插入，重跑同一 chunk 不产生重复关联。
                await session.execute(
                    insert(KnowledgeGraphEntityMention)
                    .values(
                        [
                            {
                                "entity_id": entity["entity_id"],
                                "kb_id": kb_id,
                                "file_id": file_id,
                                "chunk_id": chunk_id,
                            }
                            for entity in entities
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["entity_id", "chunk_id"])
                )

            if triples:
                # text/extractor_type 是提及级（按 chunk）属性，不落在三元组主表。
                triple_rows = [{key: value for key, value in triple.items() if key not in {"text", "extractor_type"}} for triple in triples]
                triple_stmt = insert(KnowledgeGraphTriple).values(triple_rows)
                await session.execute(
                    triple_stmt.on_conflict_do_update(
                        index_elements=["triple_id"],
                        set_={
                            "content": triple_stmt.excluded.content,
                            "relation_type": triple_stmt.excluded.relation_type,
                            "updated_at": func.now(),
                        },
                    )
                )
                await session.execute(
                    insert(KnowledgeGraphTripleMention)
                    .values(
                        [
                            {
                                "triple_id": triple["triple_id"],
                                "kb_id": kb_id,
                                "file_id": file_id,
                                "chunk_id": chunk_id,
                                "text": triple.get("text"),
                                "extractor_type": triple.get("extractor_type"),
                            }
                            for triple in triples
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["triple_id", "chunk_id"])
                )

    async def claim_vector_records(
        self,
        *,
        kb_id: str,
        record_type: str,
        limit: int,
        lease_seconds: int,
        file_id: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """按租约认领一批待向量化记录，返回锁 token 与向量化载荷。"""
        model, id_field = self._vector_model(record_type)
        now = datetime.now(UTC)
        # 可认领：退避已到期的 pending，或租约过期的 processing（前任崩溃或超时让出）。
        claimable = or_(
            ((model.vector_status == "pending") & or_(model.vector_next_retry_at.is_(None), model.vector_next_retry_at <= now)),
            ((model.vector_status == "processing") & (model.vector_locked_until < now)),
        )
        token = uuid.uuid4().hex
        async with pg_manager.get_async_session_context() as session:
            # FOR UPDATE SKIP LOCKED 让并发构建认领互不重叠的批次；token 保证只有本次持有者能回写状态。
            records = list(
                (
                    await session.execute(
                        select(model)
                        .where(model.kb_id == kb_id, claimable, self._vector_file_scope(model, file_id))
                        .order_by(model.id.asc())
                        .limit(max(limit, 1))
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            # 尝试次数在认领时递增，失败回写据此决定退避与终态。
            for record in records:
                record.vector_status = "processing"
                record.vector_attempt_count = int(record.vector_attempt_count or 0) + 1
                record.vector_locked_until = now + timedelta(seconds=lease_seconds)
                record.vector_lock_token = token
                record.vector_last_error = None
            payloads = [self._vector_payload(record_type, record, id_field) for record in records]
        return token, payloads

    async def mark_vector_records_indexed(
        self,
        *,
        record_type: str,
        record_ids: list[str],
        lock_token: str,
    ) -> None:
        """向量写入成功：置为 indexed 并释放租约。"""
        if not record_ids:
            return
        model, id_field = self._vector_model(record_type)
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(model)
                .where(id_field.in_(record_ids), model.vector_lock_token == lock_token)
                .values(
                    vector_status="indexed",
                    vector_last_error=None,
                    vector_next_retry_at=None,
                    vector_locked_until=None,
                    vector_lock_token=None,
                )
            )

    async def mark_vector_records_failed(
        self,
        *,
        record_type: str,
        record_ids: list[str],
        lock_token: str,
        error: str,
    ) -> None:
        """向量写入失败：未达最大尝试次数回到 pending 并按次数退避，达到后进入 failed 终态。"""
        if not record_ids:
            return
        model, id_field = self._vector_model(record_type)
        now = datetime.now(UTC)
        async with pg_manager.get_async_session_context() as session:
            # 第 1、2 次失败分别退避 5s/30s；认领时已递增的次数达到上限则不再回到 pending。
            await session.execute(
                update(model)
                .where(id_field.in_(record_ids), model.vector_lock_token == lock_token)
                .values(
                    vector_status=case(
                        (model.vector_attempt_count >= self.VECTOR_MAX_ATTEMPTS, "failed"),
                        else_="pending",
                    ),
                    vector_last_error=error[:4000],
                    vector_next_retry_at=case(
                        (model.vector_attempt_count == 1, now + timedelta(seconds=5)),
                        (model.vector_attempt_count == 2, now + timedelta(seconds=30)),
                        else_=None,
                    ),
                    vector_locked_until=None,
                    vector_lock_token=None,
                )
            )

    async def finalize_graph_indexed_chunks(self, kb_id: str, *, file_id: str | None = None) -> int:
        """把结构已写入且关联向量全部 indexed 的 chunk 推进为 graph_indexed。"""
        # 收尾条件经提及表关联：任一提及该 chunk 的实体或三元组仍未 indexed，chunk 就不能进入终态。
        entity_not_indexed = exists().where(
            KnowledgeGraphEntityMention.chunk_id == KnowledgeChunk.chunk_id,
            KnowledgeGraphEntity.entity_id == KnowledgeGraphEntityMention.entity_id,
            KnowledgeGraphEntity.vector_status != "indexed",
        )
        triple_not_indexed = exists().where(
            KnowledgeGraphTripleMention.chunk_id == KnowledgeChunk.chunk_id,
            KnowledgeGraphTriple.triple_id == KnowledgeGraphTripleMention.triple_id,
            KnowledgeGraphTriple.vector_status != "indexed",
        )
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(KnowledgeChunk)
                .where(
                    KnowledgeChunk.kb_id == kb_id,
                    KnowledgeChunkRepository.visible_scope(),
                    *([KnowledgeChunk.file_id == file_id] if file_id is not None else []),
                    KnowledgeChunk.graph_structure_indexed.is_(True),
                    KnowledgeChunk.graph_indexed.is_not(True),
                    ~entity_not_indexed,
                    ~triple_not_indexed,
                )
                .values(graph_indexed=True)
            )
            return int(result.rowcount or 0)

    async def retry_failed_file_vectors(self, kb_id: str, file_id: str) -> int:
        """重试当前文件提及的失败向量，共享记录也按文件来源纳入。"""
        total = 0
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeGraphEntity, KnowledgeGraphTriple):
                result = await session.execute(
                    update(model)
                    .where(model.kb_id == kb_id, model.vector_status == "failed", self._vector_file_scope(model, file_id))
                    .values(
                        vector_status="pending",
                        vector_attempt_count=0,
                        vector_last_error=None,
                        vector_next_retry_at=None,
                        vector_locked_until=None,
                        vector_lock_token=None,
                    )
                )
                total += int(result.rowcount or 0)
        return total

    # ─── 清理：向量对账重置与文件/知识库级删除 ─────────────────────

    async def reconcile_vector_records(self, kb_id: str, *, all_vectors: bool) -> int:
        """重置向量记录等待重新索引；all_vectors 时同时撤回 chunk 的 graph_indexed。"""
        now = datetime.now(UTC)
        total = 0
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeGraphEntity, KnowledgeGraphTriple):
                condition = model.kb_id == kb_id
                if not all_vectors:
                    # 只重排 failed 与租约过期的 processing，不打断在途批次。
                    condition = condition & or_(
                        model.vector_status == "failed",
                        ((model.vector_status == "processing") & (model.vector_locked_until < now)),
                    )
                result = await session.execute(
                    update(model)
                    .where(condition)
                    .values(
                        vector_status="pending",
                        vector_attempt_count=0,
                        vector_last_error=None,
                        vector_next_retry_at=None,
                        vector_locked_until=None,
                        vector_lock_token=None,
                    )
                )
                total += int(result.rowcount or 0)
            # 撤回第二阶段标记让构建重新收尾；graph_structure_indexed 保留，抽取与结构写入不重跑。
            if all_vectors:
                await session.execute(update(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id).values(graph_indexed=False))
        return total

    async def file_entity_sources(self, file_id: str, *, generation: int | None, additional_entity_ids: list[str]) -> list[dict[str, Any]]:
        """为受影响共享实体选取仍可见的提及来源，禁止复用被撤销文件覆盖的聚合属性。"""
        removed_chunks = select(KnowledgeChunk.chunk_id).where(KnowledgeChunk.file_id == file_id)
        if generation is not None:
            removed_chunks = removed_chunks.where(KnowledgeChunk.generation == generation)
        affected = select(KnowledgeGraphEntityMention.entity_id).where(KnowledgeGraphEntityMention.chunk_id.in_(removed_chunks))
        async with pg_manager.get_async_session_context() as session:
            rows = await session.execute(
                select(
                    KnowledgeGraphEntity.entity_id,
                    KnowledgeGraphEntity.normalized_name,
                    KnowledgeGraphEntityMention.chunk_id,
                    KnowledgeChunk.extraction_result,
                )
                .join(KnowledgeGraphEntityMention, KnowledgeGraphEntityMention.entity_id == KnowledgeGraphEntity.entity_id)
                .join(KnowledgeChunk, KnowledgeChunk.chunk_id == KnowledgeGraphEntityMention.chunk_id)
                .where(
                    or_(KnowledgeGraphEntity.entity_id.in_(affected), KnowledgeGraphEntity.entity_id.in_(additional_entity_ids)),
                    ~KnowledgeChunk.chunk_id.in_(removed_chunks),
                    KnowledgeChunkRepository.visible_scope(),
                )
                .order_by(KnowledgeGraphEntity.entity_id, KnowledgeChunk.chunk_id)
            )
            return [dict(row._mapping) for row in rows]

    async def delete_orphan_records(self, *, entity_ids: list[str], triple_ids: list[str]) -> None:
        """先提交历史无引用记录的撤销，Neo4j 保留文件归属直到外部清理成功。"""
        async with pg_manager.get_async_session_context() as session:
            await self._delete_orphan_records(session, entity_ids=entity_ids, triple_ids=triple_ids)

    @asynccontextmanager
    async def delete_file_references(
        self,
        file_id: str,
        *,
        generation: int | None = None,
        clear_extraction_result: bool = False,
        restored_entities: list[dict] | None = None,
        additional_entity_ids: list[str] | None = None,
        additional_triple_ids: list[str] | None = None,
    ) -> AsyncIterator[tuple[list[str], list[str], list[str]]]:
        """清理指定代次关联；外部删除失败时回滚，保留重试依据。"""
        entity_scope = KnowledgeGraphEntityMention.file_id == file_id
        triple_scope = KnowledgeGraphTripleMention.file_id == file_id
        chunk_query = select(KnowledgeChunk.chunk_id).where(KnowledgeChunk.file_id == file_id)
        if generation is not None:
            # 指定代次时，提及清理范围与返回给调用方的 chunk 清单都按该代次收窄。
            chunk_query = chunk_query.where(KnowledgeChunk.generation == generation)
            entity_scope &= KnowledgeGraphEntityMention.chunk_id.in_(chunk_query)
            triple_scope &= KnowledgeGraphTripleMention.chunk_id.in_(chunk_query)
        async with pg_manager.get_async_session_context() as session:
            chunk_ids = list((await session.execute(chunk_query)).scalars())
            affected_entity_ids = list(
                (await session.execute(select(KnowledgeGraphEntityMention.entity_id).where(entity_scope).distinct())).scalars().all()
            )
            affected_triple_ids = list(
                (await session.execute(select(KnowledgeGraphTripleMention.triple_id).where(triple_scope).distinct())).scalars().all()
            )

            # 历史上 Chunk 可能先被删除，Neo4j 文件来源补足已被级联删除的提及归属。
            affected_entity_ids = list(set(affected_entity_ids) | set(additional_entity_ids or []))
            affected_triple_ids = list(set(affected_triple_ids) | set(additional_triple_ids or []))

            # 先删提及再在同一事务内判定孤儿；孤儿三元组先于孤儿实体删除，
            # 否则刚被清空的边会把本应删除的实体继续钉在图里。
            await session.execute(delete(KnowledgeGraphTripleMention).where(triple_scope))
            await session.execute(delete(KnowledgeGraphEntityMention).where(entity_scope))

            orphan_entity_ids, orphan_triple_ids = await self._delete_orphan_records(
                session, entity_ids=affected_entity_ids, triple_ids=affected_triple_ids
            )

            # 事务跨 yield 保持打开：外部 Neo4j/Milvus 清理失败时异常抛回本上下文，PG 删除整体回滚。
            yield orphan_entity_ids, orphan_triple_ids, chunk_ids
            for entity in restored_entities or []:
                await session.execute(
                    update(KnowledgeGraphEntity)
                    .where(KnowledgeGraphEntity.entity_id == entity["entity_id"])
                    .values(name=entity["name"], attributes=entity["attributes"])
                )
            if clear_extraction_result:
                await session.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.file_id == file_id)
                    .values(
                        graph_structure_indexed=False,
                        graph_indexed=False,
                        extraction_result=None,
                        graph_extraction_details={"status": "pending", "attempt_count": 0},
                        ent_ids=None,
                        tags=None,
                    )
                )

    async def delete_by_kb_id(self, kb_id: str) -> None:
        """删除知识库全部图谱投影；先删提及和三元组再删实体，满足外键约束。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id))

    # ─── 内部辅助 ──────────────────────────────────────────────────

    @staticmethod
    async def _delete_orphan_records(session, *, entity_ids: list[str], triple_ids: list[str]) -> tuple[list[str], list[str]]:
        """仅回收候选中的无来源三元组，再按提及和剩余关系端点判定实体孤儿。"""
        orphan_triple_ids = []
        if triple_ids:
            has_mentions = exists().where(KnowledgeGraphTripleMention.triple_id == KnowledgeGraphTriple.triple_id)
            retained = set(
                await session.scalars(
                    select(KnowledgeGraphTriple.triple_id).where(KnowledgeGraphTriple.triple_id.in_(triple_ids), has_mentions)
                )
            )
            # PG 已不存在的来源候选仍需清理外部向量，不能因先前重试已删行而漏掉。
            orphan_triple_ids = sorted(set(triple_ids) - retained)
            if orphan_triple_ids:
                await session.execute(delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.triple_id.in_(orphan_triple_ids)))
        orphan_entity_ids = []
        if entity_ids:
            has_mentions = exists().where(KnowledgeGraphEntityMention.entity_id == KnowledgeGraphEntity.entity_id)
            has_triples = exists().where(
                or_(
                    KnowledgeGraphTriple.source_entity_id == KnowledgeGraphEntity.entity_id,
                    KnowledgeGraphTriple.target_entity_id == KnowledgeGraphEntity.entity_id,
                )
            )
            retained = set(
                await session.scalars(
                    select(KnowledgeGraphEntity.entity_id).where(
                        KnowledgeGraphEntity.entity_id.in_(entity_ids), or_(has_mentions, has_triples)
                    )
                )
            )
            orphan_entity_ids = sorted(set(entity_ids) - retained)
            if orphan_entity_ids:
                await session.execute(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.entity_id.in_(orphan_entity_ids)))
        return orphan_entity_ids, orphan_triple_ids

    @staticmethod
    def _vector_model(record_type: str):
        """把向量记录类型映射到投影模型与主键列；未知类型显式拒绝。"""
        if record_type == "entity":
            return KnowledgeGraphEntity, KnowledgeGraphEntity.entity_id
        if record_type == "triple":
            return KnowledgeGraphTriple, KnowledgeGraphTriple.triple_id
        raise ValueError(f"Unsupported graph vector record type: {record_type}")

    @staticmethod
    def _vector_payload(record_type: str, record, id_field) -> dict[str, Any]:
        """投影为向量载荷：实体取规范化名，三元组取 content 并附带端点 ID。"""
        payload = {
            "id": getattr(record, id_field.key),
            "content": record.normalized_name if record_type == "entity" else record.content,
        }
        if record_type == "triple":
            payload.update({"source_id": record.source_entity_id, "target_id": record.target_entity_id})
        return payload
