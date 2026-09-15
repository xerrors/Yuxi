"""独立 PostgreSQL schema 验证回收站事务、可见性与清理租约。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.document_trash_repository import DocumentTrashRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.manager import KNOWLEDGE_FILE_TRASH_SCHEMA_STATEMENTS, pg_manager
from yuxi.storage.postgres.models_knowledge import Base, KnowledgeBase, KnowledgeFile
from yuxi.utils.datetime_utils import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def trash_db(monkeypatch):
    """显式专用连接串、随机schema，不读取默认生产连接配置。"""
    url = os.getenv("TEST_TRASH_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_TRASH_POSTGRES_URL is required for isolated PostgreSQL tests")
    schema = "trash_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory.begin() as db:
            db.add(KnowledgeBase(kb_id="kb", name="fixture", kb_type="milvus", mindmap={"old": True}))
            db.add(KnowledgeBase(kb_id="other", name="other", kb_type="milvus"))

        @asynccontextmanager
        async def session_context():
            async with factory.begin() as db:
                yield db

        monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed(factory, *, status="indexed"):
    """建立一组父子文件及不相干文件。"""
    async with factory.begin() as db:
        db.add(KnowledgeFile(file_id="folder", kb_id="kb", filename="folder", is_folder=True, status="done"))
        await db.flush()
        db.add(
            KnowledgeFile(
                file_id="file",
                kb_id="kb",
                parent_id="folder",
                filename="a.txt",
                is_folder=False,
                status=status,
                content_hash="hash",
                path="original",
                markdown_file="parsed",
                file_size=12,
                chunk_count=3,
            )
        )
        db.add(KnowledgeFile(file_id="other", kb_id="other", filename="other", status="indexed"))


async def test_trash_tree_is_atomic_and_preserves_recovery_material(trash_db):
    await seed(trash_db)
    now = utc_now()
    repo = DocumentTrashRepository()
    rows = await repo.trash("kb", ["folder"], deleted_by="owner", now=now)
    assert {row.file_id for row in rows} == {"folder", "file"}
    assert len({row.deletion_id for row in rows}) == 1
    assert all(row.purge_after == now + timedelta(days=30) for row in rows)
    async with trash_db() as db:
        file = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file"))
        assert (file.path, file.markdown_file, file.status, file.chunk_count) == ("original", "parsed", "indexed", 3)
        assert (await db.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "kb"))).mindmap is None
    assert await repo.has_trashed("kb")
    assert await repo.get_trashed("other", "file") is None
    assert len(await repo.list_trashed("kb")) == 2
    assert await repo.count_trashed("kb") == 2
    assert len(await repo.list_storage_references("kb")) == 2
    repeated = await repo.trash("kb", ["folder"], deleted_by="owner", now=now + timedelta(days=1))
    assert repeated[0].deletion_id == rows[0].deletion_id
    assert repeated[0].purge_after == rows[0].purge_after


@pytest.mark.parametrize("status", ["processing", "waiting", "parsing", "indexing"])
async def test_processing_descendant_blocks_entire_delete(trash_db, status):
    await seed(trash_db, status=status)
    with pytest.raises(ValueError, match="Processing"):
        await DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner")
    async with trash_db() as db:
        assert all(row.deleted_at is None for row in (await db.scalars(select(KnowledgeFile))).all())


async def test_wrong_kb_batch_is_atomic(trash_db):
    await seed(trash_db)
    with pytest.raises(ValueError, match="not found"):
        await DocumentTrashRepository().trash("kb", ["folder", "other"], deleted_by="owner")
    assert not await DocumentTrashRepository().has_trashed("kb")


async def test_ordinary_queries_and_writes_exclude_trash(trash_db):
    await seed(trash_db)
    await DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner")
    repo = KnowledgeFileRepository()
    assert await repo.get_by_file_id("file") is None
    assert await repo.list_by_file_ids(["file", "folder"]) == []
    assert await repo.list_by_kb_id("kb") == []
    assert await repo.list_by_kb_id_after("kb") == []
    assert await repo.search_files(kb_id="kb") == ([], 0)
    assert await repo.list_documents(kb_id="kb") == ([], 0)
    assert await repo.list_documents(kb_id="kb", status="indexed", recursive=True) == ([], 0)
    assert await repo.list_children(kb_id="kb", parent_id="folder") == []
    assert await repo.get_filenames_by_file_ids(kb_id="kb", file_ids=["file"]) == {}
    assert not await repo.exists_by_content_hash(kb_id="kb", content_hash="hash")
    assert not await repo.exists_by_filename(kb_id="kb", filename="a.txt")
    assert await repo.list_same_name_files(kb_id="kb", filename="a.txt") == []
    assert await repo.list_file_ids_by_filename_contains(kb_id="kb", filename_pattern="a") == []
    assert await repo.list_file_ids_by_exact_statuses(kb_id="kb", statuses=["indexed"]) == []
    async with trash_db() as db:
        assert (await repo.query_kb_file_stats("kb", session=db))["row_count"] == 0
    assert await repo.update_fields(file_id="file", data={"filename": "changed"}) is None
    assert (
        await repo.update_fields_if_status(
            kb_id="kb", file_id="file", allowed_statuses={"indexed"}, data={"status": "done"}
        )
        is None
    )
    with pytest.raises(ValueError, match="trash"):
        await repo.upsert("file", {"kb_id": "kb", "filename": "changed"})
    async with trash_db() as db:
        file = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file"))
        assert file.filename == "a.txt" and file.status == "indexed"


async def test_restore_subtree_preserves_status_and_redelete_gets_new_generation(trash_db):
    await seed(trash_db)
    repo = DocumentTrashRepository()
    now = utc_now()
    rows = await repo.trash("kb", ["folder"], deleted_by="owner", now=now)
    old = rows[0].deletion_id
    restored = await repo.restore("kb", "folder", now=now + timedelta(days=29))
    assert {row.file_id for row in restored} == {"folder", "file"}
    assert all(row.deleted_at is None for row in restored)
    assert (await KnowledgeFileRepository().get_by_file_id("file")).status == "indexed"
    again = await repo.trash("kb", ["folder"], deleted_by="owner")
    assert all(row.deletion_id != old for row in again)


async def test_restore_deadline_parent_and_name_conflicts(trash_db):
    await seed(trash_db)
    repo = DocumentTrashRepository()
    now = utc_now()
    await repo.trash("kb", ["folder"], deleted_by="owner", now=now)
    with pytest.raises(ValueError, match="parent"):
        await repo.restore("kb", "file", now=now)
    with pytest.raises(ValueError, match="window"):
        await repo.restore("kb", "folder", now=now + timedelta(days=30))
    async with trash_db.begin() as db:
        db.add(KnowledgeFile(file_id="conflict", kb_id="kb", filename="folder", is_folder=True))
    with pytest.raises(ValueError, match="conflicts"):
        await repo.restore("kb", "folder", now=now)


async def test_claim_concurrency_tokens_and_durable_manifest(trash_db):
    await seed(trash_db)
    repo = DocumentTrashRepository()
    now = utc_now()
    await repo.trash("kb", ["folder"], deleted_by="owner", now=now - timedelta(days=31))
    one, two = await asyncio.gather(repo.claim_due(limit=1), repo.claim_due(limit=1))
    assert len(one) == len(two) == 1
    assert one[0].file_id != two[0].file_id
    row = one[0]
    objects = [{"bucket": "kb", "object": "image"}]
    assert await repo.set_purge_objects(row.file_id, row.deletion_id, row.purge_token, objects)
    assert await repo.set_purge_objects(row.file_id, row.deletion_id, row.purge_token, [])
    assert not await repo.complete_purge(row.file_id, row.deletion_id, "stale")
    assert await repo.fail_purge(row.file_id, row.deletion_id, row.purge_token, "retry")
    assert await repo.claim_due(limit=1) == []
    async with trash_db.begin() as db:
        await db.execute(
            update(KnowledgeFile)
            .where(KnowledgeFile.file_id == row.file_id)
            .values(purge_lease_until=utc_now() - timedelta(seconds=1))
        )
    retried = (await repo.claim_due(limit=1))[0]
    assert retried.purge_token != row.purge_token
    assert retried.purge_objects == objects
    assert retried.purge_error == "retry"
    assert not await repo.fail_purge(row.file_id, row.deletion_id, row.purge_token, "stale")
    assert await repo.complete_purge(retried.file_id, retried.deletion_id, retried.purge_token)
    assert await repo.get_trashed("kb", retried.file_id) is None


async def test_expired_lease_can_be_reclaimed(trash_db):
    await seed(trash_db)
    repo = DocumentTrashRepository()
    now = utc_now()
    await repo.trash("kb", ["file"], deleted_by="owner", now=now - timedelta(days=31))
    old = (await repo.claim_due(now=now - timedelta(seconds=20), lease_seconds=10))[0]
    assert not await repo.complete_purge(old.file_id, old.deletion_id, old.purge_token)
    new = (await repo.claim_due())[0]
    assert new.purge_token != old.purge_token
    assert new.purge_started_at == old.purge_started_at
    assert await repo.complete_purge(new.file_id, new.deletion_id, new.purge_token)


async def test_schema_statements_upgrade_existing_rows_idempotently(trash_db):
    async with trash_db.begin() as db:
        for field in [
            "deleted_at",
            "purge_after",
            "deletion_id",
            "deleted_by",
            "purge_started_at",
            "purge_lease_until",
            "purge_token",
            "purge_error",
            "purge_objects",
        ]:
            await db.execute(text(f'ALTER TABLE knowledge_files DROP COLUMN "{field}"'))
        await db.execute(
            text("INSERT INTO knowledge_files(file_id,kb_id,filename,status) VALUES ('legacy','kb','legacy','indexed')")
        )
        for _ in range(2):
            for statement in KNOWLEDGE_FILE_TRASH_SCHEMA_STATEMENTS:
                await db.execute(text(statement))
    legacy = await KnowledgeFileRepository().get_by_file_id("legacy")
    assert legacy.status == "indexed" and legacy.deleted_at is None


async def test_database_delete_checks_trash_before_external_cleanup(trash_db, monkeypatch):
    """整库外部副作用在树锁及回收站检查之后发生。"""
    from unittest.mock import AsyncMock
    from yuxi.repositories import knowledge_base_repository as module

    @asynccontextmanager
    async def cache_lock(_kb):
        yield

    monkeypatch.setattr(module, "kb_config_cache_lock", cache_lock)
    monkeypatch.setattr(module, "delete_cached_kb_config", AsyncMock())
    await seed(trash_db)
    await DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner")
    cleanup = AsyncMock()
    with pytest.raises(ValueError, match="trashed"):
        await module.KnowledgeBaseRepository().delete("kb", before_commit=cleanup)
    cleanup.assert_not_awaited()
    assert await DocumentTrashRepository().count_trashed("kb") == 2


async def test_database_delete_holds_tree_lock_during_external_cleanup(trash_db, monkeypatch):
    """整库清理与移入回收站不会穿过检查和外部清理之间的窗口。"""
    from unittest.mock import AsyncMock
    from yuxi.repositories import knowledge_base_repository as module

    @asynccontextmanager
    async def cache_lock(_kb):
        yield

    monkeypatch.setattr(module, "kb_config_cache_lock", cache_lock)
    monkeypatch.setattr(module, "delete_cached_kb_config", AsyncMock())
    await seed(trash_db)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cleanup():
        entered.set()
        await release.wait()
        await KnowledgeFileRepository().delete_by_kb_id("kb")

    deleting = asyncio.create_task(module.KnowledgeBaseRepository().delete("kb", before_commit=cleanup))
    await asyncio.wait_for(entered.wait(), timeout=5)
    trashing = asyncio.create_task(DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner"))
    try:
        await asyncio.sleep(0.05)
        assert not trashing.done()
    finally:
        release.set()
    await asyncio.wait_for(deleting, timeout=5)
    with pytest.raises(ValueError, match="not found"):
        await asyncio.wait_for(trashing, timeout=5)


async def test_tree_writes_reject_invisible_parent_and_immutable_kb(trash_db):
    """直接repository写入也受父链及知识库归属约束。"""
    await seed(trash_db)
    repo = KnowledgeFileRepository()
    await DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner")
    with pytest.raises(ValueError, match="active folder"):
        await repo.upsert("new", {"kb_id": "kb", "filename": "new", "parent_id": "folder"})
    with pytest.raises(ValueError, match="cannot change"):
        await repo.upsert("other", {"kb_id": "kb", "filename": "changed"})
    active = await repo.upsert("active", {"kb_id": "kb", "filename": "active", "deleted_at": utc_now()})
    assert active.deleted_at is None
    with pytest.raises(ValueError, match="active folder"):
        await repo.update_fields(file_id="active", data={"parent_id": "folder"})
    with pytest.raises(ValueError, match="cannot change"):
        await repo.update_fields(file_id="active", data={"kb_id": "other"})
    with pytest.raises(ValueError, match="ownership"):
        await repo.update_fields_if_status(
            kb_id="kb", file_id="active", allowed_statuses={"uploaded"}, data={"parent_id": "folder"}
        )


async def test_tree_session_is_reused_without_self_deadlock(trash_db):
    """现有move调用在同一树锁事务内更新而非另开连接重取锁。"""
    await seed(trash_db)
    repo = KnowledgeFileRepository()
    async with repo.lock_file_tree("kb") as session:
        record = await asyncio.wait_for(
            repo.update_fields(file_id="file", kb_id="kb", data={"parent_id": None}, session=session), timeout=2
        )
        assert record.parent_id is None
    assert (await repo.get_by_file_id("file")).parent_id is None


async def test_create_and_trash_are_serialized(trash_db):
    """并发新建子项要么随父项回收，要么被已删除父项拒绝。"""
    await seed(trash_db)
    outcome = await asyncio.gather(
        DocumentTrashRepository().trash("kb", ["folder"], deleted_by="owner"),
        KnowledgeFileRepository().upsert("new", {"kb_id": "kb", "parent_id": "folder", "filename": "new"}),
        return_exceptions=True,
    )
    assert not isinstance(outcome[0], Exception)
    if isinstance(outcome[1], Exception):
        assert isinstance(outcome[1], ValueError)
    async with trash_db() as db:
        new = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "new"))
        assert new is None or new.deleted_at is not None


async def test_storage_references_cover_other_kb_and_trashed_rows(trash_db):
    await seed(trash_db)
    await DocumentTrashRepository().trash("kb", ["file"], deleted_by="owner")
    rows = await DocumentTrashRepository().list_storage_references()
    assert {row.file_id for row in rows} == {"folder", "file", "other"}
    assert next(row for row in rows if row.file_id == "file").deleted_at is not None


@pytest.mark.parametrize("operation", ["upsert", "update", "cas"])
async def test_storage_lock_blocks_cross_kb_reference_writes(trash_db, operation):
    await seed(trash_db)
    repo = KnowledgeFileRepository()

    async def register():
        if operation == "upsert":
            return await repo.upsert("new", {"kb_id": "other", "filename": "new", "path": "shared"})
        if operation == "update":
            return await repo.update_fields(file_id="other", data={"path": "shared"})
        return await repo.update_fields_if_status(
            kb_id="other", file_id="other", allowed_statuses={"indexed"}, data={"path": "shared"}
        )

    async with repo.lock_storage_references():
        pending = asyncio.create_task(register())
        await asyncio.sleep(0.1)
        assert not pending.done()
    await asyncio.wait_for(pending, timeout=5)
    row = await repo.get_by_file_id("new" if operation == "upsert" else "other")
    assert row.path == "shared"


async def test_registration_revalidates_after_global_lock_and_rolls_back(trash_db):
    await seed(trash_db)
    repo = KnowledgeFileRepository()
    object_exists = True
    verified = asyncio.Event()

    async def verify():
        verified.set()
        if not object_exists:
            raise ValueError("Original storage object no longer exists")

    async with repo.lock_storage_references():
        registration = asyncio.create_task(
            repo.upsert("new", {"kb_id": "other", "filename": "new", "path": "shared"}, before_commit=verify)
        )
        await asyncio.sleep(0.1)
        assert not verified.is_set()
        object_exists = False
    with pytest.raises(ValueError, match="no longer exists"):
        await asyncio.wait_for(registration, timeout=5)
    assert verified.is_set()
    assert await repo.get_by_file_id("new") is None


@pytest.mark.parametrize("same_object", [False, True])
async def test_database_delete_rejects_cross_kb_storage_before_cleanup(trash_db, monkeypatch, same_object):
    from unittest.mock import AsyncMock
    from yuxi.repositories import knowledge_base_repository as module

    @asynccontextmanager
    async def cache_lock(_kb):
        yield

    monkeypatch.setattr(module, "kb_config_cache_lock", cache_lock)
    monkeypatch.setattr(module, "delete_cached_kb_config", AsyncMock())
    await seed(trash_db)
    shared = "http://minio/knowledgebases/external/shared" if same_object else "http://minio/knowledgebases/kb/upload/a"
    async with trash_db.begin() as db:
        await db.execute(update(KnowledgeFile).where(KnowledgeFile.file_id == "other").values(path=shared))
        if same_object:
            await db.execute(update(KnowledgeFile).where(KnowledgeFile.file_id == "file").values(path=shared))
    cleanup = AsyncMock()
    with pytest.raises(ValueError, match="storage is referenced"):
        await module.KnowledgeBaseRepository().delete("kb", before_commit=cleanup)
    cleanup.assert_not_awaited()
    async with trash_db() as db:
        assert await db.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "kb")) is not None


async def test_restore_rechecks_deadline_after_waiting_for_tree_lock(trash_db, monkeypatch):
    """恢复排队跨过截止时间时仍保持删除态，不使用排队前的旧时间。"""
    from yuxi.repositories import document_trash_repository as module

    await seed(trash_db)
    current = utc_now()
    clock = [current]
    await DocumentTrashRepository().trash("kb", ["file"], deleted_by="owner", now=current - timedelta(days=29))
    async with trash_db.begin() as db:
        await db.execute(
            update(KnowledgeFile)
            .where(KnowledgeFile.file_id == "file")
            .values(purge_after=current + timedelta(seconds=1))
        )
    monkeypatch.setattr(module, "utc_now", lambda: clock[0])
    waiting = asyncio.Event()
    original_lock = DocumentTrashRepository._lock_tree

    async def observed_lock(session, kb_id):
        waiting.set()
        await original_lock(session, kb_id)

    monkeypatch.setattr(DocumentTrashRepository, "_lock_tree", staticmethod(observed_lock))
    async with KnowledgeFileRepository().lock_file_tree("kb"):
        pending = asyncio.create_task(DocumentTrashRepository().restore("kb", "file"))
        await asyncio.wait_for(waiting.wait(), timeout=2)
        assert not pending.done()
        clock[0] += timedelta(seconds=2)
    with pytest.raises(ValueError, match="Restore window closed"):
        await asyncio.wait_for(pending, timeout=2)
    async with trash_db() as db:
        row = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file"))
        assert row.deleted_at is not None
