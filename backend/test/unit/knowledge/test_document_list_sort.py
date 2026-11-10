"""文件列表排序在查询前拒绝非法参数。跨页顺序由真实 PostgreSQL 测试证明。"""

import pytest

from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.mark.parametrize(
    ("sort_by", "sort_order"),
    [
        ("path", "ascend"),
        ("filename", "asc"),
        ("filename", None),
        (None, "descend"),
        ("token_count", "ascend"),
        ("filename", ""),
    ],
)
async def test_list_documents_rejects_invalid_sort_before_query(sort_by, sort_order):
    with pytest.raises(ValueError, match="Unsupported document sort"):
        await KnowledgeFileRepository().list_documents(kb_id="kb", sort_by=sort_by, sort_order=sort_order)
