"""通过真实 API、PostgreSQL 与 MinIO 验证内置 MCP。"""

import json
import uuid
from http.server import BaseHTTPRequestHandler
from threading import Barrier

import pytest
import pytest_asyncio

from test.support.openai_replay_server import send_json_response, serve_replay_handler
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.client import MinIOClient
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.mcp.auth import create_knowledge_token
from yuxi.modules.knowledge.models import KnowledgeFile

# 所有资源由下方及公共 fixture 创建回收；MCP 不创建 Session，无需扫描共享工作目录。
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def mcp_document(test_client, admin_headers, knowledge_base_resource):
    """创建私有测试库中的持久文档，并随 fixture 回收对象与知识库。"""
    kb_id = knowledge_base_resource["kb_id"]
    owner = await test_client.get("/api/auth/me", headers=admin_headers)
    assert owner.status_code == 200
    response = await test_client.put(
        f"/api/knowledge/knowledge-bases/{kb_id}",
        headers=admin_headers,
        json={
            "name": f"pytest_mcp_private_{uuid.uuid4().hex}",
            "description": "MCP private document fixture",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [owner.json()["uid"]]},
                "manage_scope": None,
            },
        },
    )
    assert response.status_code == 200, response.text
    file_id = f"pytest_mcp_{uuid.uuid4().hex[:12]}"
    object_name = f"{kb_id}/parsed/{file_id}.md"
    minio = get_minio_client()
    uploaded = await minio.aupload_file(
        MinIOClient.KB_BUCKETS["parsed"], object_name, b"First line\nMCP fixture needle\nLast line", content_type="text/markdown"
    )
    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as db:
            db.add(
                KnowledgeFile(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename="mcp-proof.md",
                    file_type="md",
                    markdown_file=uploaded.url,
                    status="parsed",
                    is_folder=False,
                )
            )
        yield kb_id, file_id
    finally:
        await minio.adelete_file(MinIOClient.KB_BUCKETS["parsed"], object_name)
        await pg_manager.close()


