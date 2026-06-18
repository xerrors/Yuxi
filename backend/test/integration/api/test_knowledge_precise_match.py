"""知识库精准匹配检索 integration 测试。

覆盖 query_keywords 工具背后「精准优先 + BM25 兜底」检索策略的端到端链路：
建 KB -> 上传文件 -> 异步索引 -> 通过 /query-test 接口验证 PHRASE_MATCH 精准命中
排在 BM25 模糊命中之前，且 metadata.is_precise_match 标记正确。

依赖 docker compose up -d 后的运行环境与 TEST_USERNAME/TEST_PASSWORD 超管凭据。
"""

import asyncio
import os

import pytest
from pymilvus import Collection, connections, utility

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_MILVUS_FLUSH_ALIAS = "_test_flush_kb"


# 包含完整短语「扭转减振器」的段落会被 PHRASE_MATCH 精准命中；
# 仅提及「减振器/振动」的段落只能由 BM25 模糊命中。
PRECISE_MATCH_DOC = """# 扭转减振器技术说明

扭转减振器是汽车传动系统中的关键部件，用于衰减发动机曲轴产生的扭转振动，保护传动系免受过大动载荷。扭转减振器通常安装在飞轮与离合器之间，通过匹配刚度与阻尼来吸收扭转振动能量。

减振器的设计需要兼顾工作频段与共振点位置。当系统振动频率接近共振点时，减振装置能够有效降低振幅，避免传动系结构件因疲劳而损坏。普通减振器一般通过橡胶元件或弹簧组提供阻尼。

维护保养方面，应定期检查减振元件的老化程度与连接紧固情况。一旦发现橡胶开裂或弹簧失效，需及时更换，否则会削弱减振能力并放大振动。日常使用中若出现异常振动噪声，应优先排查减振装置。

安装扭矩与配合间隙必须符合厂家规范。过紧会加剧磨损，过松则无法有效传递阻尼。建议在专业场地由技术人员操作，并使用专用工具校核安装尺寸。
"""


async def _wait_for_task(test_client, admin_headers, task_id: str, timeout: float = 120.0) -> dict:
    """轮询任务直到进入终态，返回 task 字典。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await test_client.get(f"/api/tasks/{task_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        task = resp.json().get("task", {})
        if task.get("status") in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.5)
    pytest.fail(f"Task {task_id} did not reach terminal status within {timeout}s")


async def _index_markdown(test_client, admin_headers, kb_id: str, filename: str, content: str) -> None:
    """上传 markdown 文件并以 auto_index 触发解析+索引，等待完成。"""
    upload_resp = await test_client.post(
        "/api/knowledge/files/upload",
        params={"kb_id": kb_id},
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        headers=admin_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    upload_json = upload_resp.json()
    minio_url = upload_json["file_path"]

    enqueue_resp = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents",
        json={
            "items": [minio_url],
            "params": {
                "content_type": "file",
                "auto_index": True,
                "content_hashes": {minio_url: upload_json["content_hash"]},
                "file_sizes": {minio_url: upload_json["size"]},
            },
        },
        headers=admin_headers,
    )
    assert enqueue_resp.status_code == 200, enqueue_resp.text
    task_id = enqueue_resp.json()["task_id"]

    task = await _wait_for_task(test_client, admin_headers, task_id)
    assert task["status"] == "success", f"indexing task failed: {task.get('error') or task.get('result')}"
    await _flush_kb_collection(kb_id)


async def _flush_kb_collection(kb_id: str) -> None:
    """显式 flush 集合，保证 PHRASE_MATCH 倒排索引在 growing segment 上可查。

    index_file insert 后未 flush，enable_match 倒排索引需 segment seal 后才稳定可见，
    否则索引后立即查询会偶发返回空。flush 是测试侧保证数据可见性的手段，不改变检索语义。
    """
    uri = os.getenv("MILVUS_URI", "http://milvus:19530")
    if connections.has_connection(_MILVUS_FLUSH_ALIAS):
        connections.disconnect(_MILVUS_FLUSH_ALIAS)
    connections.connect(alias=_MILVUS_FLUSH_ALIAS, uri=uri)
    try:
        if utility.has_collection(kb_id, using=_MILVUS_FLUSH_ALIAS):
            Collection(kb_id, using=_MILVUS_FLUSH_ALIAS).flush()
    finally:
        connections.disconnect(_MILVUS_FLUSH_ALIAS)


async def _query_test(test_client, admin_headers, kb_id: str, query: str, meta: dict) -> list[dict]:
    resp = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/query-test",
        json={"query": query, "meta": meta},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_query_test_precise_match_ranks_phrase_hits_first(test_client, admin_headers, knowledge_database):
    """精准匹配：含完整短语的 chunk 标记 is_precise_match=True 且排在模糊命中之前。"""
    kb_id = knowledge_database["kb_id"]
    await _index_markdown(test_client, admin_headers, kb_id, "torsional_damper.md", PRECISE_MATCH_DOC)

    chunks = await _query_test(
        test_client,
        admin_headers,
        kb_id,
        "扭转减振器",
        {
            "search_mode": "keyword",
            "precise_match": True,
            "phrase_match_terms": ["扭转减振器"],
            "final_top_k": 10,
        },
    )

    assert chunks, "精准匹配检索应返回非空结果"

    precise_flags = [bool(c.get("metadata", {}).get("is_precise_match")) for c in chunks]
    assert any(precise_flags), "应至少有一个精准命中（is_precise_match=True）的 chunk"

    # 精准块必须整体排在非精准块之前：最后一个 True 之后不应再出现 False
    last_true = max(i for i, v in enumerate(precise_flags) if v)
    has_false_after = any(not precise_flags[i] for i in range(last_true + 1, len(precise_flags)))
    assert not has_false_after, "精准命中必须排在 BM25 兜底命中之前"

    # 精准命中的 chunk 内容应包含完整短语，且带 bm25_score
    precise_chunks = [c for c in chunks if c.get("metadata", {}).get("is_precise_match")]
    assert all("扭转减振器" in c.get("content", "") for c in precise_chunks)
    assert all(isinstance(c.get("bm25_score"), float) for c in precise_chunks)


async def test_query_test_pure_bm25_omits_precise_flag(test_client, admin_headers, knowledge_database):
    """纯 BM25（不启用精准匹配）返回的 chunk 不应带 is_precise_match 标记。"""
    kb_id = knowledge_database["kb_id"]
    await _index_markdown(test_client, admin_headers, kb_id, "torsional_damper_plain.md", PRECISE_MATCH_DOC)

    chunks = await _query_test(
        test_client,
        admin_headers,
        kb_id,
        "扭转减振器",
        {"search_mode": "keyword", "final_top_k": 10},
    )

    assert chunks, "纯 BM25 检索应返回非空结果"
    assert all("is_precise_match" not in c.get("metadata", {}) for c in chunks), (
        "未启用 precise_match 时不应写入 is_precise_match 标记"
    )
