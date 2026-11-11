"""通过共享 Compose API 与本地 MCP 协议对端验证管理连接检查。"""

import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    "mode,enabled",
    [
        ("empty", False),
        ("empty", True),
        ("tool", False),
        ("error", False),
        ("refused", False),
        ("error", True),
        ("refused", True),
    ],
)
async def test_management_connection_uses_real_protocol_and_preserves_state(test_client, admin_headers, mode, enabled):
    """真实管理 API 完成协议检查，成功或失败均保留 PostgreSQL 配置。"""
    methods = []
    headers = []

    class ProtocolHandler(BaseHTTPRequestHandler):
        """无 session 的本地 Streamable HTTP MCP 协议对端。"""

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = request.get("method")
            methods.append(method)
            headers.append(self.headers.get("Authorization"))
            if mode == "error":
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"fixture-secret-response")
                return
            if "id" not in request:
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": []
                    if mode == "empty"
                    else [
                        {
                            "name": "fixture_tool",
                            "description": "Fixture tool",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            else:
                raise AssertionError(f"Unexpected MCP method: {method}")
            payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def log_message(self, *_):
            pass

    protocol = ThreadingHTTPServer(("127.0.0.1", 0), ProtocolHandler)
    protocol_thread = Thread(target=protocol.serve_forever, daemon=True)
    protocol_thread.start()
    refused = socket.socket()
    refused.bind(("127.0.0.1", 0))
    port = refused.getsockname()[1] if mode == "refused" else protocol.server_port
    slug = "pytest-mcp-inspect-" + uuid4().hex[:12]
    server_path = f"/api/system/mcp-servers/{slug}"
    conn = None
    created = False
    try:
        # 在 api 容器内运行，协议对端与被测 API 共用 loopback 网络。
        response = await test_client.post(
            "/api/system/mcp-servers",
            headers=admin_headers,
            json={
                "slug": slug,
                "name": "Pytest MCP inspection",
                "transport": "streamable_http",
                "url": f"http://127.0.0.1:{port}/mcp",
                "headers": {"Authorization": "Bearer fixture-header"},
                "timeout": 1,
                "sse_read_timeout": 1,
            },
        )
        assert response.status_code == 200, response.text
        created = True
        response = await test_client.put(f"{server_path}/status", headers=admin_headers, json={"enabled": enabled})
        assert response.status_code == 200, response.text
        response = await test_client.put(f"{server_path}/tools/fixture_tool/toggle", headers=admin_headers)
        assert response.status_code == 200, response.text

        conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
        before = await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug)
        assert before is not None
        assert bool(before["enabled"]) is enabled
        assert json.loads(before["disabled_tools"]) == ["fixture_tool"]

        response = await test_client.post(f"{server_path}/test", headers=admin_headers)
        if mode in {"error", "refused"}:
            assert response.status_code == 502, response.text
            assert response.json()["detail"] == "连接器连接失败，请检查服务地址、凭据和网络后重试"
            assert "fixture-secret-response" not in response.text
            assert "fixture-header" not in response.text
        else:
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert response.json()["tool_count"] == (0 if mode == "empty" else 1)
            assert "initialize" in methods and "tools/list" in methods
            assert headers and all(value == "Bearer fixture-header" for value in headers)
        if mode == "error":
            assert "initialize" in methods
        after = await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug)
        assert after == before
    finally:
        try:
            if created:
                response = await test_client.delete(server_path, headers=admin_headers)
                assert response.status_code == 200, response.text
                if conn is not None:
                    assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) is None
        finally:
            if conn is not None:
                await conn.close()
            refused.close()
            protocol.shutdown()
            protocol.server_close()
            protocol_thread.join(timeout=5)


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"icon": "📚"}, "extra_forbidden"),
        ({"tags": ["文档"]}, "extra_forbidden"),
        ({"transport": "stdio"}, "传输类型"),
        ({"transport": "streamable_http", "command": "sh"}, "extra_forbidden"),
        ({"transport": "streamable_http", "args": []}, "extra_forbidden"),
        ({"transport": "streamable_http", "env": {}}, "extra_forbidden"),
        ({"transport": "streamable_http", "url": "file:///tmp/mcp"}, "HTTP URL"),
        ({"transport": "streamable_http", "url": "https://example.com:bad/mcp"}, "HTTP URL"),
        ({"transport": "streamable_http", "url": "https://exa mple.com/mcp"}, "HTTP URL"),
        ({"transport": "streamable_http", "url": "https://example.com/\u0000mcp"}, "HTTP URL"),
        ({"transport": "sse", "timeout": -1}, "timeout"),
        ({"transport": "sse", "timeout": True}, "timeout"),
        ({"transport": "sse", "timeout": 1.0}, "timeout"),
        ({"transport": "sse", "timeout": "30"}, "timeout"),
        ({"transport": "sse", "sse_read_timeout": True}, "sse_read_timeout"),
    ],
)
async def test_remote_config_rejected_before_persistence(test_client, admin_headers, payload, reason):
    """非法远程配置通过真实 HTTP 拒绝且 PostgreSQL 无记录。"""
    slug = "pytest-mcp-rejected-" + uuid4().hex[:12]
    response = await test_client.post(
        "/api/system/mcp-servers",
        headers=admin_headers,
        json={"slug": slug, "name": "Pytest rejected MCP", "url": "https://example.com/mcp", **payload},
    )
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        assert response.status_code in (400, 422), response.text
        assert reason in response.text or (response.status_code == 400 and response.json()["detail"].startswith("连接器配置无效"))
        assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) is None
    finally:
        try:
            # 负向 guard 退化而意外写入时，也只回收本用例的唯一资源。
            response = await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
            assert response.status_code in (200, 404), response.text
            assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) is None
        finally:
            await conn.close()


