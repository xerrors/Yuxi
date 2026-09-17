"""KnowledgeBase.update_file_markdown 的行为断言。

覆盖点（按重要性）：
1. 已入库文件被编辑后：状态退回 parsed，且旧分块被清除——这是本功能的核心保证，
   否则会出现「状态是待入库、但检索里仍挂着旧内容向量」的隐蔽不一致。
2. 未入库文件被编辑时不触碰向量库（避免无谓往返）。
3. 各类非法输入在写 MinIO 之前就被拒绝（失败不留痕）。
4. 并发冲突与普通参数错误用不同异常区分（409 vs 400）。
"""

import types
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.base import (
    FileStatus,
    KBFileStateConflictError,
    KnowledgeBase,
)


class FakeKnowledgeBase(KnowledgeBase):
    """最小可实例化的 executor，用于验证基类里的通用流程。"""

    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}


class FakeFileRepository:
    """记录 CAS 调用的假仓库。"""

    def __init__(self, cas_result=None):
        self.cas_result = cas_result
        self.cas_calls = []

    async def update_fields_if_status(self, **kwargs):
        self.cas_calls.append(kwargs)
        return self.cas_result


def make_meta(**overrides):
    meta = {
        "file_id": "file_1",
        "kb_id": "kb_1",
        "filename": "a.pdf",
        "is_folder": False,
        "status": FileStatus.PARSED,
        "markdown_file": "http://minio/knowledgebases/kb_1/parsed/file_1.md",
    }
    meta.update(overrides)
    return meta


@pytest.fixture
def kb(tmp_path):
    return FakeKnowledgeBase(str(tmp_path))


def patch_repo(monkeypatch, repo):
    """update_file_markdown 内部是函数级 import，故 patch 模块属性。"""
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda *a, **kw: repo,
    )


def make_record(meta):
    """伪造 CAS 返回的 ORM 记录（_file_record_to_meta 只做属性读取）。"""
    return types.SimpleNamespace(
        file_id=meta["file_id"],
        kb_id=meta["kb_id"],
        filename=meta.get("filename"),
        original_filename=meta.get("original_filename"),
        file_type=meta.get("file_type"),
        parent_id=meta.get("parent_id"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_type=meta.get("content_type"),
        content_hash=meta.get("content_hash"),
        file_size=meta.get("file_size"),
        chunk_count=meta.get("chunk_count", 0),
        token_count=meta.get("token_count", 0),
        processing_params=meta.get("processing_params"),
        is_folder=meta.get("is_folder", False),
        error_message=None,
        created_by=None,
        created_at=None,
        updated_at=None,
        updated_by=None,
    )


@pytest.mark.asyncio
async def test_已入库文件编辑后退回待入库并清除旧分块(monkeypatch, kb):
    """核心保证：状态退回 parsed + chunk_count 归零 + 旧分块被清。"""
    meta = make_meta(status=FileStatus.INDEXED, chunk_count=42, token_count=999)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))

    save = AsyncMock(return_value="http://minio/.../file_1.md")
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)
    purge = AsyncMock()
    monkeypatch.setattr(kb, "purge_indexed_chunks", purge)

    repo = FakeFileRepository(cas_result=make_record({**meta, "status": FileStatus.PARSED}))
    patch_repo(monkeypatch, repo)

    result = await kb.update_file_markdown("kb_1", "file_1", "# new content", operator_id="u1")

    assert save.await_count == 1, "应写入新的解析产物"
    assert purge.await_count == 1, "已入库文件编辑后必须清除旧分块"
    assert purge.await_args.args[:2] == ("kb_1", "file_1")

    assert len(repo.cas_calls) == 1
    cas = repo.cas_calls[0]
    assert cas["allowed_statuses"] == {FileStatus.INDEXED}, (
        "CAS 必须只接受读取时观察到的那个状态。放宽成整个可编辑集合会打开这条路径："
        "读到时是 parsed（跳过清向量）→ 并发索引完成 → CAS 仍命中 → 状态变 parsed 但旧向量还在"
    )
    assert cas["data"]["status"] == FileStatus.PARSED
    assert cas["data"]["chunk_count"] == 0
    assert cas["data"]["token_count"] == 0
    assert cas["data"]["updated_by"] == "u1"

    assert result["status"] == FileStatus.PARSED


