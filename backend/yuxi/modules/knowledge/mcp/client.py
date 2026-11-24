"""知识服务的标准 MCP 客户端与内部请求认证。"""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from yuxi.bootstrap.capabilities import knowledge_enabled
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.mcp.config import MCPDelegationContextError
from yuxi.modules.knowledge.mcp import KNOWLEDGE_MCP_URL
from yuxi.modules.knowledge.mcp.auth import create_knowledge_token

KNOWLEDGE_MCP_TIMEOUT_SECONDS = 120


class KnowledgeMCPError(RuntimeError):
    """仅包含 MCP 边界允许公开的失败提示。"""


class KnowledgeMCPUnavailable(RuntimeError):
    """连接、认证或服务故障使本次运行的知识能力不可用。"""


async def call_knowledge_mcp(
    name: str,
    arguments: dict[str, Any],
    *,
    uid: str,
    selection: str | list[str],
) -> Any:
    """握手并执行真实 MCP 工具，保持原有结构化结果形状。"""
    try:
        if not knowledge_enabled():
            raise RuntimeError("知识库服务在当前部署不可用")
        async with (
            asyncio.timeout(KNOWLEDGE_MCP_TIMEOUT_SECONDS),
            httpx.AsyncClient(
                auth=KnowledgeMCPAuth(uid, selection),
                timeout=httpx.Timeout(KNOWLEDGE_MCP_TIMEOUT_SECONDS, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as http_client,
            streamable_http_client(KNOWLEDGE_MCP_URL, http_client=http_client) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=KNOWLEDGE_MCP_TIMEOUT_SECONDS)) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        if result.isError:
            # 此服务公开的工具错误只含固定指导语，不转发第三方任意异常。
            raise KnowledgeMCPError("\n".join(item.text for item in result.content if item.type == "text"))
        payload = result.structuredContent
        if not isinstance(payload, dict):
            raise KnowledgeMCPError("知识库 MCP 返回了无效结果")
        if name == "list_kbs":
            return payload["knowledge_bases"]
        return payload
    except KnowledgeMCPError:
        raise
    except Exception as exc:
        logger.bind(component="knowledge_mcp", server_slug="knowledge", error_type=type(exc).__name__, available=False).warning(
            "Knowledge MCP unavailable: error_type={}", type(exc).__name__
        )
        raise KnowledgeMCPUnavailable("知识库 MCP 不可用，请稍后重试") from exc


async def list_knowledge_bases(uid: str, *, selection: str | list[str] = "all") -> list[dict[str, Any]]:
    """资源选择器和引用过滤也通过 MCP 读取当前权限摘要。"""
    return await call_knowledge_mcp("list_kbs", {"include_capabilities": True}, uid=uid, selection=selection)


class KnowledgeMCPAuth(httpx.Auth):
    """每次 HTTP 出站使用可信身份重新签发短期委托。"""

    requires_request_body = True

    def __init__(self, uid: str, selection: str | list[str], before_request=None):
        """绑定本次调用的可信用户与会话范围。"""
        self.uid, self.selection = uid, selection
        self.before_request = before_request

    async def async_auth_flow(self, request):
        """委托凭据不进入模型参数、持久化工具结果或全局缓存。"""
        if not knowledge_enabled():
            raise RuntimeError("知识库服务在当前部署不可用")
        if self.before_request is not None:
            await self.before_request(request)
        request.headers["Authorization"] = "Bearer " + create_knowledge_token(uid=self.uid, selection=self.selection)
        yield request


def knowledge_mcp_connection(*, uid: str, context=None, management: bool = False, before_request=None) -> dict:
    """从可信上下文装配 SDK 认证；管理发现显式使用本人全部范围。"""
    if not uid or not knowledge_enabled():
        raise ValueError("知识库 MCP 用户或部署不可用")
    if management:
        selection = "all"
    else:
        if context is None or getattr(context, "uid", None) != uid:
            raise MCPDelegationContextError("知识库 MCP 缺少匹配的可信运行上下文")
        selection = getattr(context, "knowledges", None)
        if selection is None:
            raise MCPDelegationContextError("知识库 MCP 缺少可信的运行选择范围")
    return {"auth": KnowledgeMCPAuth(uid, selection, before_request), "httpx_client_factory": knowledge_http_client}


def knowledge_http_client(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """内部凭据请求不读取环境代理，超时由适配器传入。"""
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, trust_env=False)


async def knowledge_resource_options(uid: str) -> list[dict]:
    """读取可见知识摘要，知识能力不可用时提供空选项。"""
    try:
        return [
            {"key": str(item["kb_id"]), "name": str(item["name"] or item["kb_id"]), "description": str(item["description"] or "")}
            for item in await list_knowledge_bases(uid)
            if item["kb_id"]
        ]
    except (KnowledgeMCPError, KnowledgeMCPUnavailable):
        return []
