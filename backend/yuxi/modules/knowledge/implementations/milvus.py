import asyncio
import json
import os
import re
import time
import traceback
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    Collection,
    DataType,
    FieldSchema,
    FunctionType,
    LexicalHighlighter,
    WeightedRanker,
    connections,
    db,
    utility,
)

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.base import FileStatus, KBEmbeddingModelMissingError, KnowledgeExecutor
from yuxi.modules.knowledge.chunking.dispatcher import IngestionChunk, chunk_markdown
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.storage.milvus import (
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    content_fields,
    create_collection,
    embedding_field,
    get_embedding_function,
)
from yuxi.modules.knowledge.storage.offload import run_milvus_query_io
from yuxi.modules.models.providers.cache import model_cache
from yuxi.modules.system.options import system_options
from yuxi.shared.hashing import hashstr

MILVUS_CHUNK_EMBED_BATCH_SIZE = 200
# Milvus 为派生索引：PG 先行落库（权威源），Milvus upsert 失败有限次重试，
# 重试耗尽则抛错由上层标记文件状态；不回滚 PG（避免单边数据）。
# 重新索引时 PG batch_upsert 与 Milvus upsert 均按主键幂等，重跑即可收敛。
MILVUS_CHUNK_UPSERT_ATTEMPTS = 3
MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS = 1.0


@dataclass(kw_only=True)
class MilvusRetrievalConfig:
    search_mode: str = field(
        default="vector",
        metadata={
            "label": "检索模式",
            "type": "select",
            "options": [
                {"value": "vector", "label": "向量检索", "description": "仅使用向量相似度检索"},
                {"value": "keyword", "label": "BM25 全文检索", "description": "仅使用 Milvus BM25 检索"},
                {"value": "hybrid", "label": "混合检索", "description": "Milvus 向量检索与 BM25 融合检索"},
            ],
            "description": "选择检索模式",
        },
    )
    required_terms: str = field(
        default="",
        metadata={"label": "必含词", "type": "string", "description": "空格分隔；每个词项都必须匹配，按分词结果判断"},
    )
    excluded_terms: str = field(
        default="",
        metadata={"label": "排除词", "type": "string", "description": "空格分隔；排除包含任一词项的片段"},
    )
    exact_phrase: str = field(
        default="",
        metadata={"label": "完整短语", "type": "string", "description": "按分词后的顺序匹配完整短语，不允许间隔词项"},
    )
    highlight_results: bool = field(
        default=True,
        metadata={"label": "命中高亮", "type": "boolean", "description": "展示关键词和文本筛选条件命中的片段"},
    )
    final_top_k: int = field(
        default=10,
        metadata={
            "label": "最终返回 Chunk 数",
            "type": "number",
            "min": 1,
            "max": 100,
            "description": "重排序后返回给前端的文档数量",
        },
    )
    similarity_threshold: float = field(
        default=0.0,
        metadata={
            "label": "向量相似度阈值（0-1）",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "仅对纯向量检索生效，过滤低于此值的结果",
        },
    )
    bm25_top_k: int = field(
        default=50,
        metadata={
            "label": "BM25 召回数量",
            "type": "number",
            "min": 1,
            "max": 200,
            "description": "BM25 全文检索和混合检索中的 BM25 候选数量",
        },
    )
    vector_weight: float = field(
        default=0.7,
        metadata={
            "label": "向量检索权重",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "混合检索中向量召回结果的融合权重",
        },
    )
    bm25_weight: float = field(
        default=0.3,
        metadata={
            "label": "BM25 权重",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "混合检索中 BM25 召回结果的融合权重",
        },
    )
    bm25_drop_ratio_search: float = field(
        default=0.0,
        metadata={
            "label": "BM25 稀疏项丢弃比例",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "BM25 检索时丢弃低分稀疏项的比例，数值越大检索越快但可能降低召回",
        },
    )
    include_distances: bool = field(
        default=True,
        metadata={"label": "显示相似度", "type": "boolean", "description": "在结果中显示相似度分数"},
    )
    use_graph_retrieval: bool = field(
        default=False,
        metadata={"label": "启用图检索", "type": "boolean", "description": "是否启用实体和三元组扩散检索"},
    )
    graph_entity_top_k: int = field(
        default=10,
        metadata={
            "label": "图实体召回数量",
            "type": "number",
            "min": 1,
            "max": 100,
            "depend_on": ("use_graph_retrieval", True),
            "description": "通过 Query 召回的实体数量",
        },
    )
    graph_triple_top_k: int = field(
        default=10,
        metadata={
            "label": "图三元组召回数量",
            "type": "number",
            "min": 1,
            "max": 100,
            "depend_on": ("use_graph_retrieval", True),
            "description": "通过 Query 召回的三元组数量",
        },
    )
    graph_max_nodes: int = field(
        default=10000,
        metadata={
            "label": "图检索最大节点数",
            "type": "number",
            "min": 100,
            "max": 50000,
            "depend_on": ("use_graph_retrieval", True),
            "description": "2-hop 扩散子图最多读取的节点数量",
        },
    )
    graph_top_k: int = field(
        default=20,
        metadata={
            "label": "图召回 Chunk 数",
            "type": "number",
            "min": 1,
            "max": 200,
            "depend_on": ("use_graph_retrieval", True),
            "description": "PPR 后从图谱路径召回的 Chunk 数量",
        },
    )
    graph_weight: float = field(
        default=1.0,
        metadata={
            "label": "图检索融合权重",
            "type": "number",
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "depend_on": ("use_graph_retrieval", True),
            "description": "排名融合时图检索结果的权重",
        },
    )
    ppr_damping: float = field(
        default=0.85,
        metadata={
            "label": "PPR 阻尼系数",
            "type": "number",
            "min": 0.1,
            "max": 0.99,
            "step": 0.01,
            "depend_on": ("use_graph_retrieval", True),
            "description": "Personalized PageRank 的阻尼系数",
        },
    )
    use_reranker: bool = field(
        default=False,
        metadata={"label": "启用重排序", "type": "boolean", "description": "是否使用精排模型对检索结果进行重排序"},
    )
    reranker_model: str = field(
        default="",
        metadata={
            "label": "重排序模型",
            "type": "select",
            "depend_on": ("use_reranker", True),
            "description": "选择用于本次查询的重排序模型",
            "options_provider": "rerank_models",
        },
    )
    recall_top_k: int = field(
        default=50,
        metadata={
            "label": "召回数量",
            "type": "number",
            "min": 10,
            "max": 200,
            "depend_on": ("use_reranker", True),
            "description": "向量检索或混合检索保留的候选数量（启用重排序时有效）",
        },
    )


