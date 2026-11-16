"""原始像素视觉向量的 Milvus 投影；PostgreSQL 媒体档案决定可见版本。"""

import asyncio
import hashlib
import json
import os

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, MilvusException, connections, db, utility

from yuxi.infrastructure.filesystem import await_io
from yuxi.modules.knowledge.storage.offload import run_milvus_query_io
from yuxi.shared.errors import InvalidOperationError


class MediaVectorStore:
    """集合按知识库和模型分离，过滤只能来自当前授权的媒体修订。"""

    def __init__(self):
        uri = os.getenv("MILVUS_URI") or "http://localhost:19530"
        self.alias = "media_" + hashlib.sha256(uri.encode()).hexdigest()[:12]
        if not connections.has_connection(self.alias):
            connections.connect(alias=self.alias, uri=uri, token=os.getenv("MILVUS_TOKEN") or "")
        database = os.getenv("MILVUS_DB") or "yuxi"
        if database not in db.list_database(using=self.alias):
            db.create_database(database, using=self.alias)
        db.using_database(database, using=self.alias)

    async def write(self, kb_id: str, model: str, revision: str, file_id: str, assets: list[dict], embeddings: list[list[float]]) -> None:
        """一次构建写独立修订；发布前不可被当前搜索消费。"""
        await await_io(asyncio.to_thread(self._write, kb_id, model, revision, file_id, assets, embeddings))

    async def search(self, kb_id: str, model: str, revisions: list[str], vector: list[float], limit: int) -> list[dict]:
        """空修订集合不调用 Milvus，缺失的已发布集合明确失败。"""
        if not revisions:
            return []
        return await run_milvus_query_io(self._search, kb_id, model, revisions, vector, limit)

    async def verify(self, kb_id: str, model: str, revision: str, file_id: str, assets: list[dict], dimension: int) -> None:
        """独立回读实际向量记录，完整投影是 PostgreSQL 发布的前置条件。"""
        await run_milvus_query_io(self._verify, kb_id, model, revision, file_id, assets, dimension)

    def cleanup(self, kb_id: str, file_id: str | None = None, revision: str | None = None) -> None:
        """供持有知识投影排他锁的清理器删除库或文件视觉投影。"""
        prefix = collection_prefix(kb_id)
        for name in utility.list_collections(using=self.alias):
            if not name.startswith(prefix):
                continue
            if file_id is None and revision is None:
                utility.drop_collection(name, using=self.alias)
            else:
                collection = Collection(name, using=self.alias)
                filters = []
                if file_id is not None:
                    filters.append(f"file_id == {json.dumps(file_id)}")
                if revision is not None:
                    filters.append(f"revision == {json.dumps(revision)}")
                collection.delete(expr=" and ".join(filters))
                collection.flush()

    def _write(self, kb_id, model, revision, file_id, assets, embeddings):
        """同步 SDK 调用完成后才允许媒体档案发布。"""
        if not assets or len(assets) != len(embeddings):
            raise ValueError("视觉资产与向量数量不匹配")
        name = collection_name(kb_id, model, len(embeddings[0]))
        if not utility.has_collection(name, using=self.alias):
            schema = CollectionSchema(
                fields=[
                    FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
                    FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="revision", dtype=DataType.VARCHAR, max_length=80),
                    FieldSchema(name="asset_id", dtype=DataType.VARCHAR, max_length=100),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=len(embeddings[0])),
                ]
            )
            try:
                collection = Collection(name, schema=schema, using=self.alias)
            except MilvusException:
                if not utility.has_collection(name, using=self.alias):
                    raise
                collection = Collection(name, using=self.alias)
            collection.create_index("embedding", {"metric_type": "COSINE", "index_type": "FLAT", "params": {}})
        collection = Collection(name, using=self.alias)
        rows = []
        for asset, embedding in zip(assets, embeddings, strict=True):
            rows.append(
                {
                    "id": hashlib.sha256(f"{revision}:{asset['asset_id']}".encode()).hexdigest(),
                    "file_id": file_id,
                    "revision": revision,
                    "asset_id": asset["asset_id"],
                    "embedding": embedding,
                }
            )
        collection.upsert(rows)
        collection.flush()
        self._verify(kb_id, model, revision, file_id, assets, len(embeddings[0]))

    def _verify(self, kb_id, model, revision, file_id, assets, dimension):
        """读取 Strong consistency 的向量与身份，不能只信写入返回码。"""
        name = collection_name(kb_id, model, dimension)
        if not utility.has_collection(name, using=self.alias):
            raise InvalidOperationError("媒体视觉索引尚未完整写入", code="media_index_incomplete")
        collection = Collection(name, using=self.alias)
        collection.load()
        rows = collection.query(
            expr=f"revision == {json.dumps(revision)}", output_fields=["asset_id", "file_id", "embedding"], consistency_level="Strong"
        )
        if (
            {row["asset_id"] for row in rows} != {asset["asset_id"] for asset in assets}
            or len(rows) != len(assets)
            or any(row["file_id"] != file_id or len(row["embedding"]) != dimension for row in rows)
        ):
            raise InvalidOperationError("媒体视觉索引数量或身份不完整", code="media_index_incomplete")

    def _search(self, kb_id, model, revisions, vector, limit):
        """过滤修订来自 PostgreSQL，不能直接使用模型提供的表达式。"""
        name = collection_name(kb_id, model, len(vector))
        if not utility.has_collection(name, using=self.alias):
            raise InvalidOperationError("已发布媒体的视觉索引缺失，请重新构建", code="media_index_missing")
        collection = Collection(name, using=self.alias)
        collection.load()
        hits = collection.search(
            data=[vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=limit,
            expr=f"revision in {json.dumps(revisions)}",
            output_fields=["file_id", "revision", "asset_id"],
            consistency_level="Strong",
        )
        return [
            {
                "file_id": hit.entity.get("file_id"),
                "revision": hit.entity.get("revision"),
                "asset_id": hit.entity.get("asset_id"),
                "score": float(hit.distance),
            }
            for hit in hits[0]
        ]


def collection_prefix(kb_id: str) -> str:
    """知识库集合前缀不包含用户提供的 Milvus 标识符。"""
    return "native_media_" + hashlib.sha256(kb_id.encode()).hexdigest()[:20] + "_"


def collection_name(kb_id: str, model: str, dimension: int) -> str:
    """模型与维度属于索引契约，变化时生成新集合。"""
    return collection_prefix(kb_id) + hashlib.sha256(f"{model}:{dimension}".encode()).hexdigest()[:20]


async def cleanup_media_projection(kb_id: str, file_id: str | None = None, revision: str | None = None) -> None:
    """已持有投影锁的回收用例可按任一事实边界删除，无需模型配置。"""

    def cleanup():
        """连接与同步删除共同卸载，取消等待外部副作用完成。"""
        MediaVectorStore().cleanup(kb_id, file_id, revision)

    await await_io(asyncio.to_thread(cleanup))