async def call_tool(client, headers, name, arguments, *, error=False):
    """读取真实 MCP 返回，并对成功与错误语义断言。"""
    response = await client.post(
        "/api/mcp",
        headers={**headers, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert response.status_code == 200, response.text
    if name == "open_kb_document" and not error:
        assert len(response.content) <= 64 * 1024
    result = response.json()["result"]
    assert result["isError"] is error, result
    text = result["content"][0]["text"]
    if not error:
        decoded = json.loads(text)
        assert result["structuredContent"] == ({"knowledge_bases": decoded} if name == "list_kbs" else decoded)
    return text


@pytest_asyncio.fixture
async def query_kbs(test_client, admin_headers, standard_user):
    """创建两个持久知识库，检索经过真实 HTTP 访问确定性 Dify 服务。"""
    kb_ids = []
    requests = []
    parameters = []
    first_pair = Barrier(2)
    owner = await test_client.get("/api/auth/me", headers=admin_headers)
    assert owner.status_code == 200, owner.text
    granted_uids = [owner.json()["uid"], standard_user["user"]["uid"]]

    class Handler(BaseHTTPRequestHandler):
        """两个数据集返回同名文件的不同正文，独立于知识库聚合实现。"""

        def do_POST(self):
            """按实际请求的数据集返回固定片段。"""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            dataset = self.path.split("/")[-2]
            requests.append((dataset, payload["query"]))
            parameters.append(payload)
            # 首次多库调用必须同时到达两个真实 HTTP 请求，串行实现会超时。
            if len(requests) <= 2:
                first_pair.wait(timeout=5)
            send_json_response(
                self,
                200,
                {
                    "records": [
                        {
                            "score": 0.8,
                            "segment": {
                                "id": "same-chunk",
                                "content": f"{dataset} needle",
                                "document": {"id": "same-file", "name": "guide.md"},
                            },
                        }
                    ]
                },
            )

        def log_message(self, *_args):
            """测试服务不输出请求与凭据。"""

    # 与 API 同处 Compose 网络，允许 API 进程访问临时服务。
    with serve_replay_handler(Handler) as url:
        try:
            for dataset in ("products", "manual"):
                created = await test_client.post(
                    "/api/knowledge/knowledge-bases",
                    headers=admin_headers,
                    json={
                        "name": f"pytest_multi_kb_{dataset}_{uuid.uuid4().hex}",
                        "description": "Multi-KB query fixture",
                        "kb_type": "dify",
                        "additional_params": {"dify_api_url": url, "dify_token": "fixture-token", "dify_dataset_id": dataset},
                        "share_config": {
                            "version": 2,
                            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": granted_uids},
                            "manage_scope": None,
                        },
                    },
                )
                assert created.status_code == 200, created.text
                kb_ids.append(created.json()["kb_id"])
            yield kb_ids, requests, owner.json()["uid"], parameters
        finally:
            for kb_id in reversed(kb_ids):
                removed = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
                assert removed.status_code == 200, removed.text


async def test_mcp_queries_multiple_kbs_and_enforces_selection_and_current_permissions(
    test_client, admin_headers, standard_user, api_key_factory, query_kbs
):
    """真实协议返回双库正文；委托选择、撤权与 Public 单库契约分别生效。"""
    kb_ids, requests, owner_uid, parameters = query_kbs
    token = create_knowledge_token(uid=standard_user["user"]["uid"], selection=kb_ids)
    headers = {"Authorization": f"Bearer {token}"}
    result = json.loads(await call_tool(test_client, headers, "query_kb", {"kb_id": [*kb_ids, kb_ids[0]], "query_text": "needle"}))
    assert result["kb_ids"] == kb_ids
    assert [(item["kb_id"], item["file_id"], item["content"]) for item in result["results"]] == [
        (kb_ids[0], "same-file", "products needle"),
        (kb_ids[1], "same-file", "manual needle"),
    ]
    assert sorted(requests) == [("manual", "needle"), ("products", "needle")]

    key = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "MCP multi KB", "access_level": "knowledge"})
    external = json.loads(await call_tool(test_client, key.headers, "query_kb", {"kb_id": kb_ids, "query_text": "needle"}))
    assert external == result
    single = json.loads(await call_tool(test_client, headers, "query_kb", {"kb_id": kb_ids[0], "query_text": "needle"}))
    assert single == {"kb_id": kb_ids[0], "results": [result["results"][0]]}
    management_results = [{**single["results"][0], "score": 0.8}]
    saved = await test_client.put(
        f"/api/knowledge/knowledge-bases/{kb_ids[0]}/query-params", headers=admin_headers, json={"final_top_k": 3}
    )
    assert saved.status_code == 200, saved.text
    for overrides, top_k in [(None, 3), ({"final_top_k": 1}, 1), (None, 3)]:
        body = {"query": "needle"}
        if overrides is not None:
            body["meta"] = overrides
        tested = await test_client.post(f"/api/knowledge/knowledge-bases/{kb_ids[0]}/query-test", headers=admin_headers, json=body)
        assert tested.status_code == 200, tested.text
        assert tested.json() == management_results
        assert parameters[-1]["retrieval_model"]["top_k"] == top_k
    queried = await test_client.post(f"/api/knowledge/knowledge-bases/{kb_ids[0]}/query", headers=admin_headers, json={"query": "needle"})
    assert queried.status_code == 200, queried.text
    assert queried.json() == {"status": "success", "result": management_results}
    count = len(requests)
    rejected = await test_client.post(
        f"/api/knowledge/knowledge-bases/{kb_ids[0]}/query-test",
        headers=admin_headers,
        json={"query": "needle", "meta": {"final_top_k": 1000}},
    )
    assert rejected.status_code == 422, rejected.text
    assert "超出允许范围" in rejected.text and len(requests) == count
    public = await test_client.post("/api/v1/knowledge/tools/query_kb", headers=key.headers, json={"kb_id": kb_ids, "query_text": "needle"})
    assert public.status_code == 422, public.text
    assert any(error["loc"] == ["body", "kb_id"] and error["type"] == "string_type" for error in public.json()["detail"])

    narrowed = create_knowledge_token(uid=standard_user["user"]["uid"], selection=kb_ids[:1])
    requests.clear()
    denied = await call_tool(
        test_client, {"Authorization": f"Bearer {narrowed}"}, "query_kb", {"kb_id": kb_ids, "query_text": "needle"}, error=True
    )
    assert "404" in denied and "products needle" not in denied and "manual needle" not in denied
    assert requests == []

    current = await test_client.get(f"/api/knowledge/knowledge-bases/{kb_ids[1]}", headers=admin_headers)
    assert current.status_code == 200, current.text
    revoked = await test_client.put(
        f"/api/knowledge/knowledge-bases/{kb_ids[1]}",
        headers=admin_headers,
        json={
            "name": current.json()["name"],
            "description": current.json()["description"],
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [owner_uid]},
                "manage_scope": None,
            },
        },
    )
    assert revoked.status_code == 200, revoked.text
    denied = await call_tool(test_client, headers, "query_kb", {"kb_id": kb_ids, "query_text": "needle"}, error=True)
    assert "404" in denied and "products needle" not in denied and "manual needle" not in denied
    assert requests == []


