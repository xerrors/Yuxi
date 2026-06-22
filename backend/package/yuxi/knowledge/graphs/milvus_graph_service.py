from __future__ import annotations

import asyncio
import json
import weakref
from typing import Any

from yuxi.knowledge.graphs.extractors import GraphExtractor, GraphExtractorFactory, normalize_extraction_result
from yuxi.knowledge.graphs.graph_utils import (
    build_graph_payload,
    compute_entity_id,
    compute_triple_id,
    cypher_merge_chunk,
    cypher_merge_entity_mention,
    cypher_merge_relation,
    normalize_entity_name,
)
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_graph_repository import KnowledgeGraphRepository
from yuxi.storage.neo4j import (
    Neo4jConnectionManager,
    get_shared_neo4j_connection,
    neo4j_read,
    neo4j_write,
    safe_neo4j_label,
)
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat

GRAPH_CONFIG_KEY = "graph_build_config"
GRAPH_TASK_TYPE = "knowledge_graph_index"
NEO4J_QUERY_OFFLOAD_LIMIT = 8
_neo4j_query_offload_semaphore_refs: dict[
    int,
    tuple[weakref.ReferenceType[asyncio.AbstractEventLoop], weakref.ReferenceType[asyncio.Semaphore]],
] = {}


def _get_neo4j_query_offload_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    entry = _neo4j_query_offload_semaphore_refs.get(loop_id)
    if entry is not None:
        loop_ref, semaphore_ref = entry
        semaphore = semaphore_ref()
        if loop_ref() is loop and semaphore is not None:
            return semaphore

    semaphore = asyncio.Semaphore(NEO4J_QUERY_OFFLOAD_LIMIT)

    def cleanup(ref, stale_loop_id=loop_id):
        current_entry = _neo4j_query_offload_semaphore_refs.get(stale_loop_id)
        if current_entry is not None and current_entry[1] is ref:
            _neo4j_query_offload_semaphore_refs.pop(stale_loop_id, None)

    _neo4j_query_offload_semaphore_refs[loop_id] = (weakref.ref(loop), weakref.ref(semaphore, cleanup))
    return semaphore


async def _run_neo4j_query_io(func, /, *args, **kwargs):
    semaphore = _get_neo4j_query_offload_semaphore()
    await semaphore.acquire()
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))

    def release_capacity(completed_task: asyncio.Task):
        semaphore.release()
        if completed_task.cancelled():
            return
        completed_task.exception()

    task.add_done_callback(release_capacity)
    return await asyncio.shield(task)