def _retrieval_config_options() -> list[dict[str, Any]]:
    options = []
    for config_field in fields(MilvusRetrievalConfig):
        metadata = dict(config_field.metadata)
        options_provider = metadata.pop("options_provider", None)
        default = None if config_field.default is MISSING else config_field.default
        option = {
            "key": config_field.name,
            "default": default,
            **metadata,
        }
        if options_provider == "rerank_models":
            option["options"] = [{"label": info.display_name, "value": info.spec} for info in model_cache.get_all_specs("rerank")]
        options.append(option)
    return options


class MilvusKB(KnowledgeExecutor):
    """基于 Milvus 的生产级向量库"""

    MAX_FINAL_TOP_K = 100
    MAX_RECALL_TOP_K = 200
    MAX_BM25_TOP_K = 200
    MAX_GRAPH_ENTITY_TOP_K = 100
    MAX_GRAPH_TRIPLE_TOP_K = 100
    MAX_GRAPH_TOP_K = 200
    MAX_GRAPH_NODES = 50_000

    kb_type = "milvus"
    name = "Milvus"
    description = "基于 Milvus 的生产级向量知识库，适合高性能部署"

    def __init__(self, work_dir: str, **kwargs):
        """初始化 Milvus 知识库并建立连接。"""
        super().__init__(work_dir)

        self.milvus_token = kwargs.get("milvus_token", os.getenv("MILVUS_TOKEN") or "")
        self.milvus_uri = kwargs.get("milvus_uri", os.getenv("MILVUS_URI") or "http://localhost:19530")
        self.milvus_db = kwargs.get("milvus_db") or os.getenv("MILVUS_DB") or "yuxi"

        self.connection_alias = f"milvus_{hashstr(work_dir, 6)}"

        # 存储集合映射 {kb_id: Collection}
        self.collections: dict[str, Any] = {}
        from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

        self.graph_service = KnowledgeGraphService()

        self._init_connection()
        logger.info("MilvusKB initialized")

    def __del__(self):
        """清理连接"""
        try:
            if hasattr(self, "connection_alias"):
                connections.disconnect(self.connection_alias)
        except Exception:  # noqa: S110
            pass

    # ==== 文件写入：索引 ====

    async def index_file(self, kb_id: str, file_id: str, *args, **kwargs) -> dict:
        """持有投影写锁直到所有 I/O 完成，清理不能被旧 attempt 的晚写越过。"""
        from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

        async with knowledge_projection_lock(kb_id, shared=True):
            return await self._index_file(kb_id, file_id, *args, **kwargs)

    # ==== 检索 ====

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        **kwargs,
    ) -> list[dict]:
        """异步查询知识库"""
        embedding_model_spec = config.embedding_model_spec
        collection = await self._get_existing_milvus_collection(kb_id)
        if not collection:
            if await KnowledgeChunkRepository().count_by_kb_id(kb_id):
                raise ValueError(f"Knowledge base {kb_id} has no vector projection; reindex is required")
            return []

        # 合并查询参数：kwargs（单次临时参数）优先级高于 config.query_options（持久化配置）
        merged_kwargs = {**asdict(MilvusRetrievalConfig()), **config.query_options, **kwargs}

        try:
            logger.debug(f"Query params: {merged_kwargs}")
            final_top_k = min(max(int(merged_kwargs["final_top_k"]), 1), self.MAX_FINAL_TOP_K)
            similarity_threshold = float(merged_kwargs["similarity_threshold"])
            include_distances = bool(merged_kwargs["include_distances"])
            search_mode = str(merged_kwargs["search_mode"]).lower()
            if search_mode not in {"vector", "keyword", "hybrid"}:
                raise ValueError("Unsupported Milvus search mode")

            use_reranker = bool(merged_kwargs["use_reranker"])
            use_graph_retrieval = bool(merged_kwargs["use_graph_retrieval"])
            if use_reranker or use_graph_retrieval:
                recall_top_k = min(
                    max(int(merged_kwargs["recall_top_k"]), final_top_k),
                    self.MAX_RECALL_TOP_K,
                )
            else:
                recall_top_k = final_top_k

            active_expr = await self._build_active_generation_expr(kb_id)
            file_expr = await self._build_file_name_expr(kb_id, merged_kwargs.get("file_name"))
            text_expr, highlighter = self._build_text_search(merged_kwargs, search_mode, query_text)
            filter_expr = " and ".join(f"({expr})" for expr in (active_expr, file_expr, text_expr) if expr)

            output_fields = ["content", "chunk_id", "file_id", "generation", "chunk_index"]
            retrieved_chunks: list[dict] = []
            if search_mode == "vector":
                embedding_function = get_embedding_function(embedding_model_spec, sync=True)
                query_embedding = await run_milvus_query_io(embedding_function, [query_text])

                search_params = {"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}}

                results = await run_milvus_query_io(
                    collection.search,
                    data=query_embedding,
                    anns_field="embedding",
                    param=search_params,
                    limit=recall_top_k,
                    expr=filter_expr,
                    output_fields=output_fields,
                    highlighter=highlighter,
                )

                for hit in results[0] if results else []:
                    if hit.distance < similarity_threshold:
                        continue
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, hit.distance, include_distances))

                logger.debug(f"Milvus vector query response: {len(retrieved_chunks)} chunks found (after similarity filtering)")

            elif search_mode == "keyword":
                bm25_top_k = min(
                    max(int(merged_kwargs["bm25_top_k"]), 1),
                    self.MAX_BM25_TOP_K,
                )
                bm25_drop_ratio_search = float(merged_kwargs["bm25_drop_ratio_search"])
                bm25_search_params = {
                    "metric_type": "BM25",
                    "params": {"drop_ratio_search": bm25_drop_ratio_search},
                }

                results = await run_milvus_query_io(
                    collection.search,
                    data=[query_text],
                    anns_field=CONTENT_SPARSE_FIELD,
                    param=bm25_search_params,
                    limit=bm25_top_k,
                    expr=filter_expr,
                    output_fields=output_fields,
                    highlighter=highlighter,
                )

                for hit in results[0] if results else []:
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, hit.distance, include_distances, score_field="bm25_score"))

                logger.debug(f"Milvus BM25 query response: {len(retrieved_chunks)} chunks found")
            else:
                embedding_function = get_embedding_function(embedding_model_spec, sync=True)
                query_embedding = await run_milvus_query_io(embedding_function, [query_text])
                bm25_top_k = min(
                    max(int(merged_kwargs["bm25_top_k"]), 1),
                    self.MAX_BM25_TOP_K,
                )
                bm25_drop_ratio_search = float(merged_kwargs["bm25_drop_ratio_search"])
                vector_weight = float(merged_kwargs["vector_weight"])
                bm25_weight = float(merged_kwargs["bm25_weight"])

                vector_request = AnnSearchRequest(
                    data=query_embedding,
                    anns_field="embedding",
                    param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
                    limit=recall_top_k,
                    expr=filter_expr,
                )
                bm25_request = AnnSearchRequest(
                    data=[query_text],
                    anns_field=CONTENT_SPARSE_FIELD,
                    param={
                        "metric_type": "BM25",
                        "params": {"drop_ratio_search": bm25_drop_ratio_search},
                    },
                    limit=bm25_top_k,
                    expr=filter_expr,
                )
                results = await run_milvus_query_io(
                    collection.hybrid_search,
                    reqs=[vector_request, bm25_request],
                    rerank=WeightedRanker(vector_weight, bm25_weight),
                    limit=recall_top_k,
                    output_fields=output_fields,
                )
                for hit in results[0] if results else []:
                    score = float(hit.distance or 0.0)
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, score, include_distances, score_field="hybrid_score"))

                if highlighter and retrieved_chunks:
                    # 3.0.2 的 hybrid_search 不返回高亮；在已召回 ID 内取文本高亮，保持融合分数与顺序。
                    chunk_ids = [chunk["metadata"]["chunk_id"] for chunk in retrieved_chunks]
                    highlighted = await run_milvus_query_io(
                        collection.search,
                        data=query_embedding,
                        anns_field="embedding",
                        param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
                        limit=len(chunk_ids),
                        expr=f"chunk_id in {json.dumps(chunk_ids)}",
                        output_fields=output_fields,
                        highlighter=highlighter,
                    )
                    highlights_by_id = {
                        hit.entity.get("chunk_id"): self._build_chunk_from_hit(hit, hit.distance, False).get("highlights")
                        for hit in highlighted[0]
                    }
                    for chunk in retrieved_chunks:
                        if fragments := highlights_by_id.get(chunk["metadata"]["chunk_id"]):
                            chunk["highlights"] = fragments

                logger.debug(f"Milvus hybrid query response: {len(retrieved_chunks)} chunks found")

            if use_graph_retrieval:
                graph_chunks = await self._retrieve_graph_chunks(
                    query_text,
                    kb_id,
                    retrieved_chunks,
                    merged_kwargs,
                    embedding_model_spec,
                )
                if graph_chunks and filter_expr:
                    chunk_ids = [chunk["metadata"]["chunk_id"] for chunk in graph_chunks]
                    allowed = await run_milvus_query_io(
                        collection.query,
                        expr=f"({filter_expr}) and chunk_id in {json.dumps(chunk_ids)}",
                        output_fields=["chunk_id"],
                    )
                    allowed_ids = {chunk["chunk_id"] for chunk in allowed}
                    graph_chunks = [chunk for chunk in graph_chunks if chunk["metadata"]["chunk_id"] in allowed_ids]
                if graph_chunks:
                    graph_weight = float(merged_kwargs["graph_weight"])
                    retrieved_chunks = self._fuse_chunk_rankings(retrieved_chunks, graph_chunks, graph_weight)

            if not retrieved_chunks:
                return []

            retrieved_chunks = await self._hydrate_chunk_sources(kb_id, retrieved_chunks)

            if not use_reranker:
                return retrieved_chunks[:final_top_k]

            reranker_model = merged_kwargs["reranker_model"]
            if not reranker_model:
                raise ValueError(
                    "Reranker model must be specified when use_reranker=True. Please provide reranker_model in query parameters."
                )

            try:
                from yuxi.modules.models.rerank import get_reranker

                reranker = get_reranker(reranker_model)
                try:
                    rerank_start = time.time()
                    documents_text = [chunk["content"] for chunk in retrieved_chunks]
                    rerank_scores = await reranker.acompute_score([query_text, documents_text], normalize=True)

                    for chunk, rerank_score in zip(retrieved_chunks, rerank_scores):
                        chunk["rerank_score"] = float(rerank_score)

                    retrieved_chunks.sort(key=lambda item: item.get("rerank_score", item.get("score", 0.0)), reverse=True)
                    elapsed = time.time() - rerank_start
                    logger.info(f"Reranking completed for {kb_id} in {elapsed:.3f}s with model {reranker_model}")
                finally:
                    await reranker.aclose()

            except Exception as exc:  # noqa: BLE001
                logger.error(f"Reranking failed: {exc}, falling back to vector scores")

            return retrieved_chunks[:final_top_k]

        except Exception as e:
            logger.error(f"Milvus query error: {e}, {traceback.format_exc()}")
            raise

    @classmethod
    def get_query_params_config(cls) -> dict:
        """获取 Milvus 知识库的查询参数配置"""
        return {"type": "milvus", "options": _retrieval_config_options()}

    # ==== 清理与运维 ====

    async def delete_file_chunks_only(
        self, kb_id: str, file_id: str, generation: int | None = None, *, update_file_stats: bool = True
    ) -> None:
        """删除指定代次的派生 Chunk；不传 generation 时删除文件全部代次。"""
        chunk_repo = KnowledgeChunkRepository()
        # 结构已写入但向量失败时 graph_indexed 仍为 false，也必须撤销其图谱贡献。
        await self.graph_service.delete_file_graph(kb_id, file_id, generation=generation)
        collection = await self._get_existing_milvus_collection(kb_id)

        if collection:
            try:
                await self._delete_file_chunks_from_milvus(collection, file_id, generation)
            except Exception as e:
                logger.error(f"Error checking file existence in Milvus: {e}")
                raise
        # 外部删除成功后再移除 chunk 事实，失败时保留可重试的元数据。
        if generation is None:
            await chunk_repo.delete_by_file_id(file_id)
        else:
            await chunk_repo.delete_by_file_id(file_id, generation=generation)
        if generation is None and update_file_stats:
            await KnowledgeFileRepository().update_fields(
                file_id=file_id,
                kb_id=kb_id,
                data={"chunk_count": 0, "token_count": 0},
            )

    async def cleanup_knowledge_base_resources(self, kb_id: str) -> dict:
        """清理知识库资源，同时删除 Milvus 集合。"""

        vector_store = await self.graph_service.get_graph_vector_store()

        def delete_milvus_collections() -> None:
            try:
                if utility.has_collection(kb_id, using=self.connection_alias):
                    utility.drop_collection(kb_id, using=self.connection_alias)
                    logger.info(f"Dropped Milvus collection for {kb_id}")
                else:
                    logger.info(f"Milvus collection {kb_id} does not exist, skipping")
            except Exception as e:
                logger.error(f"Failed to drop Milvus collection {kb_id}: {e}")
                raise

            vector_store.drop_graph_collections(kb_id)

        await await_io(asyncio.to_thread(delete_milvus_collections))

        return await super().cleanup_knowledge_base_resources(kb_id)

    # ==== 内部辅助：连接与集合管理 ====

    def _init_connection(self):
        """在本连接作用域内创建并选择目标数据库。"""
        connections.connect(alias=self.connection_alias, uri=self.milvus_uri, token=self.milvus_token)
        if self.milvus_db not in db.list_database(using=self.connection_alias):
            db.create_database(self.milvus_db, using=self.connection_alias)
        db.using_database(self.milvus_db, using=self.connection_alias)
        logger.info(f"Connected to Milvus at {self.milvus_uri}, database={self.milvus_db}")

    async def _get_or_create_milvus_collection(self, kb_id: str, embedding_model_spec: str | None):
        """获取或创建 Milvus 集合"""
        if kb_id in self.collections:
            return self.collections[kb_id]

        try:
            collection = await self._create_kb_instance(kb_id, embedding_model_spec)
            await self._initialize_kb_instance(collection)

            self.collections[kb_id] = collection
            return collection

        except KBEmbeddingModelMissingError:
            raise
        except Exception as e:
            logger.error(f"Failed to create Milvus collection for {kb_id}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    async def _get_existing_milvus_collection(self, kb_id: str) -> Collection | None:
        """获取已存在的集合，不因删除操作创建新集合。"""
        collection = self.collections.get(kb_id)
        if collection is not None:
            return collection

        def load_existing_collection() -> Collection | None:
            if not utility.has_collection(kb_id, using=self.connection_alias):
                return None
            return Collection(name=kb_id, using=self.connection_alias)

        return await asyncio.to_thread(load_existing_collection)

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None) -> Any:
        """在线程中创建或加载 Milvus 集合，避免阻塞 worker heartbeat。"""
        return await await_io(asyncio.to_thread(self._create_kb_instance_sync, kb_id, embedding_model_spec))

    def _create_kb_instance_sync(self, kb_id: str, embedding_model_spec: str | None) -> Any:
        """同步创建或加载 Milvus 集合。"""
        logger.info(f"Creating Milvus collection for {kb_id}")

        if not embedding_model_spec:
            raise KBEmbeddingModelMissingError()

        embedding_info = model_cache.get_model_info(embedding_model_spec)
        if not embedding_info or embedding_info.model_type != "embedding":
            raise ValueError(f"Unsupported embedding model: {embedding_model_spec}")

        collection_name = kb_id

        if not utility.has_collection(collection_name, using=self.connection_alias):
            return self._create_new_collection(collection_name, embedding_info, kb_id)

        collection = Collection(name=collection_name, using=self.connection_alias)
        expected_description = f"Knowledge base collection for {kb_id} using {embedding_info.model_id}"
        # 局部命名与导入的 embedding_field 构造函数区分开：这里检查已建集合的 schema。
        embedding_field_schema = next((field for field in collection.schema.fields if field.name == "embedding"), None)
        if (
            collection.description != expected_description
            or embedding_field_schema is None
            or embedding_field_schema.params.get("dim") != (embedding_info.dimension or 1024)
            or not self._collection_supports_text_retrieval(collection)
        ):
            raise RuntimeError(f"Collection {collection_name} does not match the knowledge base schema or model")
        return collection

    def _create_new_collection(self, collection_name: str, embedding_info: Any, kb_id: str) -> Collection:
        """创建新的 Milvus 集合"""
        embedding_dim = embedding_info.dimension or 1024
        model_name = embedding_info.model_id

        collection = create_collection(
            collection_name,
            fields=[
                *content_fields(),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                embedding_field(embedding_dim),
                FieldSchema(name="generation", dtype=DataType.INT64),
            ],
            description=f"Knowledge base collection for {kb_id} using {model_name}",
            using=self.connection_alias,
        )

        logger.info(f"Created new Milvus collection: {collection_name} '{model_name=}', {embedding_dim=}")

        return collection

    def _collection_supports_text_retrieval(self, collection: Collection) -> bool:
        """检查集合是否具备 BM25 与文本匹配所需的 schema。"""
        fields = {field.name: field for field in collection.schema.fields}
        content_field = fields.get("content")
        sparse_field = fields.get(CONTENT_SPARSE_FIELD)
        generation_field = fields.get("generation")
        if not generation_field or generation_field.dtype != DataType.INT64:
            return False
        if not content_field or content_field.dtype != DataType.VARCHAR:
            return False
        if not content_field.params.get("enable_analyzer") or not content_field.params.get("enable_match"):
            return False
        if not sparse_field or sparse_field.dtype != DataType.SPARSE_FLOAT_VECTOR:
            return False

        for function in collection.schema.functions:
            if (
                function.type == FunctionType.BM25
                and function.input_field_names == ["content"]
                and function.output_field_names == [CONTENT_SPARSE_FIELD]
            ):
                return True
        return False

    async def _initialize_kb_instance(self, instance: Any) -> None:
        """初始化 Milvus 集合（加载到内存）"""
        await asyncio.to_thread(instance.load)
        logger.info("Milvus collection loaded into memory")

    # ==== 内部辅助：文件索引写入与删除 ====

    async def _index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """索引已解析文件（状态 INDEXING → INDEXED/ERROR_INDEXING）。"""
        await documents.load_file_meta(kb_id, file_id)
        # 在投影锁内先检查 PG 可见事实，删除后不能重新创建外部集合。
        collection = await self._get_or_create_milvus_collection(kb_id, embedding_model_spec)
        if not collection:
            raise ValueError(f"Failed to get Milvus collection for {kb_id}")

        embedding_function = get_embedding_function(embedding_model_spec)

        allowed_statuses = {
            FileStatus.PARSED,
            FileStatus.ERROR_INDEXING,
            FileStatus.INDEXED,
        }
        file_repo = KnowledgeFileRepository()
        owner_filter = (
            {"processing_job_id": processing_job_id, "processing_owner": processing_owner}
            if processing_job_id is not None and processing_owner is not None
            else {}
        )

        claim_data = {
            "status": FileStatus.INDEXING,
            "error_message": None,
            "processing_job_id": processing_job_id,
            "processing_owner": processing_owner,
        }
        if operator_id:
            claim_data["updated_by"] = operator_id

        claimed_record = await file_repo.update_fields_if_status(
            kb_id=kb_id,
            file_id=file_id,
            allowed_statuses=allowed_statuses,
            data=claim_data,
            processing_params=params,
            kb_additional_params=additional_params,
        )
        if claimed_record is None:
            current_meta = await documents.load_file_meta(kb_id, file_id)
            current_status = current_meta.get("status")
            raise ValueError(
                f"Cannot index file with status '{current_status}'. File must be parsed first "
                f"(status should be one of: {', '.join(allowed_statuses)})"
            )

        file_meta = documents.file_record_to_meta(claimed_record)
        params = dict(file_meta.get("processing_params") or {})
        if not file_meta.get("markdown_file"):
            reset_data = {
                "status": FileStatus.UPLOADED,
                "error_message": None,
                "processing_job_id": None,
                "processing_owner": None,
            }
            if operator_id:
                reset_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                kb_id=kb_id,
                file_id=file_id,
                allowed_statuses={FileStatus.INDEXING},
                data=reset_data,
                **owner_filter,
            )
            if updated_record is None and processing_owner is not None:
                raise asyncio.CancelledError("File processing owner was lost")
            raise ValueError("File has not been parsed yet (no markdown_file)")

        try:
            generation = await file_repo.begin_index_generation(kb_id=kb_id, file_id=file_id, **owner_filter)
            file_meta["generation"] = generation
            chunk_parser_config = dict(params.get("chunk_parser_config") or {})
            chunk_parser_config.setdefault("embed_model_id", (await system_options.get())["embed_model"])
            params["chunk_parser_config"] = chunk_parser_config
            markdown_content = await documents.read_markdown_from_minio(file_meta["markdown_file"])
            filename = file_meta.get("filename")

            chunks = self._split_text_into_chunks(markdown_content, file_id, filename, params)
            for chunk in chunks:
                chunk["generation"] = generation
                chunk["chunk_id"] = f"{chunk['chunk_id'][:80]}-g{generation}"[:100]
            logger.info(
                f"Split {filename} into {len(chunks)} chunks with params: "
                f"chunk_preset_id={params.get('chunk_preset_id')}, "
                f"chunk_parser_config={params.get('chunk_parser_config')}"
            )

            chunk_stats = self._calculate_chunk_stats(chunks)
            await self._embed_and_store_chunks(kb_id, file_id, collection, chunks, embedding_function)

            logger.info(f"Indexed file {file_id} generation {generation} into Milvus")

            updated_record = await file_repo.finish_index_generation(
                kb_id=kb_id,
                file_id=file_id,
                generation=generation,
                **chunk_stats,
                **owner_filter,
            )
            return documents.file_record_to_meta(updated_record)

        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            error_msg = "File indexing was cancelled" if isinstance(e, asyncio.CancelledError) else str(e)
            logger.error(f"Indexing failed for {file_id}: {error_msg}")
            update_data = {
                "status": FileStatus.ERROR_INDEXING,
                "error_message": error_msg,
                "processing_job_id": None,
                "processing_owner": None,
                "projection_status": "failed",
                "projection_error": error_msg,
            }
            if operator_id:
                update_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                file_id=file_id,
                kb_id=kb_id,
                allowed_statuses={FileStatus.INDEXING},
                data=update_data,
                **owner_filter,
            )
            if updated_record is None and processing_owner is not None:
                raise asyncio.CancelledError("File processing owner was lost")
            raise

    def _split_text_into_chunks(self, text: str, file_id: str, filename: str, params: dict) -> list[IngestionChunk]:
        """将文本分割成块"""
        return chunk_markdown(text, file_id, filename, params)

    def _calculate_chunk_stats(self, chunks: list[IngestionChunk]) -> dict[str, int]:
        """统计 chunk 数量与 token 总数。"""
        return {
            "chunk_count": len(chunks),
            "token_count": sum(count_tokens(chunk["content"]) for chunk in chunks),
        }

    async def _embed_and_store_chunks(
        self,
        kb_id: str,
        file_id: str,
        collection: Collection,
        chunks: list[IngestionChunk],
        embedding_function,
        *,
        chunk_batch_size: int = MILVUS_CHUNK_EMBED_BATCH_SIZE,
    ) -> None:
        """对 chunks 进行分批嵌入并存储到 Milvus 和 PostgreSQL"""
        if not chunks:
            return

        chunk_batch_size = max(int(chunk_batch_size), 1)
        for start in range(0, len(chunks), chunk_batch_size):
            batch_chunks = chunks[start : start + chunk_batch_size]
            texts = [chunk["content"] for chunk in batch_chunks]
            embeddings = await embedding_function(texts)
            await self._insert_chunks_to_stores(
                kb_id,
                file_id,
                collection,
                batch_chunks,
                embeddings,
            )

    def _build_chunk_pg_records(self, kb_id: str, chunks: list[IngestionChunk]) -> list[dict[str, Any]]:
        """把 chunk 列表映射为 PG batch_upsert 记录。"""
        return [
            {
                "chunk_id": chunk["chunk_id"],
                "file_id": chunk["file_id"],
                "kb_id": kb_id,
                "chunk_index": chunk["chunk_index"],
                "generation": int(chunk.get("generation", 1) or 1),
                "content": chunk["content"],
                "start_line": chunk.get("start_line"),
                "end_line": chunk.get("end_line"),
                "start_char_pos": chunk.get("start_char_pos"),
                "end_char_pos": chunk.get("end_char_pos"),
                "start_token_pos": chunk.get("start_token_pos"),
                "end_token_pos": chunk.get("end_token_pos"),
                "graph_indexed": bool(chunk.get("graph_indexed", False)),
                "ent_ids": chunk.get("ent_ids"),
                "tags": chunk.get("tags"),
                "extraction_result": chunk.get("extraction_result"),
            }
            for chunk in chunks
        ]

    async def _insert_chunks_to_stores(
        self,
        kb_id: str,
        file_id: str,
        collection: Collection,
        chunks: list[IngestionChunk],
        embeddings: list,
    ) -> None:
        """先落 PG 权威事实，再把派生向量 upsert 进 Milvus。"""
        if not chunks:
            return

        entities = [
            [chunk["chunk_id"] for chunk in chunks],
            [chunk["content"] for chunk in chunks],
            [chunk["chunk_id"] for chunk in chunks],
            [chunk["file_id"] for chunk in chunks],
            [chunk["chunk_index"] for chunk in chunks],
            embeddings,
            [int(chunk.get("generation", 1) or 1) for chunk in chunks],
        ]
        chunk_repo = KnowledgeChunkRepository()

        def _upsert_milvus_records():
            collection.upsert(entities)

        # PG 为权威源，先落 chunk 事实；Milvus 只是派生索引，失败不回滚 PG。
        await chunk_repo.batch_upsert(self._build_chunk_pg_records(kb_id, chunks))

        last_error: Exception | None = None
        for attempt in range(MILVUS_CHUNK_UPSERT_ATTEMPTS):
            try:
                await await_io(asyncio.to_thread(_upsert_milvus_records))
                return
            except Exception as e:
                last_error = e
                logger.warning(f"Milvus chunk upsert failed for file {file_id} (attempt {attempt + 1}/{MILVUS_CHUNK_UPSERT_ATTEMPTS}): {e}")
                if attempt + 1 < MILVUS_CHUNK_UPSERT_ATTEMPTS:
                    await asyncio.sleep(MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS)

        assert last_error is not None
        raise last_error

    async def _delete_file_chunks_from_milvus(self, collection: Collection, file_id: str, generation: int | None = None) -> None:
        """删除文件在 Milvus 中的派生向量；文件不存在时跳过删除。"""
        expr = f'file_id == "{file_id}"'
        if generation is not None:
            expr += f" and generation == {int(generation)}"

        def delete_from_milvus() -> bool:
            results = collection.query(expr=expr, output_fields=["id"], limit=1)
            if not results:
                return False
            collection.delete(expr)
            return True

        if await await_io(asyncio.to_thread(delete_from_milvus)):
            logger.info(f"Deleted chunks for file {file_id} from Milvus")
        else:
            logger.info(f"File {file_id} not found in Milvus, skipping delete operation")

    # ==== 内部辅助：检索过滤与结果装配 ====

    async def _build_active_generation_expr(self, kb_id: str) -> str:
        """先过滤非服务代次，避免不可见候选占满 Milvus top-k。"""
        grouped: dict[int, list[str]] = {}
        for file_id, generation in await KnowledgeFileRepository().list_active_generations(kb_id):
            grouped.setdefault(generation, []).append(file_id)
        return (
            " or ".join(f"(generation == {generation} and file_id in {json.dumps(file_ids)})" for generation, file_ids in grouped.items())
            or "file_id in []"
        )

    async def _build_file_name_expr(self, kb_id: str, file_name: str | None) -> str | None:
        """把文件名筛选转换为 file_id 过滤表达式；无匹配时返回恒假表达式。"""
        if not file_name:
            return None

        matched_file_ids = await KnowledgeFileRepository().list_file_ids_by_filename_contains(
            kb_id=kb_id,
            filename_pattern=file_name,
        )
        if not matched_file_ids:
            return 'file_id == "__no_matching_file__"'
        escaped_ids = [file_id.replace('"', '\\"') for file_id in matched_file_ids]
        if len(escaped_ids) == 1:
            return f'file_id == "{escaped_ids[0]}"'
        joined_ids = '", "'.join(escaped_ids)
        return f'file_id in ["{joined_ids}"]'

    def _build_text_search(
        self, options: dict[str, Any], search_mode: str, query_text: str = ""
    ) -> tuple[str | None, LexicalHighlighter | None]:
        """将文本条件转换为安全的过滤表达式与服务端高亮配置。"""
        text_options = {}
        for key in ("required_terms", "excluded_terms", "exact_phrase"):
            value = options.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            text_options[key] = value.strip()

        clauses = []
        highlight_query = []
        for term in text_options["required_terms"].split():
            clauses.append(f"TEXT_MATCH(content, {json.dumps(term, ensure_ascii=False)})")
            highlight_query.append({"type": "TextMatch", "field": "content", "text": term})
        for term in text_options["excluded_terms"].split():
            clauses.append(f"not TEXT_MATCH(content, {json.dumps(term, ensure_ascii=False)})")
        if phrase := text_options["exact_phrase"]:
            clauses.append(f"PHRASE_MATCH(content, {json.dumps(phrase, ensure_ascii=False)}, 0)")
            # 高亮协议仅支持 TextMatch；短语是否命中由上面的 PHRASE_MATCH 决定。
            highlight_query.append({"type": "TextMatch", "field": "content", "text": phrase})

        if search_mode == "hybrid" and query_text:
            highlight_query.append({"type": "TextMatch", "field": "content", "text": query_text})

        highlighter = None
        highlight_results = options.get("highlight_results", True)
        if not isinstance(highlight_results, bool):
            raise ValueError("highlight_results must be a boolean")
        if highlight_results and (search_mode != "vector" or highlight_query):
            highlighter = LexicalHighlighter(
                pre_tags=["\ue000"],
                post_tags=["\ue001"],
                highlight_search_text=search_mode == "keyword",
                highlight_query=highlight_query,
                fragment_offset=40,
                fragment_size=200,
                num_of_fragments=3,
            )
        return " and ".join(clauses) or None, highlighter

    def _build_chunk_from_hit(
        self,
        hit: Any,
        score: float,
        include_distances: bool,
        score_field: str | None = None,
    ) -> dict:
        """将 Milvus Hit 转成知识库统一返回的 Chunk 结构。"""
        entity = hit.entity
        file_id = entity.get("file_id")
        metadata = {
            "source": "未知来源",
            "chunk_id": entity.get("chunk_id"),
            "file_id": file_id,
            "generation": entity.get("generation", 1),
            "chunk_index": entity.get("chunk_index"),
        }
        chunk = {"content": entity.get("content", ""), "metadata": metadata, "score": float(score or 0.0)}
        chunk["score_type"] = {"bm25_score": "bm25", "hybrid_score": "hybrid"}.get(score_field, "cosine")
        highlight = getattr(hit, "highlight", None)
        if highlight and (fragments := highlight.get("content", {}).get("fragments")):
            chunk["highlights"] = [
                [
                    {"text": part[1:-1] if part.startswith("\ue000") else part, "matched": part.startswith("\ue000")}
                    for part in re.split(r"(\ue000.*?\ue001)", fragment, flags=re.DOTALL)
                    if part
                ]
                for fragment in fragments
            ]
        if score_field:
            chunk[score_field] = float(score or 0.0)
        if include_distances:
            chunk["distance"] = hit.distance
        return chunk

    async def _hydrate_chunk_sources(self, kb_id: str, chunks: list[dict]) -> list[dict]:
        """补充 chunk 来源文件名和总分片数，并过滤已从 PG 删除的孤儿 chunk。

        Milvus 中可能残留已删除文件的向量（如删除流程中途失败），
        检索侧依据 PG 中 file_id 是否存在进行过滤，防止已删除内容被返回。
        """
        file_ids = sorted({str(file_id) for chunk in chunks if (file_id := (chunk.get("metadata") or {}).get("file_id"))})
        if not file_ids:
            return []

        sources = await KnowledgeFileRepository().get_chunk_sources_by_file_ids(kb_id=kb_id, file_ids=file_ids)
        chunk_ids = [str((chunk.get("metadata") or {}).get("chunk_id") or "") for chunk in chunks]
        records = await KnowledgeChunkRepository().list_by_chunk_ids(chunk_ids)
        records_by_id = {record.chunk_id: record for record in records if record.kb_id == kb_id}
        live_chunks: list[dict] = []
        for chunk in chunks:
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict):
                continue
            file_id = str(metadata.get("file_id") or "")
            source = sources.get(file_id)
            if source is None:
                continue
            record = records_by_id.get(str(metadata.get("chunk_id") or ""))
            if record is None or record.file_id != file_id or record.generation != source["active_generation"]:
                continue
            chunk["content"] = record.content
            metadata.update(source)
            metadata["generation"] = record.generation
            metadata["chunk_index"] = record.chunk_index
            metadata["start_line"] = record.start_line
            metadata["end_line"] = record.end_line
            live_chunks.append(chunk)
        return live_chunks

    # ==== 内部辅助：图检索与排名融合 ====

    async def _retrieve_graph_chunks(
        self,
        query_text: str,
        kb_id: str,
        base_chunks: list[dict],
        query_params: dict[str, Any],
        embedding_model_spec: str | None,
    ) -> list[dict]:
        """图扩散召回候选 chunk；失败降级为空结果，不阻断主检索。"""
        try:
            if not embedding_model_spec:
                return []

            entity_top_k = min(
                max(int(query_params.get("graph_entity_top_k", 10)), 1),
                self.MAX_GRAPH_ENTITY_TOP_K,
            )
            triple_top_k = min(
                max(int(query_params.get("graph_triple_top_k", 10)), 1),
                self.MAX_GRAPH_TRIPLE_TOP_K,
            )
            graph_top_k = min(max(int(query_params.get("graph_top_k", 20)), 1), self.MAX_GRAPH_TOP_K)
            graph_max_nodes = min(max(int(query_params.get("graph_max_nodes", 10000)), 1), self.MAX_GRAPH_NODES)

            vector_store = await self.graph_service.get_graph_vector_store()
            entity_hits, triple_hits = await asyncio.gather(
                vector_store.search_entities(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=entity_top_k,
                ),
                vector_store.search_triples(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=triple_top_k,
                ),
            )
            seed_weights = await self._build_graph_seed_weights(kb_id, base_chunks, entity_hits, triple_hits)
            if not seed_weights:
                return []

            graph_scores = await self.graph_service.query_and_rank_chunks_by_ppr(
                kb_id,
                seed_weights,
                max_nodes=graph_max_nodes,
                top_k=graph_top_k,
                damping=float(query_params.get("ppr_damping", 0.85)),
            )
            if not graph_scores:
                return []

            chunks = await KnowledgeChunkRepository().list_by_chunk_ids([chunk_id for chunk_id, _ in graph_scores])
            score_by_chunk_id = dict(graph_scores)
            return [self._build_chunk_from_record(chunk, score_by_chunk_id[chunk.chunk_id], score_field="graph_score") for chunk in chunks]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Graph retrieval failed for {kb_id}: {exc}")
            return []

    async def _build_graph_seed_weights(
        self,
        kb_id: str,
        base_chunks: list[dict],
        entity_hits: list[dict[str, Any]],
        triple_hits: list[dict[str, Any]],
    ) -> dict[str, float]:
        """由实体、三元组与已召回 chunk 构建归一化的 PPR 种子权重。"""
        seed_weights: dict[str, float] = {}

        def add_seed(entity_id: str | None, score: float, weight: float) -> None:
            if not entity_id:
                return
            seed_weights[entity_id] = seed_weights.get(entity_id, 0.0) + max(float(score or 0.0), 0.0) * weight

        for hit in entity_hits:
            add_seed(hit.get("id"), hit.get("score", 0.0), 1.0)

        for hit in triple_hits:
            score = float(hit.get("score") or 0.0)
            add_seed(hit.get("source_id"), score, 0.8)
            add_seed(hit.get("target_id"), score, 0.8)

        chunk_scores = {
            chunk.get("metadata", {}).get("chunk_id"): float(chunk.get("score") or 0.0)
            for chunk in base_chunks
            if chunk.get("metadata", {}).get("chunk_id")
        }
        if chunk_scores:
            chunks = await KnowledgeChunkRepository().list_by_chunk_ids(list(chunk_scores))
            for chunk in chunks:
                for entity_id in chunk.ent_ids or []:
                    add_seed(entity_id, chunk_scores.get(chunk.chunk_id, 0.0), 0.3)

        total = sum(seed_weights.values())
        if total <= 0:
            return {}
        return {entity_id: weight / total for entity_id, weight in seed_weights.items()}

    def _build_chunk_from_record(self, chunk: Any, score: float, score_field: str | None = None) -> dict:
        """将 PG chunk 记录转成统一返回的 Chunk 结构。"""
        metadata = {
            "source": "未知来源",
            "chunk_id": chunk.chunk_id,
            "file_id": chunk.file_id,
            "chunk_index": chunk.chunk_index,
        }
        result = {"content": chunk.content, "metadata": metadata, "score": float(score or 0.0)}
        result["score_type"] = "graph"
        if score_field:
            result[score_field] = float(score or 0.0)
        return result

    def _fuse_chunk_rankings(
        self,
        base_chunks: list[dict],
        graph_chunks: list[dict],
        graph_weight: float,
    ) -> list[dict]:
        """按 RRF 融合基础召回与图召回的排名。"""
        fused: dict[str, dict[str, Any]] = {}
        rrf_k = 60.0

        def merge_chunk(chunk: dict, rank: int, weight: float, source: str) -> None:
            chunk_id = chunk.get("metadata", {}).get("chunk_id")
            if not chunk_id:
                return
            score = weight / (rrf_k + rank)
            existing = fused.get(chunk_id)
            if existing is None:
                existing = {**chunk, "fusion_score": 0.0, "fusion_sources": []}
                fused[chunk_id] = existing
            existing["fusion_score"] += score
            existing["score"] = existing["fusion_score"]
            existing["score_type"] = "fusion"
            existing["fusion_sources"].append(source)
            if source == "graph" and "graph_score" in chunk:
                existing["graph_score"] = chunk["graph_score"]

        for rank, chunk in enumerate(base_chunks, start=1):
            merge_chunk(chunk, rank, 1.0, "chunk")
        for rank, chunk in enumerate(graph_chunks, start=1):
            merge_chunk(chunk, rank, max(graph_weight, 0.0), "graph")

        return sorted(fused.values(), key=lambda item: item.get("fusion_score", 0.0), reverse=True)
