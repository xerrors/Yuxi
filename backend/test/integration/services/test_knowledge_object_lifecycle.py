"""真实 PostgreSQL 与 MinIO 的知识原件和派生产物生命周期。"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.migrations.schema import create_knowledge_tables
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeImport
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def database(monkeypatch):
    """数据库用例拥有独立 Schema，不依赖运行中的 API 或 worker。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_object_lifecycle")
    await create_knowledge_tables(manager)
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: BackgroundJobRecord.__table__.create(conn, checkfirst=True))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction():
        """被测服务拥有提交点。"""
        async with sessions() as session, session.begin():
            yield session

    monkeypatch.setattr(pg_manager, "get_async_session_context", transaction)
    async with sessions() as session, session.begin():
        session.add(KnowledgeBase(kb_id="kb-test", name="test", kb_type="milvus"))
    try:
        yield sessions
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest_asyncio.fixture
async def object_storage(monkeypatch):
    """真实 MinIO 使用独立桶，只清理本例创建的对象。"""
    from yuxi.infrastructure.minio.client import MinIOClient
    from yuxi.modules.knowledge import base, preview
    from yuxi.modules.knowledge.services import uploads

    client = MinIOClient()
    bucket = f"pytest-knowledge-{uuid4().hex}"
    client.KB_BUCKETS = {"documents": bucket, "parsed": bucket, "images": bucket}
    monkeypatch.setattr(MinIOClient, "KB_BUCKETS", client.KB_BUCKETS)
    for module in (base, preview, uploads):
        monkeypatch.setattr(module, "get_minio_client", lambda: client)
    await asyncio.to_thread(client.ensure_bucket_exists, bucket)
    try:
        yield client
    finally:
        await client.adelete_objects_by_prefix(bucket, "")
        await asyncio.to_thread(client.client.remove_bucket, bucket)


async def test_office_preview_cannot_publish_after_file_deletion(database, object_storage, monkeypatch):
    """转换暂停时删除文件，恢复转换后回读存储确认没有晚写预览。"""
    from yuxi.modules.knowledge import preview

    client = object_storage
    source = await client.aupload_file(client.KB_BUCKETS["documents"], "kb-test/original/file-test/source.docx", b"office")
    await KnowledgeFileRepository().upsert(
        "file-test",
        {
            "kb_id": "kb-test",
            "filename": "report.docx",
            "path": f"minio://{source.bucket_name}/{source.object_name}",
            "status": "uploaded",
            "file_size": 6,
        },
    )
    converting, resume = asyncio.Event(), asyncio.Event()

    async def convert(_filename, _content):
        """只替代转换工具，发布、数据库及对象存储保持真实。"""
        converting.set()
        await resume.wait()
        return b"%PDF-1.4\nconverted"

    monkeypatch.setattr(preview, "convert_office_to_pdf", convert)
    task = asyncio.create_task(preview.read_knowledge_file_preview("kb-test", "file-test"))
    try:
        await asyncio.wait_for(converting.wait(), timeout=5)
        await KnowledgeFileRepository().mark_deleted(kb_id="kb-test", file_id="file-test")
        resume.set()
        with pytest.raises(ValueError, match="not found"):
            await task
        assert await client.astat_file(client.KB_BUCKETS["parsed"], "kb-test/preview/file-test.pdf") is None
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_file_resource_cleanup_removes_office_preview(database, object_storage):
    """删除单文件必须回收独立 preview 前缀下的缓存。"""
    from yuxi.modules.knowledge.base import KnowledgeExecutor

    client = object_storage
    bucket = client.KB_BUCKETS["documents"]
    source = await client.aupload_file(bucket, "kb-test/original/file-test/source.docx", b"office")
    await KnowledgeFileRepository().upsert(
        "file-test", {"kb_id": "kb-test", "filename": "report.docx", "path": f"minio://{source.bucket_name}/{source.object_name}"}
    )
    for key in ("kb-test/preview/file-test.pdf", "kb-test/parsed/file-test/attempt/content.md", "kb-test/kb-images/file-test/image.png"):
        await client.aupload_file(bucket, key, b"derived")
    await KnowledgeFileRepository().mark_deleted(kb_id="kb-test", file_id="file-test")
    await KnowledgeExecutor.cleanup_file_resources(None, "kb-test", "file-test")
    assert await asyncio.to_thread(lambda: list(client.client.list_objects(bucket, recursive=True))) == []