@pytest.mark.asyncio
async def test_未入库文件编辑时不触碰向量库(monkeypatch, kb):
    """parsed 状态的文件尚未入库，不应产生 Milvus 往返。"""
    meta = make_meta(status=FileStatus.PARSED)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))
    monkeypatch.setattr(kb, "_save_markdown_to_minio", AsyncMock(return_value="u"))
    purge = AsyncMock()
    monkeypatch.setattr(kb, "purge_indexed_chunks", purge)
    patch_repo(monkeypatch, FakeFileRepository(cas_result=make_record(meta)))

    await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert purge.await_count == 0, "未入库文件不应调用清除分块"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [FileStatus.ERROR_INDEXING, "done"])
async def test_其他可编辑状态也会清除旧分块(monkeypatch, kb, status):
    """error_indexing / 历史 done 状态同样可能残留分块。"""
    meta = make_meta(status=status)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))
    monkeypatch.setattr(kb, "_save_markdown_to_minio", AsyncMock(return_value="u"))
    purge = AsyncMock()
    monkeypatch.setattr(kb, "purge_indexed_chunks", purge)
    patch_repo(monkeypatch, FakeFileRepository(cas_result=make_record(meta)))

    await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert purge.await_count == 1


@pytest.mark.asyncio
async def test_无解析产物时拒绝且不写存储(monkeypatch, kb):
    """失败必须不留痕：既不写 MinIO，也不改状态。"""
    meta = make_meta(markdown_file=None)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)
    repo = FakeFileRepository()
    patch_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="尚未生成解析结果"):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert save.await_count == 0
    assert repo.cas_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   ", "\n\t "])
async def test_空内容被拒绝(monkeypatch, kb, content):
    """空 Markdown 会切出 0 个分块，产生「已入库却检索不到」的哑状态。"""
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=make_meta()))
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    with pytest.raises(ValueError, match="不能为空"):
        await kb.update_file_markdown("kb_1", "file_1", content)

    assert save.await_count == 0


@pytest.mark.asyncio
async def test_文件夹不支持编辑(monkeypatch, kb):
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=make_meta(is_folder=True)))
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    with pytest.raises(ValueError, match="文件夹"):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert save.await_count == 0


@pytest.mark.asyncio
async def test_超长内容被拒绝(monkeypatch, kb):
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=make_meta()))
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    oversized = "a" * (5 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="超出限制"):
        await kb.update_file_markdown("kb_1", "file_1", oversized)

    assert save.await_count == 0


@pytest.mark.asyncio
async def test_解析或入库中拒绝编辑并抛冲突异常(monkeypatch, kb):
    """并发冲突必须是 KBFileStateConflictError（映射 409），不是 ValueError（400）。"""
    monkeypatch.setattr(
        kb, "_get_file_meta", AsyncMock(return_value=make_meta(status=FileStatus.INDEXING))
    )
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    with pytest.raises(KBFileStateConflictError):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert save.await_count == 0


@pytest.mark.asyncio
async def test_不可编辑状态被拒绝(monkeypatch, kb):
    monkeypatch.setattr(
        kb, "_get_file_meta", AsyncMock(return_value=make_meta(status=FileStatus.UPLOADED))
    )
    save = AsyncMock()
    monkeypatch.setattr(kb, "_save_markdown_to_minio", save)

    with pytest.raises(KBFileStateConflictError):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert save.await_count == 0


@pytest.mark.asyncio
async def test_状态并发改变时抛冲突且不返回元数据(monkeypatch, kb):
    """CAS 未命中说明状态被别人改了，不能返回元数据造成「假成功」。"""
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=make_meta()))
    monkeypatch.setattr(kb, "_save_markdown_to_minio", AsyncMock(return_value="u"))
    monkeypatch.setattr(kb, "purge_indexed_chunks", AsyncMock())
    patch_repo(monkeypatch, FakeFileRepository(cas_result=None))

    with pytest.raises(KBFileStateConflictError):
        await kb.update_file_markdown("kb_1", "file_1", "# new")


