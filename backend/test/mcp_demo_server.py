from __future__ import annotations
import argparse
import asyncio
import contextvars
import logging
import os
import sys
from typing import Any
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from contextlib import asynccontextmanager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.fastmcp.server import StreamableHTTPASGIApp

# 简体中文注释与日志规范 (RULE[user_global])
logger = logging.getLogger("mcp_demo_server")

# 用于在不同传输协议下传递当前请求身份上下文的 ContextVar
current_request_headers_var = contextvars.ContextVar("current_request_headers", default=None)

# 实例化 MCP 核心服务对象
server = Server("yuxi-mcp-demo-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """根据身份或环境变量返回过滤后的三级权限工具列表"""
    headers = current_request_headers_var.get() or {}
    
    # 优先级: HTTP Headers > 系统环境变量 (兼容 stdio 与 sse 两种环境的测试)
    dept_id = headers.get("x-yuxi-department") or headers.get("x-department-id") or os.environ.get("X_DEPARTMENT_ID")
    user_id = headers.get("x-yuxi-user") or headers.get("x-user-id") or os.environ.get("X_USER_ID")
    auth_token = headers.get("authorization") or os.environ.get("AUTHORIZATION")
    
    logger.info(f"Listing tools - AuthToken: {auth_token}, DeptID: {dept_id}, UserID: {user_id}")
    
    # 基础路由工具 (全局可见)
    tools = [
        types.Tool(
            name="echo_global",
            description="全局通用工具，无须任何权限即可访问",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要回显的内容"}
                },
                "required": ["message"]
            },
        )
    ]
    
    # 部门级别权限工具
    if dept_id:
        tools.append(
            types.Tool(
                name="echo_dept_data",
                description=f"部门级别受限工具，当前已授权部门ID: {dept_id}",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "查询参数"}
                    },
                    "required": ["query"]
                },
            )
        )
        
    # 用户个人级别权限工具
    if user_id:
        tools.append(
            types.Tool(
                name="echo_user_profile",
                description=f"个人受限工具，当前已授权用户ID: {user_id}",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dummy": {"type": "string", "description": "占位参数"}
                    }
                },
            )
        )
        
    return tools


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    """执行工具回显结果"""
    logger.info(f"Calling tool: {name} with args: {arguments}")
    args = arguments or {}
    
    if name == "echo_global":
        msg = args.get("message", "")
        return [types.TextContent(type="text", text=f"[Global Output] 回显内容: {msg}")]
        
    elif name == "echo_dept_data":
        query = args.get("query", "")
        return [types.TextContent(type="text", text=f"[Department Output] 数据查询回显: {query}")]
        
    elif name == "echo_user_profile":
        return [types.TextContent(type="text", text="[User Output] 成功获取用户专有敏感配置与画像数据")]
        
    else:
        raise ValueError(f"Unknown tool: {name}")


# =============================================================================
# === SSE 与 Streamable HTTP 传输协议支持 (FastAPI) ===
# =============================================================================

session_manager = StreamableHTTPSessionManager(
    app=server,
    stateless=True,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with session_manager.run():
        yield

app = FastAPI(title="MCP Demo Server", lifespan=lifespan)
app.mount("/mcp", StreamableHTTPASGIApp(session_manager))

sse_transport = SseServerTransport("/messages")

@app.post("/oauth/token")
async def oauth_token(request: Request):
    """
    模拟 OAuth2 认证端点。
    返回一个 15 秒过期的 access_token，用以充分验证 Yuxi 后台的“短期 Token 自动失效与刷新”链路。
    """
    logger.info("Handling OAuth token request...")
    return {
        "access_token": "mock_access_token_123456",
        "refresh_token": "mock_refresh_token_789000",
        "expires_in": 15,  # 15 秒过期，利于测试
        "token_type": "Bearer",
        "scope": "read write"
    }

class SSEEndpoint:
    async def __call__(self, scope, receive, send):
        """建立 SSE 长连接通道，并将当前的 Headers 存入 ContextVar"""
        headers_dict = {k.decode('utf-8', errors='replace'): v.decode('utf-8', errors='replace') for k, v in scope.get("headers", [])}
        logger.info(f"New SSE connection attempt. Headers: {headers_dict}")
        
        # 注入 ContextVar，使得在该长连接处理循环下的所有 list/call_tool 能读取到 header
        token = current_request_headers_var.set(headers_dict)
        try:
            async with sse_transport.connect_sse(
                scope, receive, send
            ) as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="yuxi-mcp-demo-server",
                        server_version="1.0.0",
                        capabilities=server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )
        finally:
            current_request_headers_var.reset(token)

app.add_route("/sse", SSEEndpoint(), methods=["GET"])

class MessagesEndpoint:
    async def __call__(self, scope, receive, send):
        """接收 SSE 通道发来的具体 JSON-RPC 请求"""
        await sse_transport.handle_post_message(scope, receive, send)

app.add_route("/messages", MessagesEndpoint(), methods=["POST"])


# =============================================================================
# === Stdio 传输协议支持 (本地子进程) ===
# =============================================================================

async def run_stdio():
    """以 Stdio 形式在控制台管道中拉起"""
    logger.info("Starting Stdio server transport...")
    
    # Stdio 模式下从系统环境变量读取 headers
    current_request_headers_var.set({k.lower().replace("_", "-"): v for k, v in os.environ.items()})
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="yuxi-mcp-demo-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

# =============================================================================
# === 启动入口 ===
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Mock MCP Demo Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="sse",
        help="传输协议类型 (默认为 sse)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8999,
        help="FastAPI SSE 服务的端口号"
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        asyncio.run(run_stdio())
    else:
        logger.info(f"Starting FastAPI Server (transport: {args.transport}) on port {args.port}...")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
