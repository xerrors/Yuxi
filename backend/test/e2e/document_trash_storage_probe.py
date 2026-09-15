"""Isolated real PG/MinIO/Neo4j/Milvus purge evidence; only synthetic fixture IDs."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from sqlalchemy import select, update, func
from pymilvus import Collection, CollectionSchema, FieldSchema, DataType
from neo4j import GraphDatabase

# Explicit opt-in: this probe seeds and deletes only a dedicated disposable stack.
from sqlalchemy.engine import make_url

if os.environ.get("TEST_ALLOW_TRASH_STORAGE") != "1":
    raise RuntimeError("TEST_ALLOW_TRASH_STORAGE=1 is required for disposable storage tests")
if make_url(os.environ["POSTGRES_URL"]).database != "trash":
    raise RuntimeError("The disposable PostgreSQL database must be named trash")

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBase,
    KnowledgeFile,
    KnowledgeChunk,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
)
from yuxi.storage.minio.client import get_minio_client, MinIOClient
from yuxi.services.document_trash_service import DocumentTrashService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.utils.datetime_utils import utc_now

SUFFIX = os.environ.get("PROBE_SUFFIX", "")
if not SUFFIX or not SUFFIX.replace("_", "").isalnum() or len(SUFFIX) > 16:
    raise ValueError("PROBE_SUFFIX must be a nonempty short alphanumeric fixture suffix")
KB = "trash_storage_fixture" + SUFFIX
A = "purge_probe_A" + SUFFIX
B = "purge_probe_B" + SUFFIX
E = "purge_probe_exclusive" + SUFFIX
S = "purge_probe_shared" + SUFFIX
C = {A: "purge_probe_chunk_A" + SUFFIX, B: "purge_probe_chunk_B" + SUFFIX}
client = get_minio_client()
buckets = MinIOClient.KB_BUCKETS
service = DocumentTrashService()


def neo():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
    )


def create_collection(name, alias, rows, with_file=False):
    fields = [
        FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=128),
        FieldSchema("vector", DataType.FLOAT_VECTOR, dim=2),
    ]
    if with_file:
        fields.append(FieldSchema("file_id", DataType.VARCHAR, max_length=64))
    col = Collection(name, CollectionSchema(fields), using=alias, consistency_level="Strong")
    col.insert(rows)
    col.create_index("vector", {"index_type": "FLAT", "metric_type": "L2", "params": {}})
    col.flush()
    col.load()
    return col


async def due(fid):
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            update(KnowledgeFile)
            .where(KnowledgeFile.file_id == fid)
            .values(purge_after=utc_now() - timedelta(days=1), purge_lease_until=None)
        )


async def setup():
    async with pg_manager.get_async_session_context() as db:
        db.add(KnowledgeBase(kb_id=KB, name="isolated purge probe", kb_type="milvus", additional_params={}))
        await db.flush()
        for fid in [A, B]:
            db.add(
                KnowledgeFile(
                    file_id=fid,
                    kb_id=KB,
                    filename=fid + ".md",
                    status="done",
                    path=f"minio://{buckets['documents']}/{KB}/shared-upload.md",
                    markdown_file=f"minio://{buckets['parsed']}/{KB}/parsed/{fid}.md",
                )
            )
        await db.flush()
        for fid in [A, B]:
            db.add(
                KnowledgeChunk(chunk_id=C[fid], file_id=fid, kb_id=KB, chunk_index=0, content=fid, graph_indexed=True)
            )
        db.add_all(
            [
                KnowledgeGraphEntity(
                    entity_id=e, kb_id=KB, normalized_name=e, label="Entity", name=e, vector_status="indexed"
                )
                for e in [E, S]
            ]
        )
        await db.flush()
        for fid, e in [(A, E), (A, S), (B, S)]:
            db.add(KnowledgeGraphEntityMention(entity_id=e, kb_id=KB, file_id=fid, chunk_id=C[fid]))
    for fid in [A, B]:
        await client.aupload_file(
            buckets["parsed"],
            f"{KB}/parsed/{fid}.md",
            f"![shared](kb-images/shared%20image.png) ![own](kb-images/{fid}/own%20image.png)".encode(),
        )
        await client.aupload_file(buckets["parsed"], f"{KB}/preview/{fid}.pdf", b"preview")
        await client.aupload_file(buckets["images"], f"{KB}/kb-images/{fid}/own image.png", b"own")
    await client.aupload_file(buckets["documents"], f"{KB}/shared-upload.md", b"shared upload")
    await client.aupload_file(buckets["images"], f"{KB}/kb-images/shared image.png", b"shared image")
    with neo() as driver, driver.session() as session:
        for fid in [A, B]:
            session.run(
                f"CREATE (c:Chunk:MilvusKB:`{KB}` {{kb_id:$kb,file_id:$fid,chunk_id:$cid,content_preview:$fid}})",
                kb=KB,
                fid=fid,
                cid=C[fid],
            ).consume()
        for e in [E, S]:
            session.run(
                f"CREATE (e:Entity:MilvusKB:`{KB}` {{kb_id:$kb,entity_id:$eid,normalized_name:$eid,"
                'name:$eid,label:"Entity",attributes:"private"})',
                kb=KB,
                eid=e,
            ).consume()
        for fid, e in [(A, E), (A, S), (B, S)]:
            session.run(
                f"MATCH(c:Chunk:`{KB}` {{file_id:$fid}}),(e:Entity:`{KB}` {{entity_id:$eid}}) "
                "CREATE (c)-[:MENTIONS {file_id:$fid,kb_id:$kb}]->(e)",
                fid=fid,
                eid=e,
                kb=KB,
            ).consume()
    executor = await knowledge_base.get_kb_executor(KB)
    vectors = MilvusGraphVectorStore()
    create_collection(
        KB, executor.connection_alias, [{"id": C[fid], "file_id": fid, "vector": [1.0, 0.0]} for fid in [A, B]], True
    )
    create_collection(KB + "_entity", vectors.connection_alias, [{"id": e, "vector": [1.0, 0.0]} for e in [E, S]])
    await service.trash(KB, [A], deleted_by="fixture")
    await check_reads()


async def check_reads():
    # Real graph reads after trash hide A, preserve B and shared entity.
    graph = MilvusGraphService()
    data = await graph.query_nodes(KB, max_depth=1, max_nodes=20)
    ids = {n["properties"].get("entity_id") for n in data["nodes"]}
    assert E not in ids and S in ids, data
    assert all(n["properties"].get("file_id") != A for n in data["nodes"])
    assert all("attributes" not in n["properties"] for n in data["nodes"] if n["properties"].get("entity_id") == S)
    stats = await graph.get_stats(KB)
    assert stats["total_nodes"] == 2 and stats["total_edges"] == 1, stats
    assert "Entity" in await graph.get_labels(KB)
    await due(A)
    print(
        json.dumps(
            {
                "stage": "setup",
                "real_graph_visible_nodes": stats["total_nodes"],
                "real_graph_visible_edges": stats["total_edges"],
                "status": "passed",
            }
        )
    )


async def failed():
    rows = await service.repository.claim_due(limit=1)
    assert len(rows) == 1 and rows[0].file_id == A
    row = rows[0]
    try:
        await asyncio.wait_for(service._purge_one(row), timeout=100)
    except Exception as exc:
        await service.repository.fail_purge(row.file_id, row.deletion_id, row.purge_token, type(exc).__name__)
    else:
        raise AssertionError("Expected actual Neo4j outage to stop purge")
    async with pg_manager.get_async_session_context() as db:
        saved = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == A))
        assert saved is not None and saved.purge_error and saved.purge_objects
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.file_id == A)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(KnowledgeGraphEntityMention)
                .where(KnowledgeGraphEntityMention.file_id == A)
            )
            == 2
        )
    assert client.file_exists(buckets["parsed"], f"{KB}/parsed/{A}.md")
    print(
        json.dumps(
            {
                "stage": "real_neo4j_outage",
                "pg_file_retained": True,
                "pg_chunks_retained": True,
                "pg_graph_refs_rolled_back": True,
                "minio_retained": True,
                "status": "passed",
            }
        )
    )


async def verify_after(fid, last=False):
    async with pg_manager.get_async_session_context() as db:
        assert await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == fid)) is None
        assert (
            await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.file_id == fid)) == 0
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(KnowledgeGraphEntityMention)
                .where(KnowledgeGraphEntityMention.file_id == fid)
            )
            == 0
        )
    executor = await knowledge_base.get_kb_executor(KB)
    vectors = MilvusGraphVectorStore()
    col = Collection(KB, using=executor.connection_alias)
    col.load()
    assert col.query(expr=f'file_id == "{fid}"', output_fields=["id"], consistency_level="Strong") == []
    ent = Collection(KB + "_entity", using=vectors.connection_alias)
    ent.load()
    ids = {r["id"] for r in ent.query(expr='id != ""', output_fields=["id"], consistency_level="Strong")}
    assert E not in ids
    assert (S in ids) == (not last), ids
    with neo() as driver, driver.session() as session:
        assert session.run(f"MATCH(c:Chunk:`{KB}` {{file_id:$fid}}) RETURN count(c) AS n", fid=fid).single()["n"] == 0
        assert session.run(f"MATCH(e:Entity:`{KB}` {{entity_id:$eid}}) RETURN count(e) AS n", eid=E).single()["n"] == 0
        shared = session.run(f"MATCH(e:Entity:`{KB}` {{entity_id:$eid}}) RETURN properties(e) AS p", eid=S).single()
        assert bool(shared) == (not last)
        if shared:
            assert "attributes" not in shared["p"]
    assert not client.file_exists(buckets["parsed"], f"{KB}/parsed/{fid}.md")
    assert not client.file_exists(buckets["parsed"], f"{KB}/preview/{fid}.pdf")
    assert not client.file_exists(buckets["images"], f"{KB}/kb-images/{fid}/own image.png")
    assert client.file_exists(buckets["images"], f"{KB}/kb-images/shared image.png") == (not last)
    assert client.file_exists(buckets["documents"], f"{KB}/shared-upload.md") == (not last)


async def retry():
    await due(A)
    result = await service.purge_due(limit=1)
    assert result == {"completed": 1, "failed": 0}, result
    await verify_after(A)
    print(
        json.dumps(
            {
                "stage": "retry_after_real_outage",
                "all_four_stores_cleaned": True,
                "shared_upload_image_entity_retained": True,
                "status": "passed",
            }
        )
    )
    await service.trash(KB, [B], deleted_by="fixture")
    await due(B)
    result = await service.purge_due(limit=1)
    assert result == {"completed": 1, "failed": 0}, result
    await verify_after(B, last=True)
    print(
        json.dumps(
            {
                "stage": "last_shared_reference",
                "all_four_stores_cleaned": True,
                "shared_objects_removed": True,
                "status": "passed",
            }
        )
    )


async def concurrent():
    await setup()
    await service.trash(KB, [B], deleted_by="fixture")
    await due(B)
    rows = await service.repository.claim_due(limit=2)
    assert {r.file_id for r in rows} == {A, B}
    await asyncio.gather(*(service._purge_one(row) for row in rows))
    await verify_after(A, last=True)
    await verify_after(B, last=True)
    print(
        json.dumps(
            {
                "stage": "concurrent_shared_purge",
                "shared_space_image_deleted": True,
                "shared_upload_deleted": True,
                "both_files_four_stores_cleaned": True,
                "status": "passed",
            }
        )
    )


async def crosskb():
    """两个知识库引用同一原件，串行及并行清理均保留至最后引用。"""
    for parallel in [False, True]:
        tag = "parallel" if parallel else "sequential"
        kbs = [f"purge_cross_{tag}_one{SUFFIX}", f"purge_cross_{tag}_two{SUFFIX}"]
        fids = [f"purge_cross_{tag}_A{SUFFIX}", f"purge_cross_{tag}_B{SUFFIX}"]
        key = f"{kbs[0]}/shared-original.pdf"
        async with pg_manager.get_async_session_context() as db:
            for kb in kbs:
                db.add(KnowledgeBase(kb_id=kb, name=kb, kb_type="milvus", additional_params={}))
            await db.flush()
            for kb, fid in zip(kbs, fids):
                db.add(
                    KnowledgeFile(
                        file_id=fid,
                        kb_id=kb,
                        filename=fid + ".pdf",
                        status="done",
                        path=f"minio://{buckets['documents']}/{key}",
                        markdown_file=f"minio://{buckets['parsed']}/{kb}/parsed/{fid}.md",
                    )
                )
        await client.aupload_file(buckets["documents"], key, b"cross-kb shared original")
        for kb, fid in zip(kbs, fids):
            await client.aupload_file(buckets["parsed"], f"{kb}/parsed/{fid}.md", b"synthetic text")
            await service.trash(kb, [fid], deleted_by="fixture")
            await due(fid)
        rows = await service.repository.claim_due(limit=2)
        assert {r.file_id for r in rows} == set(fids)
        if parallel:
            await asyncio.gather(*(service._purge_one(row) for row in rows))
        else:
            await service._purge_one(rows[0])
            assert client.file_exists(buckets["documents"], key), "first KB removed another KB original"
            await service._purge_one(rows[1])
        assert not client.file_exists(buckets["documents"], key), "last reference left an orphan"
        async with pg_manager.get_async_session_context() as db:
            assert (
                await db.scalar(select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.file_id.in_(fids)))
                == 0
            )
        print(
            json.dumps(
                {
                    "stage": "cross_kb_shared_original_" + tag,
                    "last_reference_only": True,
                    "zero_orphan": True,
                    "status": "passed",
                }
            )
        )


async def main():
    pg_manager.initialize()
    await pg_manager.create_knowledge_tables()
    await {
        "setup": setup,
        "reads": check_reads,
        "fail": failed,
        "retry": retry,
        "concurrent": concurrent,
        "crosskb": crosskb,
    }[sys.argv[1]]()
    await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
