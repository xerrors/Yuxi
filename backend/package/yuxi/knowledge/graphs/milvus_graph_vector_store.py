from __future__ import annotations

import asyncio
import os
from functools import partial
from typing import Any

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    connections,
    db,
    utility,
)

from yuxi.knowledge.graphs.graph_utils import graph_entity_collection_name, graph_triple_collection_name
from yuxi.knowledge.implementations.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    _run_milvus_query_io,
)
from yuxi.models.embed import select_embedding_model
from yuxi.models.providers.cache import model_cache
from yuxi.utils import hashstr, logger


class MilvusGraphVectorStore:
    def __init__(self):
        self.milvus_token = os.getenv("MILVUS_TOKEN") or ""
        self.milvus_uri = os.getenv("MILVUS_URI") or "http://localhost:19530"
        self.milvus_db = os.getenv("MILVUS_DB") or "yuxi"
        self.connection_alias = f"milvus_graph_{hashstr(self.milvus_uri, 6)}"
        self._init_connection()

    def _init_connection(self) -> None:
        if not connections.has_connection(self.connection_alias):
            connections.connect(alias=self.connection_alias, uri=self.milvus_uri, token=self.milvus_token)
        try:
            if self.milvus_db not in db.list_database():
                db.create_database(self.milvus_db)
            db.using_database(self.milvus_db)
        except Exception as exc:
            logger.warning(f"Milvus graph database operation failed, using default: {exc}")

    async def insert_missing_graph_records(
        self,
        *,
        kb_id: str,
        embedding_model_spec: str,
        entities: list[dict[str, Any]],
        triples: list[dict[str, Any]],
    ) -> None:
        if not entities and not triples:
            return

        embedding_info = model_cache.get_model_info(embedding_model_spec)
        if not embedding_info or embedding_info.model_type != "embedding":
            raise ValueError(f"Unsupported embedding model: {embedding_model_spec}")

        entity_collection = self._get_or_create_entity_collection(kb_id, embedding_info)
        triple_collection = self._get_or_create_triple_collection(kb_id, embedding_info)

        entity_ids = [entity["entity_id"] for entity in entities]
        triple_ids = [triple["triple_id"] for triple in triples]
        existing_entity_ids, existing_triple_ids = await asyncio.gather(
            asyncio.to_thread(self._query_existing_ids, entity_collection, entity_ids),
            asyncio.to_thread(self._query_existing_ids, triple_collection, triple_ids),
        )

        missing_entities = [entity for entity in entities if entity["entity_id"] not in existing_entity_ids]
        missing_triples = [triple for triple in triples if triple["triple_id"] not in existing_triple_ids]
        if not missing_entities and not missing_triples:
            return

        embed = self._get_embedding_function(embedding_model_spec)
        entity_embeddings, triple_embeddings = await asyncio.gather(
            embed([entity["content"] for entity in missing_entities]) if missing_entities else self._empty_embeddings(),
            embed([triple["content"] for triple in missing_triples]) if missing_triples else self._empty_embeddings(),
        )

        if missing_entities:
            await asyncio.to_thread(self._insert_entities, entity_collection, missing_entities, entity_embeddings)
        if missing_triples:
            await asyncio.to_thread(self._insert_triples, triple_collection, missing_triples, triple_embeddings)

    async def delete_graph_records(self, kb_id: str, *, entity_ids: list[str], triple_ids: list[str]) -> None:
        tasks = []
        if entity_ids:
            tasks.append(asyncio.to_thread(self._delete_ids, graph_entity_collection_name(kb_id), entity_ids))
        if triple_ids:
            tasks.append(asyncio.to_thread(self._delete_ids, graph_triple_collection_name(kb_id), triple_ids))
        if tasks:
            await asyncio.gather(*tasks)

    async def search_entities(
        self,
        *,
        kb_id: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        collection_name = graph_entity_collection_name(kb_id)
        has_collection = await _run_milvus_query_io(
            utility.has_collection, collection_name, using=self.connection_alias
        )
        if not has_collection:
            return []
        return await self._search_graph_collection(
            collection_name=collection_name,
            query_text=query_text,
            embedding_model_spec=embedding_model_spec,
            top_k=top_k,
            output_fields=["id", "content"],
        )

    async def search_triples(
        self,
        *,
        kb_id: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        collection_name = graph_triple_collection_name(kb_id)
        has_collection = await _run_milvus_query_io(
            utility.has_collection, collection_name, using=self.connection_alias
        )
        if not has_collection:
            return []
        return await self._search_graph_collection(
            collection_name=collection_name,
            query_text=query_text,
            embedding_model_spec=embedding_model_spec,
            top_k=top_k,
            output_fields=["id", "content", "source_id", "target_id"],
        )

    def drop_graph_collections(self, kb_id: str) -> None:
        for collection_name in [graph_entity_collection_name(kb_id), graph_triple_collection_name(kb_id)]:
            try:
                if utility.has_collection(collection_name, using=self.connection_alias):
                    utility.drop_collection(collection_name, using=self.connection_alias)
                    logger.info(f"Dropped Milvus graph collection {collection_name}")
            except Exception as exc:
                logger.error(f"Failed to drop Milvus graph collection {collection_name}: {exc}")

    async def _empty_embeddings(self) -> list:
        return []

    def _get_embedding_function(self, embedding_model_spec: str):
        model = select_embedding_model(embedding_model_spec)
        batch_size = int(getattr(model, "batch_size", 40) or 40)
        return partial(model.abatch_encode, batch_size=batch_size)

    async def _search_graph_collection(
        self,
        *,
        collection_name: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        embed = self._get_embedding_function(embedding_model_spec)
        query_embedding = await embed([query_text])
        return await _run_milvus_query_io(
            self._search_graph_collection_sync,
            collection_name,
            query_embedding,
            max(top_k, 1),
            output_fields,
        )

    def _search_graph_collection_sync(
        self,
        collection_name: str,
        query_embedding: list,
        top_k: int,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        collection = Collection(name=collection_name, using=self.connection_alias)
        collection.load()
        return self._search_loaded_collection(collection, query_embedding, top_k, output_fields)

    def _search_loaded_collection(
        self,
        collection: Collection,
        query_embedding: list,
        top_k: int,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        results = collection.search(
            data=query_embedding,
            anns_field="embedding",
            param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
            limit=top_k,
            output_fields=output_fields,
        )
        if not results or not results[0]:
            return []

        records = []
        for hit in results[0]:
            entity = hit.entity
            record = {field: entity.get(field) for field in output_fields}
            record["score"] = float(hit.distance or 0.0)
            records.append(record)
        return records

    def _get_or_create_entity_collection(self, kb_id: str, embedding_info: Any) -> Collection:
        collection_name = graph_entity_collection_name(kb_id)
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
            ),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_info.dimension or 1024),
            FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
        ]
        return self._get_or_create_collection(collection_name, fields, embedding_info)

    def _get_or_create_triple_collection(self, kb_id: str, embedding_info: Any) -> Collection:
        collection_name = graph_triple_collection_name(kb_id)
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
            ),
            FieldSchema(name="source_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="target_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_info.dimension or 1024),
            FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
        ]
        return self._get_or_create_collection(collection_name, fields, embedding_info)

    def _get_or_create_collection(
        self, collection_name: str, fields: list[FieldSchema], embedding_info: Any
    ) -> Collection:
        if utility.has_collection(collection_name, using=self.connection_alias):
            return Collection(name=collection_name, using=self.connection_alias)

        bm25_function = Function(
            name="content_bm25",
            input_field_names=["content"],
            output_field_names=[CONTENT_SPARSE_FIELD],
            function_type=FunctionType.BM25,
        )
        schema = CollectionSchema(
            fields=fields,
            description=f"Knowledge graph collection {collection_name} using {embedding_info.model_id}",
            functions=[bm25_function],
        )
        collection = Collection(name=collection_name, schema=schema, using=self.connection_alias)
        collection.create_index(
            "embedding", {"metric_type": VECTOR_METRIC_TYPE, "index_type": "IVF_FLAT", "params": {"nlist": 1024}}
        )
        collection.create_index(
            CONTENT_SPARSE_FIELD,
            {
                "metric_type": "BM25",
                "index_type": "SPARSE_INVERTED_INDEX",
                "params": {"inverted_index_algo": "DAAT_MAXSCORE"},
            },
        )
        return collection

    def _query_existing_ids(self, collection: Collection, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        collection.load()
        existing_ids: set[str] = set()
        for start in range(0, len(ids), 1000):
            batch = ids[start : start + 1000]
            quoted_ids = ", ".join(f'"{item}"' for item in batch)
            rows = collection.query(expr=f"id in [{quoted_ids}]", output_fields=["id"])
            existing_ids.update(row["id"] for row in rows)
        return existing_ids

    def _insert_entities(self, collection: Collection, entities: list[dict[str, Any]], embeddings: list) -> None:
        collection.insert(
            [
                [entity["entity_id"] for entity in entities],
                [entity["content"] for entity in entities],
                embeddings,
            ]
        )

    def _insert_triples(self, collection: Collection, triples: list[dict[str, Any]], embeddings: list) -> None:
        collection.insert(
            [
                [triple["triple_id"] for triple in triples],
                [triple["content"] for triple in triples],
                [triple["source_entity_id"] for triple in triples],
                [triple["target_entity_id"] for triple in triples],
                embeddings,
            ]
        )

    def _delete_ids(self, collection_name: str, ids: list[str]) -> None:
        if not utility.has_collection(collection_name, using=self.connection_alias):
            return
        collection = Collection(name=collection_name, using=self.connection_alias)
        for start in range(0, len(ids), 1000):
            batch = ids[start : start + 1000]
            quoted_ids = ", ".join(f'"{item}"' for item in batch)
            collection.delete(expr=f"id in [{quoted_ids}]")
