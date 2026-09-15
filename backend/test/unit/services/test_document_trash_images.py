"""图片对象键解析及旧图片精确授权回归。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.document_trash_service import DocumentTrashService, image_object_paths


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("![图](/api/knowledge/databases/kb/images/kb-images/a%20b.png)", {"kb-images/a b.png"}),
        ("![图](/api/knowledge/databases/kb/images/kb-images/a%29b.png)", {"kb-images/a)b.png"}),
        ("![图](https://example.test/kb-images/%E4%B8%AD%E6%96%87.png)", {"kb-images/中文.png"}),
        ("![图](https://example.test/kb-images/a.png?download=1#preview)", {"kb-images/a.png"}),
        ("![图](https://example.test/kb-images/a.png#preview)", {"kb-images/a.png"}),
        ("![图](kb-images/a.png.backup)", {"kb-images/a.png.backup"}),
        ("![图](kb-images/%2e%2e/secret.png) ![图](kb-images/a%5Cb.png)", set()),
        ("![图](kb-images/a.png) ![图](kb-images/a.png)", {"kb-images/a.png"}),
    ],
)
def test_image_keys_decode_after_tokenization(markdown, expected):
    """按完整编码键提取，再解码，不把文件名空格或括号当成边界。"""
    assert image_object_paths(markdown) == expected


@pytest.mark.asyncio
async def test_legacy_image_permission_never_uses_prefix_match():
    """活跃文档的较长对象名不为已删除短对象名授权。"""
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service.repository.list_storage_references.return_value = [SimpleNamespace(deleted_at=None)]
    service._markdown = AsyncMock(return_value="![图](kb-images/a.png.backup)")
    assert not await service.image_is_active("kb", "kb-images/a.png")
    assert await service.image_is_active("kb", "kb-images/a.png.backup")


@pytest.mark.asyncio
async def test_legacy_image_permission_matches_encoded_space_exactly():
    """代理收到解码路径时仍能准确关联编码markdown链接。"""
    service = DocumentTrashService()
    service.repository = AsyncMock()
    service.repository.list_storage_references.return_value = [SimpleNamespace(deleted_at=None)]
    service._markdown = AsyncMock(return_value="![图](kb-images/a%20b.png)")
    assert await service.image_is_active("kb", "kb-images/a b.png")
    assert not await service.image_is_active("kb", "kb-images/a")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
async def test_purge_resolves_executor_before_tree_lock_and_always_releases(monkeypatch, failure):
    """成功、失败和取消均释放树锁，缓存读取发生在树锁外。"""
    from contextlib import asynccontextmanager
    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    events = []
    executor = object()

    async def get_executor(kb_id):
        events.append("executor")
        return executor

    @asynccontextmanager
    async def tree_lock(self, kb_id):
        events.append("tree_enter")
        try:
            yield
        finally:
            events.append("tree_exit")

    @asynccontextmanager
    async def storage_lock(self):
        events.append("global_enter")
        try:
            yield
        finally:
            events.append("global_exit")

    service = DocumentTrashService()

    async def purge_locked(row, received_executor):
        assert received_executor is executor
        events.append("purge")
        if failure:
            raise failure()

    monkeypatch.setattr(knowledge_base, "get_kb_executor", get_executor)
    monkeypatch.setattr(KnowledgeFileRepository, "lock_file_tree", tree_lock)
    monkeypatch.setattr(KnowledgeFileRepository, "lock_storage_references", storage_lock)
    monkeypatch.setattr(service, "_purge_locked", purge_locked)
    row = SimpleNamespace(kb_id="kb", is_folder=False)
    if failure:
        with pytest.raises(failure):
            await service._purge_one(row)
    else:
        await service._purge_one(row)
    assert events == ["executor", "global_enter", "tree_enter", "purge", "tree_exit", "global_exit"]
