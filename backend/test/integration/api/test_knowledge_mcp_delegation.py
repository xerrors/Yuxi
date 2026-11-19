"""真实 HTTP 验证内部委托的身份、选择交集和当前权限。"""

import json
import os
import uuid
from datetime import timedelta

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.client import MinIOClient
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.models import User
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.modules.knowledge.mcp.auth import create_knowledge_token
from yuxi.modules.knowledge.mcp.client import call_knowledge_mcp
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
DOCUMENT_BYTES = b"First line\nDelegated MCP needle\nLast line\n"


async def _call(client, token, name, arguments):
    """读取 shipping MCP 的完整协议结果，不以 HTTP 成功替代工具成功。"""
    response = await client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _grant(uid):
    """为测试资源建立单用户读取范围。"""
    return {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        "manage_scope": None,
    }


@pytest_asyncio.fixture
async def delegated_document(test_client, admin_headers, standard_user, knowledge_base_resource, postgres_sessions):
    """创建持久正文，权限只授予本例普通用户。"""
    kb_id = knowledge_base_resource["kb_id"]
    name = f"pytest_delegation_{uuid.uuid4().hex}"
    uid = standard_user["user"]["uid"]
    updated = await test_client.put(
        f"/api/knowledge/knowledge-bases/{kb_id}",
        headers=admin_headers,
        json={"name": name, "description": "Delegation permission fixture", "share_config": _grant(uid)},
    )
    assert updated.status_code == 200, updated.text
    file_id = f"pytest_delegation_{uuid.uuid4().hex[:12]}"
    minio = get_minio_client()
    objects = [
        (MinIOClient.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}.md", DOCUMENT_BYTES),
    ]
    uploaded = []
    try:
        for bucket, key, content in objects:
            uploaded.append(await minio.aupload_file(bucket, key, content, content_type="application/octet-stream"))
        async with postgres_sessions() as db:
            db.add(
                KnowledgeFile(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename="delegation-proof.bin",
                    file_type="bin",
                    markdown_file=uploaded[0].url,
                    status="parsed",
                    is_folder=False,
                )
            )
            await db.commit()
        yield {"kb_id": kb_id, "file_id": file_id, "uid": uid, "name": name}
    finally:
        for bucket, key, _ in objects:
            await minio.adelete_file(bucket, key)


async def test_delegation_reads_persisted_content_and_limits_selection(test_client, delegated_document):
    """同一用户的显式选择和空选择不能因调用参数扩大。"""
    fixture = delegated_document
    token = create_knowledge_token(uid=fixture["uid"], selection=[fixture["kb_id"]])
    listed = await _call(test_client, token, "list_kbs", {"include_capabilities": True})
    assert listed["isError"] is False, listed
    assert [item["kb_id"] for item in listed["structuredContent"]["knowledge_bases"]] == [fixture["kb_id"]]
    assert listed["structuredContent"]["knowledge_bases"][0]["supports_documents"] is True
    assert listed["structuredContent"]["knowledge_bases"][0]["supports_document_read"] is True
    metadata = await _call(test_client, token, "get_kb_metadata", {"kb_id": fixture["kb_id"]})
    assert metadata["structuredContent"] == {"kb_id": fixture["kb_id"], "supported": True, "fields": []}
    opened = await _call(test_client, token, "open_kb_document", {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]})
    assert opened["isError"] is False, opened
    assert "Delegated MCP needle" in opened["structuredContent"]["content"]
    found = await _call(
        test_client,
        token,
        "find_kb_document",
        {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"], "patterns": ["needle"]},
    )
    assert found["isError"] is False, found
    assert found["structuredContent"]["windows"][0]["matched_lines"] == [2]
    empty = create_knowledge_token(uid=fixture["uid"], selection=[])
    listed = await _call(test_client, empty, "list_kbs", {"selection": "all", "uid": "another-user"})
    assert listed["structuredContent"]["knowledge_bases"] == []
    denied = await _call(test_client, empty, "open_kb_document", {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]})
    assert denied["isError"] is True and "404" in denied["content"][0]["text"]
    assert "Delegated MCP needle" not in json.dumps(denied)
    denied_metadata = await _call(test_client, empty, "get_kb_metadata", {"kb_id": fixture["kb_id"]})
    assert denied_metadata["isError"] is True and "404" in denied_metadata["content"][0]["text"]


