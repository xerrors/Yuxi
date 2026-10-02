"""编辑发布与入库认领在真实 PostgreSQL / MinIO 上的串行化窗口回归。

维护者指出的失败窗口是「发布不是原子的」：旧实现先做状态与版本 CAS、再覆盖确定性路径的
产物对象，两步之间入库可以认领并读到旧内容，最终形成「产物新、分块与向量旧」。
本文件在**真实** PostgreSQL 和 MinIO 上用确定性同步点停在「产物已写、引用未切」之间，
驱动真实的认领语句，断言不变量：**入库读到的内容必须等于行最终指向的内容**。

这条不变量能抓住旧顺序：认领读到旧对象后，编辑再把同一个对象覆盖成新内容，
行指向的那份对象内容就与实际读到的内容分叉。
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.knowledge.base import FileStatus, KBFileStateConflictError, KnowledgeBase
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.repositories import knowledge_file_repository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.storage.postgres.models_knowledge import KnowledgeBase as KnowledgeBaseModel
from yuxi.storage.postgres.models_knowledge import KnowledgeFile as KnowledgeFileModel
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SEED_CONTENT = "# 原始产物\n\n旧内容。"
EDITED_CONTENT = "# 人工修正\n\n新内容。"
# index_file 认领时允许的状态集合（milvus.py 的 allowed_statuses），照抄避免测试自己发明条件
INDEX_CLAIM_STATUSES = {FileStatus.PARSED, FileStatus.ERROR_INDEXING, FileStatus.INDEXED, "done"}


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """只使用独立 Schema，不校验线上 Schema 版本。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """资源由本文件的局部 fixture 清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本测试不创建沙盒。"""
    yield


