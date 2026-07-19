from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeChunk,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
    KnowledgeGraphTriple,
    KnowledgeGraphTripleMention,
)


class KnowledgeGraphRepository:
    VECTOR_MAX_ATTEMPTS = 3

    async def count_by_kb_id(self, kb_id: str) -> tuple[int, int]:
        async with pg_manager.get_async_session_context() as session:
            entity_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id)
            )
            triple_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id)
            )
            return int(entity_count or 0), int(triple_count or 0)

    async def count_vector_statuses_by_kb_id(self, kb_id: str) -> dict[str, int]:
        counts = {"pending": 0, "processing": 0, "indexed": 0, "failed": 0}
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeGraphEntity, KnowledgeGraphTriple):
                rows = (
                    await session.execute(
                        select(model.vector_status, func.count())
                        .where(model.kb_id == kb_id)
                        .group_by(model.vector_status)
                    )
                ).all()
                for status, count in rows:
                    counts[str(status)] = counts.get(str(status), 0) + int(count or 0)
        return counts

    async def claim_vector_records(
        self,
        *,
        kb_id: str,
        record_type: str,
        limit: int,
        lease_seconds: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        model, id_field = self._vector_model(record_type)
        now = datetime.now(UTC)
        claimable = or_(
            (
                (model.vector_status == "pending")
                & or_(model.vector_next_retry_at.is_(None), model.vector_next_retry_at <= now)
            ),
            ((model.vector_status == "processing") & (model.vector_locked_until < now)),
        )
        token = uuid.uuid4().hex
        async with pg_manager.get_async_session_context() as session:
            records = list(
                (
                    await session.execute(
                        select(model)
                        .where(model.kb_id == kb_id, claimable)
                        .order_by(model.id.asc())
                        .limit(max(limit, 1))
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
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
        if not record_ids:
            return
        model, id_field = self._vector_model(record_type)
        now = datetime.now(UTC)
        async with pg_manager.get_async_session_context() as session:
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

    async def finalize_graph_indexed_chunks(self, kb_id: str) -> int:
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
                    KnowledgeChunk.graph_structure_indexed.is_(True),
                    KnowledgeChunk.graph_indexed.is_not(True),
                    ~entity_not_indexed,
                    ~triple_not_indexed,
                )
                .values(graph_indexed=True)
            )
            return int(result.rowcount or 0)

    async def reconcile_vector_records(self, kb_id: str, *, all_vectors: bool) -> int:
        now = datetime.now(UTC)
        total = 0
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeGraphEntity, KnowledgeGraphTriple):
                condition = model.kb_id == kb_id
                if not all_vectors:
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
            if all_vectors:
                await session.execute(
                    update(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id).values(graph_indexed=False)
                )
        return total

    @staticmethod
    def _vector_model(record_type: str):
        if record_type == "entity":
            return KnowledgeGraphEntity, KnowledgeGraphEntity.entity_id
        if record_type == "triple":
            return KnowledgeGraphTriple, KnowledgeGraphTriple.triple_id
        raise ValueError(f"Unsupported graph vector record type: {record_type}")

    @staticmethod
    def _vector_payload(record_type: str, record, id_field) -> dict[str, Any]:
        payload = {
            "id": getattr(record, id_field.key),
            "content": record.normalized_name if record_type == "entity" else record.content,
        }
        if record_type == "triple":
            payload.update({"source_id": record.source_entity_id, "target_id": record.target_entity_id})
        return payload

    async def upsert_chunk_graph(
        self,
        *,
        kb_id: str,
        file_id: str,
        chunk_id: str,
        entities: list[dict[str, Any]],
        triples: list[dict[str, Any]],
    ) -> None:
        async with pg_manager.get_async_session_context() as session:
            if entities:
                entity_rows = [{key: value for key, value in entity.items() if key != "content"} for entity in entities]
                entity_stmt = insert(KnowledgeGraphEntity).values(entity_rows)
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
                triple_rows = [
                    {key: value for key, value in triple.items() if key not in {"text", "extractor_type"}}
                    for triple in triples
                ]
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

    async def delete_file_references(self, file_id: str) -> tuple[list[str], list[str]]:
        async with pg_manager.get_async_session_context() as session:
            affected_entity_ids = list(
                (
                    await session.execute(
                        select(KnowledgeGraphEntityMention.entity_id)
                        .where(KnowledgeGraphEntityMention.file_id == file_id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            affected_triple_ids = list(
                (
                    await session.execute(
                        select(KnowledgeGraphTripleMention.triple_id)
                        .where(KnowledgeGraphTripleMention.file_id == file_id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )

            await session.execute(
                delete(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.file_id == file_id)
            )
            await session.execute(
                delete(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.file_id == file_id)
            )

            orphan_triple_ids: list[str] = []
            if affected_triple_ids:
                triple_has_mentions = exists().where(
                    KnowledgeGraphTripleMention.triple_id == KnowledgeGraphTriple.triple_id
                )
                orphan_triple_ids = list(
                    (
                        await session.execute(
                            select(KnowledgeGraphTriple.triple_id).where(
                                KnowledgeGraphTriple.triple_id.in_(affected_triple_ids),
                                ~triple_has_mentions,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if orphan_triple_ids:
                    await session.execute(
                        delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.triple_id.in_(orphan_triple_ids))
                    )

            orphan_entity_ids: list[str] = []
            if affected_entity_ids:
                entity_has_mentions = exists().where(
                    KnowledgeGraphEntityMention.entity_id == KnowledgeGraphEntity.entity_id
                )
                entity_has_triples = exists().where(
                    or_(
                        KnowledgeGraphTriple.source_entity_id == KnowledgeGraphEntity.entity_id,
                        KnowledgeGraphTriple.target_entity_id == KnowledgeGraphEntity.entity_id,
                    )
                )
                orphan_entity_ids = list(
                    (
                        await session.execute(
                            select(KnowledgeGraphEntity.entity_id).where(
                                KnowledgeGraphEntity.entity_id.in_(affected_entity_ids),
                                ~entity_has_mentions,
                                ~entity_has_triples,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if orphan_entity_ids:
                    await session.execute(
                        delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.entity_id.in_(orphan_entity_ids))
                    )

            return orphan_entity_ids, orphan_triple_ids

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id))