@pytest.mark.asyncio
async def test_读取时未入库但期间并发入库完成时必须冲突(monkeypatch, kb):
    """还原「核心保证在并发下不成立」的那条路径。

    时序：T0 读到 parsed（was_indexed=False，跳过清向量）→ 写产物期间并发索引完成，
    文件真实状态变成 indexed 且已写入分块 → CAS。

    若 CAS 的 allowed_statuses 放宽成整个可编辑集合（含 indexed），它会命中并把状态改成
    parsed，而旧向量仍在——状态显示待入库、检索却命中旧内容，正是本方法要消除的不一致。
    收窄为「只接受 T0 观察到的状态」后，CAS 必然落空并抛 409；用户重试时读到的已是
    indexed，自然走清理分支。本用例在放宽时会失败（不再抛异常）。
    """

    class RacingRepository:
        """模拟并发索引：CAS 时文件的真实状态已经是 indexed。"""

        def __init__(self):
            self.calls = []

        async def update_fields_if_status(self, **kwargs):
            self.calls.append(kwargs)
            real_status = FileStatus.INDEXED
            return make_record(make_meta(status=real_status)) if real_status in kwargs["allowed_statuses"] else None

    meta = make_meta(status=FileStatus.PARSED)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))
    monkeypatch.setattr(kb, "_save_markdown_to_minio", AsyncMock(return_value="u"))
    purge = AsyncMock()
    monkeypatch.setattr(kb, "purge_indexed_chunks", purge)
    repo = RacingRepository()
    patch_repo(monkeypatch, repo)

    with pytest.raises(KBFileStateConflictError):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert repo.calls[0]["allowed_statuses"] == {FileStatus.PARSED}
    assert purge.await_count == 0, "T0 读到 parsed，本就不该清向量；冲突应由 CAS 拦下"


@pytest.mark.asyncio
async def test_清除分块失败时异常向上传播(monkeypatch, kb):
    """purge 失败不能让编辑「部分成功」—— 状态不应被改成 parsed。"""
    meta = make_meta(status=FileStatus.INDEXED)
    monkeypatch.setattr(kb, "_get_file_meta", AsyncMock(return_value=meta))
    monkeypatch.setattr(kb, "_save_markdown_to_minio", AsyncMock(return_value="u"))
    monkeypatch.setattr(
        kb, "purge_indexed_chunks", AsyncMock(side_effect=RuntimeError("milvus down"))
    )
    repo = FakeFileRepository(cas_result=make_record(meta))
    patch_repo(monkeypatch, repo)

    with pytest.raises(RuntimeError, match="milvus down"):
        await kb.update_file_markdown("kb_1", "file_1", "# new")

    assert repo.cas_calls == [], "purge 失败时不应执行状态迁移"


def test_基类提供默认清除钩子且不抛错(tmp_path):
    """未覆写的 executor 走告警 + no-op，而不是让编辑功能整体不可用。"""
    import asyncio

    kb = FakeKnowledgeBase(str(tmp_path))
    assert asyncio.run(kb.purge_indexed_chunks("kb_1", "file_1")) is None


def test_所有文档型_executor_都必须覆写清除钩子():
    """遍历注册表，要求每个文档型 executor 自己声明清除钩子。

    只断言 MilvusKB 里存在这个名字是不够的：基类若把钩子改名，那种断言照样通过，
    而实际调用点会落到基类的 no-op 分支、静默漏清向量。supports_documents 的基类
    默认值是 True，所以「新增 executor 忘记覆写」走的正是默认路径，必须在契约上拦住。
    """
    import yuxi.knowledge.runtime  # noqa: F401  触发各 executor 注册
    from yuxi.knowledge.factory import KnowledgeBaseFactory

    document_types = {
        kb_type: kb_class
        for kb_type, kb_class in KnowledgeBaseFactory._kb_types.items()
        if getattr(kb_class, "supports_documents", False)
    }
    assert document_types, "注册表中应至少有一个文档型 executor，否则本测试失去意义"

    missing = sorted(kb_type for kb_type, kb_class in document_types.items() if "purge_indexed_chunks" not in kb_class.__dict__)
    assert not missing, f"这些文档型 executor 未覆写 purge_indexed_chunks，编辑解析产物后会静默漏清向量：{missing}"
