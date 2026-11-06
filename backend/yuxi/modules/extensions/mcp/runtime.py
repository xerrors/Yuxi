"""MCP 客户端、工具发现与远端传输校验。"""

import json
import re
from collections.abc import Callable
from typing import Any, cast

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient

from yuxi.infrastructure.observability.logging import logger

SUPPORTED_TRANSPORTS = ("sse", "streamable_http")


def validate_remote_transport(config: dict[str, Any]) -> None:
    """在客户端入口拒绝非远程连接。"""
    if config.get("transport") not in SUPPORTED_TRANSPORTS:
        raise ValueError("MCP 仅支持 sse 或 streamable_http，不支持 stdio 等其他 transport")
    if set(config) & {"command", "args", "env"}:
        raise ValueError("远程 MCP 不支持 command、args 或 env 进程字段")


async def get_mcp_client(
    server_configs: dict[str, Any] | None = None,
) -> MultiServerMCPClient:
    """校验远程配置并创建客户端，初始化失败交由调用方处理。"""
    for config in (server_configs or {}).values():
        validate_remote_transport(config)
    client = MultiServerMCPClient(server_configs or {})  # pyright: ignore[reportArgumentType]
    logger.info(f"Initialized MCP client with servers: {list((server_configs or {}).keys())}")
    return client


def to_camel_case(s: str) -> str:
    """Convert string to lowerCamelCase."""

    # Handle - and _
    s = re.sub(r"[-_]+(.)", lambda m: m.group(1).upper(), s)
    # Lowercase first letter
    if len(s) > 0:
        s = s[0].lower() + s[1:]
    return s


class SharedMCPAuth(httpx.Auth):
    """旧共享工具每次出站核对当前配置，不能跨认证方式沿用共享身份。"""

    requires_request_body = True

    def __init__(self, slug, config, *, uid=None, auth_mode="headers"):
        """绑定共享工具装配时捕获的连接字段。"""
        self.slug = slug
        self.uid, self.auth_mode = uid, auth_mode
        self.endpoint = (config.get("transport"), config.get("url"), dict(config.get("headers") or {}))

    async def async_auth_flow(self, request):
        """共享凭据复用同一出站检查。"""
        await self.validate_request(request)
        yield request

    async def validate_request(self, request):
        """配置撤销在网络副作用前拒绝，内部认证可直接复用此检查。"""
        from yuxi.infrastructure.postgres.manager import pg_manager
        from yuxi.modules.extensions.mcp.repository import get_mcp_server, runtime_mcp_policy
        from yuxi.shared.errors import PermissionDeniedError, StateConflictError

        async with pg_manager.get_async_session_context() as db:
            if self.uid is not None:
                policy = await runtime_mcp_policy(db, self.slug, self.uid)
                if policy is None or policy[0].is_login_locked():
                    raise PermissionDeniedError("MCP 用户不可用")
                server = policy[1]
            else:
                server = await get_mcp_server(db, self.slug)
            if not server or not server.enabled or server.auth_mode != self.auth_mode:
                raise StateConflictError("MCP 配置已改变，请重新运行")
            if (server.transport, server.url, server.headers or {}) != self.endpoint:
                raise StateConflictError("MCP 配置已改变，请重新运行")
            if request.method == "POST" and request.content:
                payload = json.loads(request.content)
                if payload.get("method") == "tools/call" and payload.get("params", {}).get("name") in (server.disabled_tools or []):
                    raise StateConflictError("MCP 工具已停用")


async def get_mcp_tools(
    server_slug: str,
    server_config: dict[str, Any] | None,
    disabled_tools: list[str] | None = None,
) -> list[Callable[..., Any]]:
    """发现当前远端工具，工具对象的生命周期由调用方拥有。"""
    if server_config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []
    validate_remote_transport(server_config)
    try:
        client_config = {key: value for key, value in server_config.items() if key not in {"disabled_tools", "auth_mode"}}
        client_config.setdefault("auth", SharedMCPAuth(server_slug, client_config, auth_mode=server_config.get("auth_mode", "headers")))
        client = await get_mcp_client({server_slug: client_config})
        tools = cast(list[Any], await client.get_tools())
        for tool in tools:
            if tool.metadata is None:
                tool.metadata = {}
            tool.metadata["id"] = f"mcp__{to_camel_case(server_slug)}__{to_camel_case(tool.name)}"
            tool.handle_tool_error = True
    except Exception as exc:
        logger.bind(component="mcp", server_slug=server_slug, error_type=type(exc).__name__, available=False).error(
            "component=mcp server_slug={} error_type={} available=false MCP 工具加载失败", server_slug, type(exc).__name__
        )
        raise RuntimeError(f"MCP 服务 '{server_slug}' 工具加载失败") from exc
    return [tool for tool in tools if tool.name not in (disabled_tools or [])]