class MilvusGraphService:
    def __init__(
        self,
        *,
        kb_id: str | None = None,
        kb_repo: KnowledgeBaseRepository | None = None,
        chunk_repo: KnowledgeChunkRepository | None = None,
        graph_repo: KnowledgeGraphRepository | None = None,
        graph_vector_store: MilvusGraphVectorStore | None = None,
        neo4j_connection: Neo4jConnectionManager | None = None,
    ):
        self.kb_id = kb_id
        self.kb_repo = kb_repo or KnowledgeBaseRepository()
        self.chunk_repo = chunk_repo or KnowledgeChunkRepository()
        self.graph_repo = graph_repo or KnowledgeGraphRepository()
        self._graph_vector_store = graph_vector_store
        self._connection = neo4j_connection

    @property
    def connection(self) -> Neo4jConnectionManager:
        if self._connection is None:
            self._connection = get_shared_neo4j_connection()
        return self._connection

    @property
    def graph_vector_store(self) -> MilvusGraphVectorStore:
        if self._graph_vector_store is None:
            self._graph_vector_store = MilvusGraphVectorStore()
        return self._graph_vector_store

    @property
    def driver(self):
        return self.connection.driver

    async def get_status(self, kb_id: str, *, tasker: Any = None) -> dict[str, Any]:
        kb = await self._get_milvus_kb(kb_id)
        params = dict(kb.additional_params or {})
        config = params.get(GRAPH_CONFIG_KEY) or {}
        total_chunks, pending_chunks, indexed_chunks, graph_counts = await asyncio.gather(
            self.chunk_repo.count_by_kb_id(kb_id),
            self.chunk_repo.count_graph_pending_by_kb_id(kb_id),
            self.chunk_repo.count_graph_indexed_by_kb_id(kb_id),
            self.graph_repo.count_by_kb_id(kb_id),
        )
        entity_count, relationship_count = graph_counts

        build_task_status = None
        build_task_progress = 0
        if tasker is not None:
            active_task = await tasker.find_task_by_payload(
                task_type=GRAPH_TASK_TYPE,
                payload_match={"kb_id": kb_id},
                statuses={"pending", "running"},
            )
            if active_task:
                build_task_status = active_task.status
                build_task_progress = round(active_task.progress)
            else:
                failed_task = await tasker.find_task_by_payload(
                    task_type=GRAPH_TASK_TYPE,
                    payload_match={"kb_id": kb_id},
                    statuses={"failed", "cancelled"},
                )
                if failed_task:
                    build_task_status = "failed"
                    build_task_progress = 0

        return {
            "kb_id": kb_id,
            "kb_type": kb.kb_type,
            "configured": bool(config),
            "locked": bool(config.get("locked")),
            "config": self._public_config(config),
            "total_chunks": total_chunks,
            "pending_chunks": pending_chunks,
            "indexed_chunks": indexed_chunks,
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "build_task_status": build_task_status,
            "build_task_progress": build_task_progress,
        }

    async def configure(
        self,
        kb_id: str,
        extractor_type: str,
        extractor_options: dict[str, Any],
        created_by: str,
    ) -> dict:
        kb = await self._get_milvus_kb(kb_id)
        additional_params = dict(kb.additional_params or {})
        existing_config = additional_params.get(GRAPH_CONFIG_KEY) or {}
        normalized_extractor_type = (extractor_type or "").lower()
        if existing_config.get("locked"):
            existing_extractor_type = (existing_config.get("extractor_type") or "").lower()
            if normalized_extractor_type != existing_extractor_type:
                raise ValueError("图谱抽取器类型已锁定，只能修改模型、Schema 等抽取参数")

        extractor_options = extractor_options or {}
        if normalized_extractor_type == "llm" and extractor_options.get("prompt"):
            raise ValueError("LLM 图谱抽取器不支持自定义完整 Prompt，请使用 schema 配置抽取约束")
        GraphExtractorFactory.create(normalized_extractor_type, extractor_options)
        config = {
            "locked": True,
            "extractor_type": normalized_extractor_type,
            "extractor_options": extractor_options or {},
            "created_at": existing_config.get("created_at") or utc_isoformat(),
            "created_by": existing_config.get("created_by") or created_by,
        }
        if existing_config.get("locked"):
            config["updated_at"] = utc_isoformat()
            config["updated_by"] = created_by
        additional_params[GRAPH_CONFIG_KEY] = config
        await self.kb_repo.update(kb_id, {"additional_params": additional_params})
        return config

    async def build_pending_chunks(self, kb_id: str, *, batch_size: int, context=None) -> dict[str, Any]:
        kb = await self._get_milvus_kb(kb_id)
        config = self._get_locked_config(kb.additional_params or {})
        extractor_options = self._runtime_extractor_options(config)
        extractor = GraphExtractorFactory.create(config["extractor_type"], extractor_options)
        worker_count = self._get_worker_count(config)
        total_pending = await self.chunk_repo.count_graph_pending_by_kb_id(kb_id)
        processed = 0
        failed = 0
        failed_chunk_ids: set[str] = set()
        write_lock = asyncio.Lock()

        while True:
            if context is not None:
                await context.raise_if_cancelled()
            chunks = await self.chunk_repo.list_graph_pending_by_kb_id(kb_id, batch_size)
            unprocessed = [c for c in chunks if c.chunk_id not in failed_chunk_ids]
            if not unprocessed:
                break

            queue: asyncio.Queue[Any] = asyncio.Queue()
            for chunk in unprocessed:
                queue.put_nowait(chunk)

            async def worker() -> None:
                nonlocal processed, failed
                while True:
                    if context is not None:
                        await context.raise_if_cancelled()
                    try:
                        chunk = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        extraction_result = await self._get_chunk_extraction_result(kb_id, chunk, extractor)
                        async with write_lock:
                            entities, triples = await asyncio.to_thread(
                                self.write_chunk_graph,
                                kb_id,
                                chunk,
                                extraction_result,
                            )
                            await self.graph_repo.upsert_chunk_graph(
                                kb_id=kb_id,
                                file_id=chunk.file_id,
                                chunk_id=chunk.chunk_id,
                                entities=entities,
                                triples=triples,
                            )
                            await self.graph_vector_store.insert_missing_graph_records(
                                kb_id=kb_id,
                                embedding_model_spec=kb.embedding_model_spec,
                                entities=entities,
                                triples=triples,
                            )
                            await self.chunk_repo.mark_graph_indexed(
                                chunk.chunk_id,
                                ent_ids=[entity["entity_id"] for entity in entities],
                            )
                        processed += 1
                    except Exception as exc:
                        logger.error(f"Chunk 图谱构建失败 chunk_id={chunk.chunk_id}: {exc}")
                        failed_chunk_ids.add(chunk.chunk_id)
                        failed += 1
                    finally:
                        queue.task_done()

                    if context is not None:
                        completed = processed + failed
                        progress = 5.0 + min(90.0, completed / max(total_pending, 1) * 90.0)
                        await context.set_progress(progress, f"图谱构建 {completed}/{total_pending}，失败 {failed}")

            workers = [asyncio.create_task(worker()) for _ in range(min(worker_count, len(unprocessed)))]
            try:
                await asyncio.gather(*workers)
            except Exception:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise

        remaining = await self.chunk_repo.count_graph_pending_by_kb_id(kb_id)
        return {"kb_id": kb_id, "success": processed, "failed": failed, "remaining": remaining}

    @staticmethod
    def _get_worker_count(config: dict[str, Any]) -> int:
        if (config.get("extractor_type") or "").lower() != "llm":
            return 1
        try:
            worker_count = int((config.get("extractor_options") or {}).get("concurrency_count") or 1)
        except (TypeError, ValueError):
            return 1
        return max(1, min(worker_count, 1000))

    @staticmethod
    def _runtime_extractor_options(config: dict[str, Any]) -> dict[str, Any]:
        options = dict(config.get("extractor_options") or {})
        options.pop("prompt", None)
        return options

    async def _get_chunk_extraction_result(self, kb_id: str, chunk, extractor: GraphExtractor) -> dict[str, Any]:
        extractor_type = extractor.extractor_type
        if chunk.extraction_result:
            return normalize_extraction_result(chunk.extraction_result, extractor_type)

        extraction_result = await extractor.extract(
            chunk.content,
            chunk_metadata={
                "kb_id": kb_id,
                "chunk_id": chunk.chunk_id,
                "file_id": chunk.file_id,
                "chunk_index": chunk.chunk_index,
            },
        )
        normalized_result = normalize_extraction_result(extraction_result, extractor_type)
        await self.chunk_repo.update_extraction_result(chunk.chunk_id, normalized_result)
        return normalized_result

    def write_chunk_graph(
        self,
        kb_id: str,
        chunk,
        normalized_result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将单个 chunk 的抽取结果写入 Neo4j。"""
        label = safe_neo4j_label(kb_id)
        graph_payload = build_graph_payload(normalized_result)
        relation_extractor_type = graph_payload["metadata"].get("extractor_type", "unknown")
        entities = graph_payload["entities"]
        relations = graph_payload["relations"]
        entity_by_id = {entity["id"]: entity for entity in entities}
        entity_records = self._build_entity_records(kb_id, entities)
        entity_record_by_local_id = {
            entity["id"]: record for entity, record in zip(entities, entity_records, strict=True)
        }
        triple_records = self._build_triple_records(kb_id, relations, entity_record_by_local_id, graph_payload)
        content_preview = (chunk.content or "")[:300]

        # 预构建 Cypher 模板（同一 chunk 内复用）
        merge_chunk_cypher = cypher_merge_chunk(label)
        merge_entity_cypher = cypher_merge_entity_mention(label)
        merge_relation_cypher = cypher_merge_relation(label)

        def query(tx):
            # 1. MERGE Chunk 节点
            tx.run(
                merge_chunk_cypher,
                chunk_id=chunk.chunk_id,
                file_id=chunk.file_id,
                kb_id=kb_id,
                chunk_index=chunk.chunk_index,
                content_preview=content_preview,
                start_char_pos=chunk.start_char_pos,
                end_char_pos=chunk.end_char_pos,
            )

            # 2. MERGE Entity 节点 + Chunk→Entity (MENTIONS)
            for entity in entities:
                entity_record = entity_record_by_local_id[entity["id"]]
                tx.run(
                    merge_entity_cypher,
                    chunk_id=chunk.chunk_id,
                    file_id=chunk.file_id,
                    kb_id=kb_id,
                    entity_id=entity_record["entity_id"],
                    normalized_name=normalize_entity_name(entity["text"]),
                    entity_label=entity.get("label") or "Entity",
                    name=entity["text"],
                    attributes=json.dumps(entity.get("attributes") or [], ensure_ascii=False),
                )

            # 3. MERGE Entity→Entity (RELATION) 边
            for relation in relations:
                source = entity_by_id[relation["source"]]
                target = entity_by_id[relation["target"]]
                source_record = entity_record_by_local_id[relation["source"]]
                target_record = entity_record_by_local_id[relation["target"]]
                relation_type = relation.get("label") or "RELATED_TO"
                triple_id = compute_triple_id(
                    kb_id,
                    source_record["normalized_name"],
                    source_record["label"],
                    relation_type,
                    target_record["normalized_name"],
                    target_record["label"],
                )
                tx.run(
                    merge_relation_cypher,
                    kb_id=kb_id,
                    chunk_id=chunk.chunk_id,
                    file_id=chunk.file_id,
                    source_name=normalize_entity_name(source["text"]),
                    source_label=source.get("label") or "Entity",
                    target_name=normalize_entity_name(target["text"]),
                    target_label=target.get("label") or "Entity",
                    relation_type=relation_type,
                    triple_id=triple_id,
                    text=relation["text"],
                    extractor_type=relation_extractor_type,
                )

        neo4j_write(self.driver, query)
        return entity_records, triple_records

    def _build_entity_records(self, kb_id: str, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records = []
        for entity in entities:
            label = entity.get("label") or "Entity"
            normalized_name = normalize_entity_name(entity["text"])
            entity_id = compute_entity_id(kb_id, normalized_name, label)
            records.append(
                {
                    "entity_id": entity_id,
                    "kb_id": kb_id,
                    "normalized_name": normalized_name,
                    "label": label,
                    "name": entity["text"],
                    "attributes": entity.get("attributes") or [],
                    "content": normalized_name,
                }
            )
        return records

    def _build_triple_records(
        self,
        kb_id: str,
        relations: list[dict[str, Any]],
        entity_record_by_local_id: dict[str, dict[str, Any]],
        graph_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        records = []
        seen_triple_ids: set[str] = set()
        extractor_type = graph_payload["metadata"].get("extractor_type", "unknown")
        for relation in relations:
            source_record = entity_record_by_local_id[relation["source"]]
            target_record = entity_record_by_local_id[relation["target"]]
            relation_type = relation.get("label") or "RELATED_TO"
            triple_id = compute_triple_id(
                kb_id,
                source_record["normalized_name"],
                source_record["label"],
                relation_type,
                target_record["normalized_name"],
                target_record["label"],
            )
            if triple_id in seen_triple_ids:
                continue
            seen_triple_ids.add(triple_id)
            content = f"{source_record['normalized_name']} → {relation_type} → {target_record['normalized_name']}"
            records.append(
                {
                    "triple_id": triple_id,
                    "kb_id": kb_id,
                    "source_entity_id": source_record["entity_id"],
                    "target_entity_id": target_record["entity_id"],
                    "relation_type": relation_type,
                    "content": content,
                    "text": relation["text"],
                    "extractor_type": extractor_type,
                }
            )
        return records

    async def reset(self, kb_id: str, *, clear_extraction_result: bool, clear_config: bool) -> dict[str, Any]:
        kb = await self._get_milvus_kb(kb_id)
        await asyncio.to_thread(self.delete_graph, kb_id)
        await self.graph_repo.delete_by_kb_id(kb_id)
        reset_chunks = await self.chunk_repo.reset_graph_state_by_kb_id(kb_id, clear_extraction_result)
        if clear_config:
            additional_params = dict(kb.additional_params or {})
            additional_params.pop(GRAPH_CONFIG_KEY, None)
            await self.kb_repo.update(kb_id, {"additional_params": additional_params})
        return {
            "message": "图谱构建状态已重置",
            "status": "success",
            "reset_chunks": reset_chunks,
            "clear_extraction_result": clear_extraction_result,
            "clear_config": clear_config,
        }

    def delete_graph(self, kb_id: str) -> None:
        label = safe_neo4j_label(kb_id)

        def query(tx):
            tx.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n")

        neo4j_write(self.driver, query)
        self.graph_vector_store.drop_graph_collections(kb_id)

    async def delete_file_graph(self, kb_id: str, file_id: str) -> None:
        orphan_entity_ids, orphan_triple_ids = await self.graph_repo.delete_file_references(file_id)
        await self.graph_vector_store.delete_graph_records(
            kb_id,
            entity_ids=orphan_entity_ids,
            triple_ids=orphan_triple_ids,
        )
        await asyncio.to_thread(self._delete_file_graph_from_neo4j, kb_id, file_id)

    def _delete_file_graph_from_neo4j(self, kb_id: str, file_id: str) -> None:
        label = safe_neo4j_label(kb_id)

        def query(tx):
            tx.run(
                f"""
                MATCH (:Chunk:MilvusKB:`{label}`)-[m:MENTIONS {{kb_id: $kb_id, file_id: $file_id}}]->
                    (:Entity:MilvusKB:`{label}`)
                DELETE m
                """,
                kb_id=kb_id,
                file_id=file_id,
            )
            tx.run(
                f"""
                MATCH (:Entity:MilvusKB:`{label}`)-[r:RELATION {{kb_id: $kb_id, file_id: $file_id}}]->
                    (:Entity:MilvusKB:`{label}`)
                DELETE r
                """,
                kb_id=kb_id,
                file_id=file_id,
            )
            tx.run(
                f"""
                MATCH (c:Chunk:MilvusKB:`{label}` {{kb_id: $kb_id, file_id: $file_id}})
                DETACH DELETE c
                """,
                kb_id=kb_id,
                file_id=file_id,
            )
            tx.run(
                f"""
                MATCH (e:Entity:MilvusKB:`{label}` {{kb_id: $kb_id}})
                WHERE NOT ()-[:MENTIONS]->(e)
                DETACH DELETE e
                """,
                kb_id=kb_id,
            )

        neo4j_write(self.driver, query)

    async def query_nodes(
        self,
        kb_id: str | None = None,
        *,
        keyword: str = "",
        max_depth: int = 1,
        max_nodes: int = 50,
        exclude_chunk: bool = False,
    ) -> dict[str, Any]:
        effective_kb_id = kb_id or self.kb_id
        if not effective_kb_id:
            return {"nodes": [], "edges": []}

        label = safe_neo4j_label(effective_kb_id)
        limit = max_nodes
        try:
            return await _run_neo4j_query_io(
                self._query_nodes_sync,
                effective_kb_id,
                label,
                keyword,
                limit,
                max_depth,
                exclude_chunk,
            )
        except Exception as e:
            logger.error(f"Milvus graph query failed: {e}")
            return {"nodes": [], "edges": []}

    def _query_nodes_sync(
        self,
        kb_id: str,
        label: str,
        keyword: str,
        limit: int,
        max_depth: int,
        exclude_chunk: bool,
    ) -> dict[str, Any]:
        with self.driver.session() as session:
            result = session.run(
                self._build_query(label, keyword, limit, max_depth, exclude_chunk),
                keyword=keyword,
                limit=limit,
                edge_limit=limit * 10,
            )
            return self._process_query_result(result, limit, kb_id, exclude_chunk)

    async def query_seed_subgraph(
        self,
        kb_id: str,
        *,
        entity_ids: list[str],
        max_nodes: int,
    ) -> dict[str, Any]:
        if not entity_ids:
            return {"nodes": [], "edges": []}
        seed_entity_ids = list(dict.fromkeys(entity_ids))
        label = safe_neo4j_label(kb_id)
        cypher = f"""
        MATCH (seed:Entity:MilvusKB:`{label}`)
        WHERE seed.entity_id IN $entity_ids
        MATCH p = (seed)-[*1..2]-(n:MilvusKB:`{label}`)
        WITH p LIMIT $path_limit
        WITH collect(p) AS paths
        UNWIND paths AS node_path
        UNWIND nodes(node_path) AS node
        WITH paths, collect(DISTINCT node) AS graph_nodes
        UNWIND paths AS rel_path
        UNWIND relationships(rel_path) AS rel
        RETURN graph_nodes AS nodes, collect(DISTINCT rel) AS edges
        """
        try:
            return await _run_neo4j_query_io(
                self._query_seed_subgraph_sync,
                kb_id,
                cypher,
                seed_entity_ids,
                max_nodes,
            )
        except Exception as e:
            logger.error(f"Milvus seed subgraph query failed: {e}")
            return {"nodes": [], "edges": []}

    def _query_seed_subgraph_sync(
        self,
        kb_id: str,
        cypher: str,
        entity_ids: list[str],
        max_nodes: int,
    ) -> dict[str, Any]:
        with self.driver.session() as session:
            record = session.run(
                cypher,
                entity_ids=entity_ids,
                path_limit=max(max_nodes, 1) * 4,
            ).single()
            if not record:
                return {"nodes": [], "edges": []}
            return self._process_subgraph_record(record, max_nodes, kb_id)

    async def query_and_rank_chunks_by_ppr(
        self,
        kb_id: str,
        seed_weights: dict[str, float],
        *,
        max_nodes: int,
        top_k: int,
        damping: float,
    ) -> list[tuple[str, float]]:
        if not seed_weights:
            return []
        subgraph = await self.query_seed_subgraph(
            kb_id,
            entity_ids=list(seed_weights.keys()),
            max_nodes=max_nodes,
        )
        return self.rank_chunks_by_ppr(subgraph, seed_weights, top_k=top_k, damping=damping)

    @staticmethod
    def rank_chunks_by_ppr(
        subgraph: dict[str, Any],
        seed_weights: dict[str, float],
        *,
        top_k: int,
        damping: float,
    ) -> list[tuple[str, float]]:
        nodes = subgraph.get("nodes") or []
        edges = subgraph.get("edges") or []
        if not nodes:
            return []

        try:
            import igraph as ig
        except ImportError:
            logger.error("Graph retrieval requires python-igraph. Please install igraph.")
            return []

        node_ids = [node["id"] for node in nodes]
        index_by_id = {node_id: index for index, node_id in enumerate(node_ids)}
        edge_indices = [
            (index_by_id[edge["source_id"]], index_by_id[edge["target_id"]])
            for edge in edges
            if edge.get("source_id") in index_by_id and edge.get("target_id") in index_by_id
        ]
        if not edge_indices:
            return []

        graph = ig.Graph(n=len(nodes), edges=edge_indices, directed=False)
        reset = [0.0] * len(nodes)
        chunk_node_indexes: list[tuple[int, str]] = []
        for index, node in enumerate(nodes):
            properties = node.get("properties") or {}
            if node.get("type") == "Chunk" and properties.get("chunk_id"):
                chunk_node_indexes.append((index, properties["chunk_id"]))
                continue
            entity_id = properties.get("entity_id")
            if entity_id in seed_weights:
                reset[index] = seed_weights[entity_id]

        reset_total = sum(reset)
        if reset_total <= 0 or not chunk_node_indexes:
            return []
        reset = [value / reset_total for value in reset]
        scores = graph.personalized_pagerank(damping=min(max(damping, 0.1), 0.99), reset=reset)
        ranked = sorted(
            ((chunk_id, float(scores[index])) for index, chunk_id in chunk_node_indexes),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:top_k]

    async def get_labels(self, kb_id: str | None = None) -> list[str]:
        effective_kb_id = kb_id or self.kb_id
        if not effective_kb_id:
            return []
        label = safe_neo4j_label(effective_kb_id)

        cypher = f"""
        MATCH (n:MilvusKB:`{label}`)
        UNWIND labels(n) AS node_label
        WITH DISTINCT node_label
        WHERE node_label <> 'MilvusKB' AND node_label <> $kb_id
        RETURN node_label
        ORDER BY node_label
        """
        try:
            records = await _run_neo4j_query_io(self._get_labels_sync, cypher, effective_kb_id)
            return [record["node_label"] for record in records]
        except Exception as e:
            logger.error(f"Failed to get Milvus graph labels: {e}")
            return []

    def _get_labels_sync(self, cypher: str, kb_id: str) -> list[Any]:
        return neo4j_read(self.driver, cypher, kb_id=kb_id)

    async def get_stats(self, kb_id: str | None = None) -> dict[str, Any]:
        effective_kb_id = kb_id or self.kb_id
        if not effective_kb_id:
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}
        label = safe_neo4j_label(effective_kb_id)

        stats_cypher = f"""
        MATCH (n:MilvusKB:`{label}`)
        WITH count(n) AS node_count
        OPTIONAL MATCH (:MilvusKB:`{label}`)-[r]->(:MilvusKB:`{label}`)
        RETURN node_count, count(r) AS edge_count
        """
        label_cypher = f"""
        MATCH (n:Entity:MilvusKB:`{label}`)
        WITH n.label AS entity_label, count(*) AS count
        RETURN entity_label, count
        ORDER BY count DESC
        """
        try:
            return await _run_neo4j_query_io(self._get_stats_sync, stats_cypher, label_cypher)
        except Exception as e:
            logger.error(f"Failed to get Milvus graph stats: {e}")
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}

    def _get_stats_sync(self, stats_cypher: str, label_cypher: str) -> dict[str, Any]:
        with self.driver.session() as session:
            stats = session.run(stats_cypher).single()
            label_stats = session.run(label_cypher)
            return {
                "total_nodes": stats["node_count"] if stats else 0,
                "total_edges": stats["edge_count"] if stats else 0,
                "entity_types": [{"type": row["entity_label"], "count": row["count"]} for row in label_stats],
            }

    async def _get_milvus_kb(self, kb_id: str):
        kb = await self.kb_repo.get_by_kb_id(kb_id)
        if kb is None:
            raise ValueError(f"知识库 {kb_id} 不存在")
        if (kb.kb_type or "").lower() != "milvus":
            raise ValueError("仅 Milvus 知识库支持独立图谱构建")
        return kb

    def _get_locked_config(self, additional_params: dict[str, Any]) -> dict[str, Any]:
        config = additional_params.get(GRAPH_CONFIG_KEY) or {}
        if not config.get("locked"):
            raise ValueError("请先确认并锁定图谱抽取配置")
        if not config.get("extractor_type"):
            raise ValueError("图谱抽取配置缺少 extractor_type")
        return config

    def _public_config(self, config: dict[str, Any]) -> dict[str, Any] | None:
        if not config:
            return None
        return {
            "locked": bool(config.get("locked")),
            "extractor_type": config.get("extractor_type"),
            "extractor_options": self._runtime_extractor_options(config),
            "created_at": config.get("created_at"),
            "created_by": config.get("created_by"),
            "updated_at": config.get("updated_at"),
            "updated_by": config.get("updated_by"),
        }

    @staticmethod
    def _build_where(exclude_chunk: bool, keyword: str) -> str:
        clauses = []
        if exclude_chunk:
            clauses.append("NOT n:Chunk")
        if keyword and keyword != "*":
            clauses.append(
                "(toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)"
                " OR toLower(coalesce(n.content_preview, '')) CONTAINS toLower($keyword)"
                " OR toLower(coalesce(n.chunk_id, '')) CONTAINS toLower($keyword))"
            )
        return "WHERE " + " AND ".join(clauses) if clauses else ""

    def _build_query(self, label: str, keyword: str, limit: int, max_depth: int, exclude_chunk: bool = False) -> str:
        where = self._build_where(exclude_chunk, keyword)
        m_exclude = " WHERE NOT m:Chunk" if exclude_chunk else ""

        if max_depth <= 0:
            return f"""
            MATCH (n:MilvusKB:`{label}`)
            {where}
            RETURN n AS h, null AS r, null AS t
            LIMIT $limit
            """

        return f"""
        MATCH (n:MilvusKB:`{label}`)
        {where}
        WITH n LIMIT $limit
        OPTIONAL MATCH (n)-[r]-(m:MilvusKB:`{label}`){m_exclude}
        RETURN n AS h, r AS r, m AS t
        LIMIT $edge_limit
        """

    def _process_query_result(self, result, limit: int, kb_id: str, exclude_chunk: bool = False) -> dict[str, Any]:
        nodes = []
        edges = []
        node_ids = set()
        edge_ids = set()

        for record in result:
            for key in ("h", "t"):
                raw_node = record.get(key)
                if raw_node is None:
                    continue
                node = self._normalize_node(raw_node, kb_id)
                if not node or node["id"] in node_ids:
                    continue
                if exclude_chunk and node.get("type") == "Chunk":
                    continue
                nodes.append(node)
                node_ids.add(node["id"])
            raw_edge = record.get("r")
            if raw_edge is not None:
                edge = self._normalize_edge(raw_edge)
                if edge and edge["id"] not in edge_ids:
                    edges.append(edge)
                    edge_ids.add(edge["id"])
            if len(nodes) >= limit:
                break

        return {"nodes": nodes[:limit], "edges": edges[: limit * 2]}

    def _process_subgraph_record(self, record: Any, limit: int, kb_id: str) -> dict[str, Any]:
        nodes = []
        edges = []
        node_ids = set()
        edge_ids = set()

        for raw_node in record.get("nodes") or []:
            node = self._normalize_node(raw_node, kb_id)
            if not node or node["id"] in node_ids:
                continue
            nodes.append(node)
            node_ids.add(node["id"])
            if len(nodes) >= limit:
                break

        for raw_edge in record.get("edges") or []:
            edge = self._normalize_edge(raw_edge)
            if not edge or edge["id"] in edge_ids:
                continue
            if edge["source_id"] not in node_ids or edge["target_id"] not in node_ids:
                continue
            edges.append(edge)
            edge_ids.add(edge["id"])

        return {"nodes": nodes, "edges": edges}

    def _normalize_node(self, raw_node: Any, kb_id: str | None = None) -> dict[str, Any]:
        if hasattr(raw_node, "element_id"):
            node_id = raw_node.element_id
            labels = list(raw_node.labels)
            properties = dict(raw_node.items())
        elif isinstance(raw_node, dict):
            node_id = raw_node.get("id") or raw_node.get("element_id")
            labels = raw_node.get("labels", [])
            properties = raw_node.get("properties") or {k: v for k, v in raw_node.items() if k not in {"id", "labels"}}
        else:
            return {}

        effective_kb_id = kb_id or self.kb_id
        db_label = properties.get("kb_id") or effective_kb_id
        filtered_labels = [label for label in labels if label not in {"MilvusKB", db_label}]
        entity_type = "Chunk" if "Chunk" in labels else properties.get("label", "Entity")
        name = properties.get("name") or properties.get("content_preview") or properties.get("chunk_id") or "Unknown"
        return {
            "id": node_id,
            "name": name,
            "original_id": node_id,
            "type": entity_type,
            "labels": filtered_labels,
            "properties": properties,
            "normalized": {
                "name": name,
                "type": entity_type,
                "source": "milvus",
            },
            "graph_type": "milvus",
        }

    def _normalize_edge(self, raw_edge: Any) -> dict[str, Any]:
        if hasattr(raw_edge, "element_id"):
            edge_id = raw_edge.element_id
            edge_type = raw_edge.type
            source_id = raw_edge.start_node.element_id
            target_id = raw_edge.end_node.element_id
            properties = dict(raw_edge.items())
            edge_type = properties.get("type") or edge_type
        elif isinstance(raw_edge, dict):
            edge_id = raw_edge.get("id")
            edge_type = raw_edge.get("type")
            source_id = raw_edge.get("source_id")
            target_id = raw_edge.get("target_id")
            properties = raw_edge.get("properties", {})
        else:
            return {}

        return {
            "id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "type": edge_type,
            "properties": properties,
            "normalized": {
                "type": edge_type,
                "direction": "directed",
            },
        }