@pytest.fixture
async def publish_store(monkeypatch):
    """隔离 Schema + 真实 MinIO 对象 + 一个在跑的 Durable Task 租约。

    返回 (kb_id, file_id, revision, lease)。`lease` 是入库认领要带的
    `processing_task_id` / `processing_owner`：生产入库总是经 Durable Task 调用
    `index_file` 并带上它们，仓库据此走**租约分支**（先锁 Task 校验租约、再 `FOR UPDATE`
    锁文件行），所以用例必须带上租约才等于真实路径。
    """
    from yuxi.storage.minio import get_minio_client

    schema = f"pytest_pub_{uuid.uuid4().hex}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        """提交或回滚一个独立事务。"""
        async with sessions.begin() as session:
            yield session

    monkeypatch.setattr(knowledge_file_repository.pg_manager, "get_async_session_context", session_context)

    minio_client = get_minio_client()
    bucket_name = minio_client.KB_BUCKETS["parsed"]
    object_name = f"{schema}/parsed/{schema}.md"
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    worker_id = f"worker_{uuid.uuid4().hex[:8]}"
    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeBaseModel.__table__.create)
            await connection.run_sync(KnowledgeFileModel.__table__.create)
            await connection.run_sync(TaskRecord.__table__.create)

        upload = await minio_client.aupload_file(bucket_name, object_name, SEED_CONTENT.encode())
        async with sessions.begin() as session:
            session.add(KnowledgeBaseModel(kb_id=schema, name="publish window", kb_type="milvus"))
            session.add(
                KnowledgeFileModel(
                    file_id=schema,
                    kb_id=schema,
                    filename="test.md",
                    file_type="md",
                    is_folder=False,
                    status=FileStatus.PARSED,
                    markdown_file=upload.url,
                    chunk_count=0,
                    token_count=0,
                )
            )
            session.add(
                TaskRecord(
                    id=task_id,
                    name="index",
                    type="knowledge_index",
                    status="running",
                    worker_id=worker_id,
                    lease_expires_at=utc_now_naive() + timedelta(seconds=600),
                )
            )
        async with sessions() as session:
            # 修订就是行上的 updated_at，与 HTTP 层 GET 内容时回传的是同一个值
            revision = utc_isoformat((await session.execute(select(KnowledgeFileModel))).scalar_one().updated_at)
        yield schema, schema, revision, {"processing_task_id": task_id, "processing_owner": worker_id}
    finally:
        await minio_client.adelete_objects_by_prefix(bucket_name, f"{schema}/parsed/{schema}.")
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def _install_save_gate(monkeypatch, *, position: str):
    """在编辑「写产物」这一步装确定性同步点，返回 (已到达, 放行)。

    position="after"：新对象已落盘、引用尚未切换——当前实现里真正的发布窗口；
    position="before"：编辑尚未落盘任何产物。当前实现里它落在写之前，而在**旧顺序**
    （先 CAS 落库、再覆盖确定性路径）里它正好落在 CAS 之后、覆盖之前，
    也就是「入库读到旧内容、随后同一对象被覆盖」的那个窗口。
    """
    arrived = asyncio.Event()
    proceed = asyncio.Event()
    original_save = KnowledgeBase._save_markdown_to_minio

    async def gated_save(self, kb, fid, content, *, object_name=None):
        if position == "before":
            arrived.set()
            await proceed.wait()
        url = await original_save(self, kb, fid, content, object_name=object_name)
        if position == "after":
            arrived.set()
            await proceed.wait()
        return url

    monkeypatch.setattr(KnowledgeBase, "_save_markdown_to_minio", gated_save)
    return arrived, proceed


def _executor() -> MilvusKB:
    """不连 Milvus：本用例只用基类里的发布与读取路径。"""
    return object.__new__(MilvusKB)


async def _claim_for_indexing(kb_id: str, file_id: str, lease: dict):
    """入库认领：复刻 index_file 的 claim——同一组状态、同一批 claim_data 字段。

    `claim_data` 里的 `processing_task_id` / `processing_owner` 让仓库走租约分支
    （先锁 Task 校验租约、再锁文件行），与生产入库一致；认领只校验状态与租约，
    不校验版本——这正是「编辑的版本闸挡不住入库」的原因，也是本文件要覆盖的窗口。
    """
    return await KnowledgeFileRepository().update_fields_if_status(
        kb_id=kb_id,
        file_id=file_id,
        allowed_statuses=INDEX_CLAIM_STATUSES,
        data={"status": FileStatus.INDEXING, "error_message": None, **lease},
    )


async def _row_content(executor: MilvusKB, kb_id: str, file_id: str) -> str:
    """行当前引用的产物内容——读者（含入库）应当读到的就是它。"""
    record = await KnowledgeFileRepository().get_by_file_id(file_id)
    return await executor._read_markdown_from_minio(record.markdown_file)


async def _assert_claim_wins_without_divergence(publish_store, monkeypatch, *, position: str) -> None:
    """在同步点放行入库认领，随后核对「入库读到的内容 == 行最终指向的内容」。"""
    kb_id, file_id, revision, lease = publish_store
    executor = _executor()
    arrived, proceed = _install_save_gate(monkeypatch, position=position)

    edit = asyncio.create_task(executor.update_file_markdown(kb_id, file_id, EDITED_CONTENT, "operator", revision))
    edit_conflict = False
    try:
        await asyncio.wait_for(arrived.wait(), timeout=10)

        claimed = await _claim_for_indexing(kb_id, file_id, lease)
        assert claimed is not None, "认领本应成功：编辑还没切换引用，行仍是 parsed"
        # 入库读的是它认领那一刻返回的引用（index_file 用的是 claimed_record）
        indexed_content = await executor._read_markdown_from_minio(claimed.markdown_file)

        proceed.set()
        try:
            await asyncio.wait_for(edit, timeout=10)
        except KBFileStateConflictError:
            edit_conflict = True
    finally:
        proceed.set()
        if not edit.done():
            edit.cancel()
        await asyncio.gather(edit, return_exceptions=True)

    record = await KnowledgeFileRepository().get_by_file_id(file_id)

    # 核心不变量先断言：入库读到的内容 == 行最终指向的内容。旧顺序（先 CAS 再覆盖确定性
    # 路径）下这里必然分叉——入库读到旧内容，而它读的那个对象随后被覆盖成新内容，
    # 行指向的还是同一个路径。这条断言是本文件的负向能力所在，故排在互斥断言之前。
    assert await executor._read_markdown_from_minio(record.markdown_file) == indexed_content

    # 互斥：认领已把状态推进到 indexing，编辑就不该同时生效（两边都是单条原子 UPDATE）
    assert record.status == FileStatus.INDEXING
    assert edit_conflict, "认领已推进状态，编辑不应同时成功"


async def test_index_claim_after_object_write_reads_the_published_content(publish_store, monkeypatch):
    """产物已落盘、引用尚未切换时入库抢先：入库读到的内容必须仍是行最终指向的内容。

    这是当前实现真正的发布窗口：引用切换与状态 CAS 同批下发，因此认领要么看到切换前的
    引用、要么看到切换后的引用，两者都与行最终指向的内容一致。
    """
    await _assert_claim_wins_without_divergence(publish_store, monkeypatch, position="after")


async def test_index_claim_before_object_write_reads_the_published_content(publish_store, monkeypatch):
    """编辑尚未落盘产物时入库抢先，最终行指向的内容仍须与入库读到的内容一致。

    这个同步点在当前实现里落在写之前；在旧顺序（先 CAS、再覆盖确定性路径）里它落在
    CAS 之后、覆盖之前，正是维护者描述的失败窗口——把实现改回旧顺序时本用例会在
    上面那条不变量上失败，因此它是新发布顺序的负向回归。
    """
    await _assert_claim_wins_without_divergence(publish_store, monkeypatch, position="before")


async def test_edit_publish_inside_index_claim_window_yields_to_the_claim(publish_store):
    """反向顺序：引用先切换，入库认领拿到的就是新引用，读到的内容与行一致。"""
    kb_id, file_id, revision, lease = publish_store
    executor = _executor()

    await executor.update_file_markdown(kb_id, file_id, EDITED_CONTENT, "operator", revision)

    claimed = await _claim_for_indexing(kb_id, file_id, lease)
    assert claimed is not None, "编辑不改状态，认领仍应成功"
    indexed_content = await executor._read_markdown_from_minio(claimed.markdown_file)

    assert indexed_content == EDITED_CONTENT
    assert await _row_content(executor, kb_id, file_id) == indexed_content


async def test_stale_revision_does_not_publish_object(publish_store):
    """版本落空时不切换引用：行仍指旧对象，且旧对象内容未被改动。"""
    kb_id, file_id, revision, _ = publish_store
    executor = _executor()

    # 先让行前进一个版本，制造过期修订
    await KnowledgeFileRepository().update_fields(file_id=file_id, kb_id=kb_id, data={"updated_by": "someone-else"})

    with pytest.raises(KBFileStateConflictError):
        await executor.update_file_markdown(kb_id, file_id, EDITED_CONTENT, "operator", revision)

    assert await _row_content(executor, kb_id, file_id) == SEED_CONTENT
