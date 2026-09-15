"""真实 PG/MinIO 与 process_task 的同进程 assembled integration（非独立 ARQ E2E）。"""

import asyncio
import io
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool


@pytest.mark.asyncio
async def test_document_snapshot_survives_settings_reduction_in_real_worker(monkeypatch):
    """持久快照不随设置收紧改变；读取实际对象和文件终态作 oracle。"""
    url = os.getenv("TEST_POSTGRES_URL", "")
    if os.getenv("TEST_ALLOW_LIMITS_WORKER") != "1":
        pytest.skip("Requires isolated PG, Redis, MinIO and Milvus fixtures")
    assert make_url(url).database == "limits"
    for key, expected in [
        ("MINIO_URI", "pr-public-20260915-minio"),
        ("MILVUS_URI", "pr-public-20260915-milvus"),
        ("REDIS_URL", "pr-public-20260915-redis"),
    ]:
        assert expected in os.environ[key]
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import ConfigOption, TaskRecord
    from yuxi.storage.postgres.models_knowledge import Base, KnowledgeBase, KnowledgeFile
    from yuxi.config.options import ensure_options_in_db
    from yuxi.services.document_limits_service import save_document_limits, snapshot_document_limits
    from yuxi.services.task_service import tasker, process_task
    from yuxi.repositories.task_repository import TaskRepository
    from yuxi.storage.minio.client import get_minio_client
    from yuxi.knowledge.utils.kb_utils import parse_minio_url

    suffix = uuid4().hex
    schema = "limits_worker_" + suffix
    kb_id = "kb_limits_" + suffix
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    monkeypatch.setenv("YUXI_INSTANCE_ID", schema)
    monkeypatch.setenv("DOCUMENT_UPLOAD_HARD_MAX_MIB", "3")
    monkeypatch.setenv("DOCUMENT_OCR_HARD_MAX_PAGES", "10")
    client = get_minio_client()
    created_objects = []
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: ConfigOption.metadata.create_all(c, tables=[ConfigOption.__table__, TaskRecord.__table__])
            )
            await conn.run_sync(Base.metadata.create_all)
        async with session_context() as db:
            await ensure_options_in_db(db)
            db.add(KnowledgeBase(kb_id=kb_id, name="Limits worker fixture", kb_type="milvus", additional_params={}))
        await save_document_limits({"upload_max_mib": 2, "ocr_max_pages": 2}, 0, "fixture")
        writer = PdfWriter()
        for number in (1, 2):
            page = writer.add_blank_page(width=200, height=200)
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 100 Td (Snapshot fixture page {number}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        original = buffer.getvalue()
        bucket = client.KB_BUCKETS["documents"]
        object_name = f"{kb_id}/two-pages.pdf"
        uploaded = await client.aupload_file(bucket, object_name, original)
        created_objects.append((bucket, object_name))
        assert await client.adownload_file(bucket, object_name) == original
        ids = [suffix[:24] + "-old", suffix[:24] + "-new"]
        async with session_context() as db:
            for file_id in ids:
                db.add(
                    KnowledgeFile(
                        file_id=file_id,
                        kb_id=kb_id,
                        filename=file_id + ".pdf",
                        path=uploaded.url,
                        status="uploaded",
                        processing_params={"ocr_engine": "disable"},
                    )
                )
        old_snapshot = await snapshot_document_limits()
        old_task = await tasker.enqueue(
            name="old limits fixture",
            task_type="knowledge_parse",
            payload={
                "kb_id": kb_id,
                "file_ids": [ids[0]],
                "operator_id": "fixture",
                "params": {},
                "document_limits": old_snapshot,
            },
        )
        await save_document_limits({"upload_max_mib": 1, "ocr_max_pages": 1}, 1, "fixture")
        new_snapshot = await snapshot_document_limits()
        new_task = await tasker.enqueue(
            name="new limits fixture",
            task_type="knowledge_parse",
            payload={
                "kb_id": kb_id,
                "file_ids": [ids[1]],
                "operator_id": "fixture",
                "params": {"_document_limits": old_snapshot},
                "document_limits": new_snapshot,
            },
        )
        assert old_snapshot["max_ocr_pages"] == 2 and new_snapshot["max_ocr_pages"] == 1
        for task in (old_task, new_task):
            assert (await TaskRepository().get_by_id(task.id)).status == "pending"
            await process_task({"worker_id": "limits-review-assembled"}, task.id)
        old_record = await TaskRepository().get_by_id(old_task.id)
        new_record = await TaskRepository().get_by_id(new_task.id)
        async with sessions() as db:
            files = {row.file_id: row for row in (await db.scalars(select(KnowledgeFile))).all()}
        old_file, new_file = (files[file_id] for file_id in ids)
        assert old_record.status == "success" and old_record.result["failed"] == 0, old_record.to_dict()
        assert old_record.payload["document_limits"] == old_snapshot
        assert old_file.status == "parsed", old_file.error_message
        parsed_bucket, parsed_object = parse_minio_url(old_file.markdown_file)
        created_objects.append((parsed_bucket, parsed_object))
        parsed = (await client.adownload_file(parsed_bucket, parsed_object)).decode()
        assert "Snapshot fixture page 1" in parsed and "Snapshot fixture page 2" in parsed
        # Durable batch completion is success; per-file failure is persisted in its result and file record.
        assert new_record.status == "success" and new_record.result["failed"] == 1, new_record.to_dict()
        assert new_file.status == "error_parsing" and "OCR limit of 1 pages" in new_file.error_message
        assert new_file.markdown_file is None
        assert old_file.processing_owner is None and new_file.processing_owner is None
        print(
            "ASSEMBLED_LIMITS_FACTS="
            + json.dumps(
                {
                    "old_task": old_record.status,
                    "old_failed": old_record.result["failed"],
                    "old_file": old_file.status,
                    "new_task": new_record.status,
                    "new_failed": new_record.result["failed"],
                    "new_file": new_file.status,
                    "old_snapshot": old_record.payload["document_limits"],
                    "new_snapshot": new_record.payload["document_limits"],
                    "parsed_text": parsed,
                }
            )
        )
    finally:
        for bucket, name in created_objects:
            await asyncio.to_thread(client.client.remove_object, bucket, name)
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