async def test_same_delegation_token_cannot_survive_resource_revocation(test_client, admin_headers, delegated_document):
    """不等待凭据过期，Knowledge 立即执行当前权限。"""
    fixture = delegated_document
    token = create_knowledge_token(uid=fixture["uid"], selection="all")
    args = {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]}
    assert (await _call(test_client, token, "open_kb_document", args))["isError"] is False
    assert (await _call(test_client, token, "get_kb_metadata", {"kb_id": fixture["kb_id"]}))["isError"] is False
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    changed = await test_client.put(
        f"/api/knowledge/knowledge-bases/{fixture['kb_id']}",
        headers=admin_headers,
        json={"name": fixture["name"], "description": "Revoked fixture", "share_config": _grant(me.json()["uid"])},
    )
    assert changed.status_code == 200, changed.text
    denied = await _call(test_client, token, "open_kb_document", args)
    assert denied["isError"] is True and "404" in denied["content"][0]["text"]
    assert "Delegated MCP needle" not in json.dumps(denied)
    denied_metadata = await _call(test_client, token, "get_kb_metadata", {"kb_id": fixture["kb_id"]})
    assert denied_metadata["isError"] is True and "404" in denied_metadata["content"][0]["text"]


@pytest.mark.parametrize("mutation", ["expired", "audience", "purpose", "issuer", "signature", "duration"])
async def test_delegation_rejects_invalid_credentials_over_http(test_client, standard_user, mutation):
    """真实入口拒绝错误签名、用途、发行者、时限和其他 audience。"""
    token = create_knowledge_token(uid=standard_user["user"]["uid"], selection="all")
    payload = jwt.decode(token, options={"verify_signature": False})
    secret = os.environ["JWT_SECRET_KEY"]
    if mutation == "expired":
        payload["iat"] -= 600
        payload["exp"] -= 600
    elif mutation == "audience":
        payload["aud"] = "yuxi-know-api"
    elif mutation == "purpose":
        payload["purpose"] = "user-login"
    elif mutation == "issuer":
        payload["iss"] = "another-instance"
    elif mutation == "signature":
        secret = "another-signing-key-at-least-thirty-two-characters"
    else:
        payload["exp"] = payload["iat"] + 3600
    token = jwt.encode(payload, secret, algorithm="HS256")
    response = await test_client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 401, response.text


async def test_delegation_preserves_public_end_user_identity(test_client, admin_headers, delegated_document, postgres_sessions):
    """App 终端用户不能继承 Key 所有者的私有知识库权限。"""
    fixture = delegated_document
    async with postgres_sessions() as db:
        owner = await db.scalar(select(User).where(User.uid == fixture["uid"]))
        user = await UserRepository(db).get_or_create_public_end_user(
            owner=owner, app_id=f"pytest-mcp-{uuid.uuid4().hex}", end_user_id="delegation-reader"
        )
        await db.commit()
        uid = user.uid
    try:
        token = create_knowledge_token(uid=uid, selection="all")
        listed = await _call(test_client, token, "list_kbs", {})
        assert listed["isError"] is False, listed
        assert all(item["kb_id"] != fixture["kb_id"] for item in listed["structuredContent"]["knowledge_bases"])
        denied = await _call(test_client, token, "open_kb_document", {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]})
        assert denied["isError"] is True and "404" in denied["content"][0]["text"]
        granted = await test_client.put(
            f"/api/knowledge/knowledge-bases/{fixture['kb_id']}",
            headers=admin_headers,
            json={"name": fixture["name"], "description": "End user grant", "share_config": _grant(uid)},
        )
        assert granted.status_code == 200, granted.text
        opened = await _call(test_client, token, "open_kb_document", {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]})
        assert opened["isError"] is False, opened
        assert "Delegated MCP needle" in opened["structuredContent"]["content"]
        async with postgres_sessions() as db:
            owner = await db.scalar(select(User).where(User.uid == fixture["uid"]))
            owner.is_deleted = 1
            await db.commit()
        response = await test_client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 401, response.text
    finally:
        async with postgres_sessions() as db:
            owner = await db.scalar(select(User).where(User.uid == fixture["uid"]))
            owner.is_deleted = 0
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()