async def test_staging_lifecycle_is_tag_scoped_and_preserves_unrelated_rules(object_storage):
    """回读 MinIO 策略和对象标签，正式原件不进入暂存过期范围。"""
    from minio.commonconfig import ENABLED, Filter
    from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

    from yuxi.modules.knowledge.services.uploads import STAGING_TAG, stage_document

    client = object_storage
    bucket = client.KB_BUCKETS["documents"]
    unrelated = Rule(ENABLED, rule_filter=Filter(prefix="unrelated/"), rule_id="other-owner", expiration=Expiration(days=30))
    await asyncio.to_thread(client.client.set_bucket_lifecycle, bucket, LifecycleConfig([unrelated]))
    staged = await stage_document(kb_id="kb-test", filename="source.txt", content=b"source")
    original = await client.aupload_file(bucket, "kb-test/original/file/source.txt", b"source")
    rules = (await asyncio.to_thread(client.client.get_bucket_lifecycle, bucket)).rules
    by_id = {rule.rule_id: rule for rule in rules}
    assert by_id["other-owner"] == unrelated
    assert by_id[STAGING_TAG].expiration.days == 7
    assert by_id[STAGING_TAG].rule_filter.tag.key == STAGING_TAG
    assert by_id[STAGING_TAG].rule_filter.tag.value == "true"
    assert dict(await asyncio.to_thread(client.client.get_object_tags, bucket, staged.upload.object_name)) == {STAGING_TAG: "true"}
    assert not await asyncio.to_thread(client.client.get_object_tags, bucket, original.object_name)
    assert await client.adownload_file(bucket, staged.upload.object_name) == b"source"


async def test_import_intent_is_minimal_and_replays_after_staging_expires(database, object_storage, monkeypatch):
    """回执重放不读取已过期暂存，运行意图只保留执行需要的身份和参数。"""
    from yuxi.modules.knowledge.services import imports
    from yuxi.modules.knowledge.services.uploads import stage_document

    client = object_storage
    monkeypatch.setattr(imports, "get_minio_client", lambda: client)

    async def unavailable(_job_id):
        """提交后首次投递失败，保留真实 pending 作业供回读。"""
        return None

    monkeypatch.setattr(imports, "dispatch_job", unavailable)
    staged = await stage_document(kb_id="kb-test", filename="source.txt", content=b"source")
    source = f"minio://{staged.upload.bucket_name}/{staged.upload.object_name}"
    params = {
        "content_hashes": {source: staged.content_hash},
        "file_sizes": {source: 6},
        "source_paths": {source: "folder/source.txt"},
        "auto_index": True,
        "chunk_preset_id": "general",
    }
    request = {"kb_id": "kb-test", "items": [source], "params": params, "operator_id": "operator", "import_id": "receipt", "process": True}
    first = await imports.register_import(**request)
    async with database() as session:
        job = await session.get(BackgroundJobRecord, first["job_id"])
        assert job.status == "pending" and job.handler_version == 2
        assert job.payload == {
            "kb_id": "kb-test",
            "file_ids": [first["items"][0]["file_id"]],
            "params": {"auto_index": True, "chunk_preset_id": "general"},
            "operator_id": "operator",
        }
        assert (await session.scalar(select(KnowledgeImport))).response == first
    # 生命周期最终删除源对象后，幂等请求仍以提交回执为准。
    await client.adelete_file(staged.upload.bucket_name, staged.upload.object_name)
    assert await imports.register_import(**request) == first
    file = await KnowledgeFileRepository().get_by_file_id(first["items"][0]["file_id"])
    from yuxi.infrastructure.minio.object_urls import parse_minio_url

    assert await client.adownload_file(*parse_minio_url(file.path)) == b"source"


async def test_same_fingerprint_reuses_file_and_other_parser_stores_again(database, object_storage, monkeypatch):
    """相同哈希、大小和解析器不再插入第二份；换解析器才是新的处理请求。未解析的重复上传会重新入队。"""

    from sqlalchemy import func

    from yuxi.modules.knowledge.models import KnowledgeFile
    from yuxi.modules.knowledge.services import imports
    from yuxi.modules.knowledge.services.uploads import stage_document

    monkeypatch.setattr(imports, "get_minio_client", lambda: object_storage)

    async def skip_dispatch(_job_id):
        return None

    monkeypatch.setattr(imports, "dispatch_job", skip_dispatch)
    content = b"same-bytes"

    async def register(import_id, *, filename, process, ocr_engine=None):
        staged = await stage_document(kb_id="kb-test", filename=filename.rsplit("/", 1)[-1], content=content)
        source = f"minio://{staged.upload.bucket_name}/{staged.upload.object_name}"
        params = {
            "content_hashes": {source: staged.content_hash},
            "file_sizes": {source: len(content)},
            "source_paths": {source: filename},
        }
        if ocr_engine:
            params["ocr_engine"] = ocr_engine
        return await imports.register_import(
            kb_id="kb-test",
            items=[source],
            params=params,
            operator_id="operator",
            import_id=import_id,
            process=process,
        )

    first = await register("fingerprint-first", filename="folder/a.txt", process=False)
    second = await register("fingerprint-second", filename="folder/b.txt", process=True)
    assert second["items"][0]["file_id"] == first["items"][0]["file_id"]
    async with database() as session:
        job = await session.get(BackgroundJobRecord, second["job_id"])
        assert job.payload["file_ids"] == [first["items"][0]["file_id"]]
        file_count = await session.scalar(
            select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.is_folder.is_(False), KnowledgeFile.deleted_at.is_(None))
        )
        assert file_count == 1

    third = await register("fingerprint-ocr", filename="folder/c.txt", process=False, ocr_engine="rapidocr")
    assert third["items"][0]["file_id"] != first["items"][0]["file_id"]
    async with database() as session:
        file_count = await session.scalar(
            select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.is_folder.is_(False), KnowledgeFile.deleted_at.is_(None))
        )
        assert file_count == 2
