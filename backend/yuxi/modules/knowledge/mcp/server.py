"""与 API 共进程的只读知识库 MCP 协议适配器。"""

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.base import KBNotFoundError
from yuxi.modules.knowledge.mcp import KNOWLEDGE_MCP_PATH
from yuxi.modules.knowledge.mcp.auth import authenticate_knowledge_delegate
from yuxi.modules.knowledge.schemas import (
    FindInputSchema,
    ListInputSchema,
    OpenInputSchema,
    SearchFileInput,
    SearchInputSchema,
)
from yuxi.modules.knowledge.services import tools as knowledge_tools
from yuxi.modules.knowledge.services.access import visible_knowledge_bases

# 默认 Host 白名单，防 DNS rebinding；部署可用 YUXI_MCP_ALLOWED_HOSTS 覆盖。
_DEFAULT_ALLOWED_HOSTS = "localhost:*,127.0.0.1:*,api:*"


class QueryKBInput(SearchInputSchema):
    """MCP 检索接受单个 ID 或多个 ID，Public HTTP 保留单库输入。"""

    kb_id: str | list[str] = Field(description="来自 list_kbs 的知识库 ID；可传单个字符串或非空 ID 数组，一次检索多个库")


class KnowledgeMCP:
    """拥有 MCP 协议、请求身份与 API 生命周期接线。"""

    def __init__(self, authenticate_api_key: Callable[..., Awaitable[User | Response]]):
        """以现有 Schema 注册固定只读工具并创建无状态传输。"""
        self.authenticate_api_key = authenticate_api_key
        self.server = Server(
            "Yuxi Knowledge",
            instructions=(
                "只读知识库检索：先 list_kbs，根据名称与描述选择 kb_id；"
                "查找内容用 query_kb，kb_id 可传单个 ID 或多个 ID 的数组；查找文件名或浏览文件用 search_file。"
                "用结果中的 kb_id、file_id 调用 find_kb_document 定位关键词，"
                "再用 open_kb_document 读取原文核实；引用来源时保留文件 ID 和行号。"
                "文件名搜索无结果不代表内容检索无结果；内容检索无结果时可改写关键词。"
                "长文按 next_offset 继续读取，文件列表按 offset/limit 翻页。"
                "只能访问当前调用用户可见且在委托范围内的知识库；404 后重新 list_kbs/search_file，勿猜测资源 ID。"
            ),
        )
        self.tools: dict[str, tuple[type[BaseModel], str]] = {
            "list_kbs": (
                ListInputSchema,
                "列出当前用户可见知识库的 kb_id、name、description。先按名称和描述选择检索范围，再调用 query_kb 或 search_file。",
            ),
            "query_kb": (
                QueryKBInput,
                "按关键词或短语检索一个或多个知识库中的内容片段，不是按文件名搜索。"
                "kb_id 来自 list_kbs，可传单个 ID 或非空 ID 数组；file_name 对每个目标库生效，仅在需要限定文件名时填写。"
                "数组输入返回 kb_ids 和按库顺序合并的 results，保留库内排名；任一库不可见或失败则整次失败。"
                "返回 results，包含 content、kb_id、file_id 和来源 metadata；"
                "用 find_kb_document/open_kb_document 核对原文。无结果时改写关键词。",
            ),
            "search_file": (
                SearchFileInput,
                "按文件名搜索可见知识库中的文件，不检索正文。"
                "至少提供 kb_id、kb_name 或 query 之一；指定知识库且省略 query 可浏览文件。"
                "返回 files、total、offset、limit、has_more；has_more 为真时将 offset 加 limit 继续。"
                "文件的 kb_id、file_id 可用于 find_kb_document/open_kb_document；部分外部知识库仅支持 query_kb。",
            ),
            "open_kb_document": (
                OpenInputSchema,
                "按行窗口读取检索或文件搜索结果的原文。kb_id、file_id 使用工具返回的值。"
                "line 从 1 开始且优先于从 0 开始的 offset；window_size 控制行数。"
                "返回带行号的 content、total_lines、has_more_after 和 next_offset；"
                "有更多内容时传 next_offset 继续读取，并省略 line。引用时保留文件 ID 和行号。",
            ),
            "find_kb_document": (
                FindInputSchema,
                "在指定文件正文中定位关键词，返回命中行号和上下文窗口。"
                "kb_id、file_id 使用检索或文件搜索返回的值；patterns 至少一个，默认按字面关键词匹配。"
                "use_regex=true 才使用正则。用 max_windows/window_size 限制上下文；"
                "需要更多上下文时，将窗口 start_line 传给 open_kb_document 的 line。",
            ),
        }
        self.server.list_tools()(self.list_tools)
        self.server.call_tool()(self.call_tool)
        self.manager = StreamableHTTPSessionManager(
            self.server,
            stateless=True,
            json_response=True,
            security_settings=TransportSecuritySettings(
                allowed_hosts=os.getenv("YUXI_MCP_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS).split(","),
                allowed_origins=[],
            ),
        )
        self.route = Route(KNOWLEDGE_MCP_PATH, endpoint=self, methods=["GET", "POST", "DELETE"])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """每个 HTTP 请求重新验证 Key 或内部委托，不缓存身份与范围。"""
        request = Request(scope, receive)
        authorization = request.headers.get("authorization", "")
        user = None
        selection = "all"
        async with pg_manager.get_async_session_context() as db:
            if authorization.startswith("Bearer yxkey_"):
                user = await self.authenticate_api_key(request, authorization, db)
            elif authorization.startswith("Bearer "):
                try:
                    user, selection = await authenticate_knowledge_delegate(authorization.removeprefix("Bearer "), db)
                except ValueError:
                    # 无效或过期的委托视同未认证，统一落到下方 401 质询
                    pass
        if user is None:
            user = JSONResponse(
                {"detail": "未提供有效的知识库访问凭据：请使用 Access Token 或具备知识库权限的 API Key（Authorization: Bearer）"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if isinstance(user, Response):
            await user(scope, receive, send)
            return
        # 无状态传输下经同一请求 scope 把身份传给 call_tool
        request.state.knowledge_mcp_uid = user.uid
        request.state.knowledge_mcp_selection = selection
        await self.manager.handle_request(scope, receive, send)

    async def list_tools(self) -> list[types.Tool]:
        """发布现有输入 Schema 与面向模型的工具选择说明。"""
        return [
            types.Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
            )
            for name, (model, description) in self.tools.items()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """在固定工具集合中调用当前用户可见资源的既有用例。"""
        if name not in self.tools:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="Unknown knowledge tool")])
        try:
            # 与 __call__ 共享同一请求 scope，读回入口写入的当前身份与选择
            state = self.server.request_context.request.state
            visible = await visible_knowledge_bases(state.knowledge_mcp_uid, selection=state.knowledge_mcp_selection)
            model, _ = self.tools[name]
            payload = model.model_validate(arguments)
            # list_kbs 是同步用例；结构化输出按客户端约定包一层 knowledge_bases
            if name == "list_kbs":
                result = knowledge_tools.list_kbs(visible, include_capabilities=payload.include_capabilities)
            else:
                result = await getattr(knowledge_tools, name)(visible_kbs=visible, **payload.model_dump())
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                structuredContent={"knowledge_bases": result} if name == "list_kbs" else result,
            )
        except knowledge_tools.KnowledgeToolError as exc:
            status = 404 if exc.not_found else 400
        except KBNotFoundError:
            status = 404
        except ValueError:
            status = 400
        except Exception as exc:
            logger.bind(component="knowledge_mcp", tool_name=name, error_type=type(exc).__name__).exception(
                "Knowledge tool execution failed"
            )
            # SDK 默认将异常正文返回客户端；此边界只公开稳定状态，防止底层诊断泄露。
            status = 500
        guidance = {
            400: "请核对工具参数与取值范围；search_file 需要知识库或文件名关键词，find 需要非空 patterns。部分知识库仅支持 query_kb。",
            404: "资源不存在或不可见；请重新 list_kbs，再用 search_file/query_kb 获取有效的 kb_id 和 file_id。",
            500: "知识库服务暂时失败，请稍后重试；持续失败时联系服务管理员。",
        }
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=f"Knowledge tool failed ({status}): {guidance[status]}")]
        )
