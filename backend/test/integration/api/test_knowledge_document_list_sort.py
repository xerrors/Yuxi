"""知识库文件列表对筛选后的全部记录排序，再分页。"""

from datetime import UTC, datetime, timedelta

import pytest

from test.integration.api.test_knowledge_directory_imports import directory_kb as directory_kb
from yuxi.modules.knowledge.models import KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def _seed_sortable_files(kb_id, postgres_sessions):
    """构造默认顺序与各列顺序不同的一页放不下的记录。"""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    prefix = "sort-" + kb_id[-8:]
    specs = [
        ("1-folder", "m-folder", True, "done", 10, base),
        ("2-small", "z-small", False, "mystery", 1, base + timedelta(days=3)),
        ("3-mid", "a-mid", False, "indexed", 40, base + timedelta(days=2)),
        ("4-big", "b-big", False, "error_indexing", 5, base + timedelta(days=1)),
        ("5-tie", "c-tie", False, "indexed", 30, base + timedelta(days=2)),
    ]
    ids = {}
    async with postgres_sessions() as session:
        for key, filename, is_folder, status, token_count, created_at in specs:
            file_id = f"{prefix}-{key}"
            ids[key] = file_id
            session.add(
                KnowledgeFile(
                    kb_id=kb_id,
                    file_id=file_id,
                    filename=filename,
                    is_folder=is_folder,
                    status=status,
                    token_count=token_count,
                    created_at=created_at,
                )
            )
        await session.commit()
    return ids


async def _page_ids(test_client, admin_headers, kb_id, **params):
    response = await test_client.get(
        f"/api/knowledge/knowledge-bases/{kb_id}/documents",
        params={"page_size": 2, **params},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5
    return [item["file_id"] for item in body["items"]]


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "path", "sort_order": "ascend"},
        {"sort_by": "filename", "sort_order": "asc"},
        {"sort_by": "filename"},
        {"sort_order": "descend"},
        {"sort_by": "token_count", "sort_order": "ascend"},
    ],
)
async def test_invalid_document_sort_returns_400(test_client, admin_headers, directory_kb, params):
    response = await test_client.get(
        f"/api/knowledge/knowledge-bases/{directory_kb}/documents",
        params=params,
        headers=admin_headers,
    )
    assert response.status_code == 400, response.text


async def test_document_sort_orders_the_full_result_before_paging(test_client, admin_headers, directory_kb, postgres_sessions):
    """页内重排默认第一页得不到内容量最小、且位于默认第三页的文件。"""
    ids = await _seed_sortable_files(directory_kb, postgres_sessions)
    folder, small, mid, big, tie = (ids["1-folder"], ids["2-small"], ids["3-mid"], ids["4-big"], ids["5-tie"])

    default_page = await _page_ids(test_client, admin_headers, directory_kb, page=1)
    filename_page = await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="filename", sort_order="ascend")
    assert default_page == filename_page == [folder, mid]

    # 默认第一页是 folder、mid。若只排这一页，内容量升序仍从 mid 或 folder 开始，不会把默认第三页的 small 提前。
    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="content_amount", sort_order="ascend") == [small, big]
    assert await _page_ids(test_client, admin_headers, directory_kb, page=2, sort_by="content_amount", sort_order="ascend") == [folder, tie]
    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="content_amount", sort_order="descend") == [mid, tie]

    # filename 降序仍先排文件夹，再按文件名倒序；不能把文件夹排到文件后面。
    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="filename", sort_order="descend") == [folder, small]

    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="status", sort_order="ascend") == [folder, mid]
    assert await _page_ids(test_client, admin_headers, directory_kb, page=3, sort_by="status", sort_order="ascend") == [small]
    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="status", sort_order="descend") == [small, big]

    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="created_at", sort_order="ascend") == [folder, big]
    assert await _page_ids(test_client, admin_headers, directory_kb, page=1, sort_by="created_at", sort_order="descend") == [small, mid]
    # 相同创建时间不随降序倒转 file_id。
    assert await _page_ids(test_client, admin_headers, directory_kb, page=2, sort_by="created_at", sort_order="descend") == [tie, big]
