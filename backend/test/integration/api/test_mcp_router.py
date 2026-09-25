"""通过共享 Compose API 与本地 MCP 协议对端验证管理连接检查。"""

import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


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
            assert response.json()["detail"] == "MCP 连接失败，请检查服务地址、凭据和网络后重试"
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