async def test_mcp_reads_persisted_documents_and_hides_other_users_resources(
    test_client, admin_headers, api_key_factory, mcp_document, standard_user
):
    """新建 Key 经 shipping MCP 回读文档事实，另一用户已知 ID 仍不可见。"""
    kb_id, file_id = mcp_document
    owner = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "MCP document test", "access_level": "knowledge"})
    listed = json.loads(await call_tool(test_client, owner.headers, "list_kbs", {}))
    assert any(kb["kb_id"] == kb_id for kb in listed)
    files = json.loads(await call_tool(test_client, owner.headers, "search_file", {"kb_id": kb_id, "query": "mcp-proof"}))
    assert [f["file_id"] for f in files["files"]] == [file_id]
    opened = json.loads(await call_tool(test_client, owner.headers, "open_kb_document", {"kb_id": kb_id, "file_id": file_id}))
    assert opened["total_lines"] == 3
    assert "MCP fixture needle" in opened["content"]
    direct = await test_client.post(
        "/api/v1/knowledge/tools/open_kb_document", headers=owner.headers, json={"kb_id": kb_id, "file_id": file_id}
    )
    assert direct.status_code == 200
    assert opened == direct.json()
    found = json.loads(
        await call_tool(test_client, owner.headers, "find_kb_document", {"kb_id": kb_id, "file_id": file_id, "patterns": ["needle"]})
    )
    assert found["total_matches"] == 1
    assert found["windows"][0]["matched_lines"] == [2]

    other = await api_key_factory(
        {"request_id": str(uuid.uuid4()), "name": "MCP other user", "access_level": "knowledge"},
        owner_headers=standard_user["headers"],
        cleanup_headers=admin_headers,
    )
    listed = json.loads(await call_tool(test_client, other.headers, "list_kbs", {}))
    assert all(kb["kb_id"] != kb_id for kb in listed)
    for name, args in [("query_kb", {"kb_id": kb_id, "query_text": "needle"}), ("open_kb_document", {"kb_id": kb_id, "file_id": file_id})]:
        failure = await call_tool(test_client, other.headers, name, args, error=True)
        assert "404" in failure and "list_kbs" in failure and "MCP fixture needle" not in failure


async def test_mcp_real_auth_rejects_agents_invalid_and_revoked_keys(test_client, admin_headers, api_key_factory):
    """真实凭据的类型限制与撤销在每次协议请求生效。"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
    }
    accept = {"Accept": "application/json, text/event-stream"}
    for headers in ({}, {"Authorization": "Bearer yxkey_invalid"}, admin_headers):
        response = await test_client.post("/api/mcp", headers={**headers, **accept}, json=body)
        assert response.status_code == 401
    agents = await api_key_factory(
        {"request_id": str(uuid.uuid4()), "name": "MCP agents denial", "access_level": "agents", "app_id": "pytest-mcp"}
    )
    response = await test_client.post("/api/mcp", headers={**agents.headers, **accept}, json=body)
    assert response.status_code == 403
    key = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "MCP revoke test", "access_level": "knowledge"})
    response = await test_client.post("/api/mcp", headers={**key.headers, **accept}, json=body)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Yuxi Knowledge"
    deleted = await test_client.delete(f"/api/user/apikey/{key.key_id}", headers=admin_headers)
    assert deleted.status_code == 200
    response = await test_client.post(
        "/api/mcp", headers={**key.headers, **accept}, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )
    assert response.status_code == 401


async def test_mcp_document_window_has_byte_limit_and_current_permission(
    test_client, admin_headers, api_key_factory, mcp_document, standard_user
):
    """真实 MCP 和持久权限下，变长窗口可连续回读且不能越权。"""
    kb_id, file_id = mcp_document
    lines = [f"line-{index} " + "多字节正文" * 500 for index in range(8)]
    await get_minio_client().aupload_file(
        MinIOClient.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}.md", "\n".join(lines).encode(), content_type="text/markdown"
    )
    owner = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "bounded MCP document", "access_level": "knowledge"})
    offset = 0
    recovered = []
    while offset is not None:
        payload = json.loads(
            await call_tool(test_client, owner.headers, "open_kb_document", {"kb_id": kb_id, "file_id": file_id, "offset": offset})
        )
        assert len(json.dumps(payload, ensure_ascii=True).encode()) < 64 * 1024
        recovered.extend(line.split("\t", 1)[1] for line in payload["content"].splitlines())
        offset = payload["next_offset"]
    assert recovered == lines

    other = await api_key_factory(
        {"request_id": str(uuid.uuid4()), "name": "bounded MCP denied", "access_level": "knowledge"},
        owner_headers=standard_user["headers"],
        cleanup_headers=admin_headers,
    )
    failure = await call_tool(test_client, other.headers, "open_kb_document", {"kb_id": kb_id, "file_id": file_id}, error=True)
    assert "404" in failure and "多字节正文" not in failure
