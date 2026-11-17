"""知识库选择只收窄当前读取权限。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.runtime import knowledge_base
from yuxi.modules.knowledge.services import access as knowledge_access
from yuxi.modules.knowledge.services.access import visible_knowledge_bases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uid", "selection", "expected"),
    [("u1", "all", ["kb-a", "kb-b"]), ("u1", ["kb-a", "private"], ["kb-a"]), ("u1", [], []), ("", "all", [])],
)
async def test_selection_only_narrows_read_permissions(monkeypatch, uid, selection, expected):
    """全部、指定和空选择都不能扩大用户读取范围。"""
    readable = [SimpleNamespace(kb_id=kb_id, name="Docs", description=None, kb_type="milvus") for kb_id in ("kb-a", "kb-b")]
    monkeypatch.setattr(knowledge_access, "get_knowledge_bases_by_uid", AsyncMock(return_value=readable))

    result = await visible_knowledge_bases(uid, selection=selection)
    assert [kb["kb_id"] for kb in result] == expected


@pytest.mark.asyncio
async def test_no_knowledge_bases_returns_empty(monkeypatch):
    """没有知识库时默认选择正常返回空范围。"""
    monkeypatch.setattr(knowledge_access, "get_knowledge_bases_by_uid", AsyncMock(return_value=[]))
    assert await visible_knowledge_bases("u1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["kb-a", [" "]])
async def test_invalid_selection_fails(selection):
    """错误类型和空 ID 显式失败，不能扩大为全部资源。"""
    with pytest.raises(ValueError, match="知识库选择必须"):
        await visible_knowledge_bases("u1", selection=selection)