async def test_manifest_metadata_persisted_and_invalid_update_preserves_connection(test_client, admin_headers):
    """清单展示信息落库，非法更新不得改变既有远程连接。"""
    from yuxi.modules.extensions.mcp.config import normalize_mcp_manifest_entry

    slug = "pytest-mcp-manifest-" + uuid4().hex[:12]
    path = f"/api/system/mcp-servers/{slug}"
    config = normalize_mcp_manifest_entry(
        slug,
        {
            "type": "http",
            "url": "https://example.com/mcp",
            "timeout": 30,
            "extra_data": {"name": "Pytest manifest", "description": "Example docs"},
        },
    )
    response = await test_client.post("/api/system/mcp-servers", headers=admin_headers, json=config)
    assert response.status_code == 200, response.text
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        before = await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug)
        assert before["name"] == "Pytest manifest"
        assert before["transport"] == "streamable_http"
        assert before["description"] == "Example docs"
        assert not {"command", "args", "env", "extra_data", "icon", "tags"} & set(before.keys())
        for payload, reason in [
            ({"url": "file:///tmp/mcp"}, "HTTP URL"),
            ({"url": "https://example.com:bad/mcp"}, "HTTP URL"),
            ({"url": "https://exa mple.com/mcp"}, "HTTP URL"),
            ({"url": "https://example.com/\u0000mcp"}, "HTTP URL"),
            ({"timeout": True}, "timeout"),
            ({"sse_read_timeout": True}, "sse_read_timeout"),
            ({"timeout": 1.0}, "timeout"),
            ({"sse_read_timeout": "30"}, "sse_read_timeout"),
        ]:
            response = await test_client.put(path, headers=admin_headers, json=payload)
            assert response.status_code in (400, 422), response.text
            assert reason in response.text or (response.status_code == 400 and response.json()["detail"].startswith("连接器配置无效"))
            assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) == before
    finally:
        await test_client.delete(path, headers=admin_headers)
        assert await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = $1", slug) is None
        await conn.close()