@pytest.mark.parametrize("state", ["deleted", "locked"])
async def test_existing_token_rejects_current_user_revocation(test_client, standard_user, postgres_sessions, state):
    """持久用户状态变化立即使旧委托失效。"""
    uid = standard_user["user"]["uid"]
    token = create_knowledge_token(uid=uid, selection="all")
    try:
        async with postgres_sessions() as db:
            user = await db.scalar(select(User).where(User.uid == uid))
            if state == "deleted":
                user.is_deleted = 1
            else:
                user.login_locked_until = utc_now() + timedelta(minutes=10)
            await db.commit()
        response = await test_client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 401, response.text
    finally:
        async with postgres_sessions() as db:
            user = await db.scalar(select(User).where(User.uid == uid))
            user.is_deleted = 0
            user.login_locked_until = None
            await db.commit()


@pytest.mark.parametrize("credential", ["api_key", "delegation"])
async def test_removed_download_tool_is_unknown(test_client, api_key_factory, delegated_document, credential):
    """外部 Key 和内部委托均不能发现或执行已移除的工具。"""
    if credential == "api_key":
        key = await api_key_factory({"request_id": str(uuid.uuid4()), "name": "MCP removed tool", "access_level": "knowledge"})
        token = key.headers["Authorization"][7:]
    else:
        token = create_knowledge_token(uid=delegated_document["uid"], selection=[delegated_document["kb_id"]])
    listed = await test_client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed.status_code == 200, listed.text
    assert {item["name"] for item in listed.json()["result"]["tools"]} == {
        "list_kbs",
        "get_kb_metadata",
        "query_kb",
        "search_file",
        "open_kb_document",
        "find_kb_document",
    }
    result = await _call(
        test_client,
        token,
        "download_kb_file",
        {"kb_id": delegated_document["kb_id"], "file_id": delegated_document["file_id"]},
    )
    assert result["isError"] is True
    assert result["content"][0]["text"] == "Unknown knowledge tool"


async def test_knowledge_delegation_cannot_authenticate_as_login(test_client, standard_user):
    """知识委托不能重放到普通登录 API。"""
    token = create_knowledge_token(uid=standard_user["user"]["uid"], selection="all")
    response = await test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401, response.text


async def test_internal_mcp_does_not_send_delegate_to_environment_proxy(delegated_document, monkeypatch):
    """部署代理不可接收内部 MCP 的委托凭据，负控地址无法建立连接。"""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    fixture = delegated_document
    try:
        opened = await call_knowledge_mcp(
            "open_kb_document",
            {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"]},
            uid=fixture["uid"],
            selection=[fixture["kb_id"]],
        )
        assert "Delegated MCP needle" in opened["content"]
    finally:
        await pg_manager.close()


async def test_standard_user_reads_reference_window_without_management_access(
    test_client, admin_headers, standard_user, delegated_document
):
    """普通用户读取真实原文窗口，管理接口拒绝，撤权后同一读取请求拒绝。"""
    fixture = delegated_document
    headers = standard_user["headers"]
    path = "/api/v1/knowledge/tools/open_kb_document"
    payload = {"kb_id": fixture["kb_id"], "file_id": fixture["file_id"], "line": 2, "window_size": 1}
    opened = await test_client.post(path, headers=headers, json=payload)
    assert opened.status_code == 200, opened.text
    assert opened.json()["start_line"] == opened.json()["end_line"] == 2
    assert opened.json()["content"].endswith("\tDelegated MCP needle")
    blocked = await test_client.get(
        f"/api/knowledge/knowledge-bases/{fixture['kb_id']}/documents/{fixture['file_id']}/basic", headers=headers
    )
    assert blocked.status_code == 403, blocked.text
    owner = await test_client.get("/api/auth/me", headers=admin_headers)
    revoked = await test_client.put(
        f"/api/knowledge/knowledge-bases/{fixture['kb_id']}",
        headers=admin_headers,
        json={"name": fixture["name"], "description": "Revoked reference", "share_config": _grant(owner.json()["uid"])},
    )
    assert revoked.status_code == 200, revoked.text
    denied = await test_client.post(path, headers=headers, json=payload)
    assert denied.status_code == 404, denied.text
    assert "Delegated MCP needle" not in denied.text
