"""Agent 文件工具注册与大结果处理策略。"""

from deepagents.backends import CompositeBackend
from deepagents.middleware.filesystem import TOOLS_EXCLUDED_FROM_EVICTION, FilesystemMiddleware, FsToolName

# Yuxi 在 DeepAgents 内建排除集之上额外豁免知识库文档工具结果，
# 避免 read_file/offload 循环：该工具自带分页与引用语义。
_TOOL_RESULT_EVICTION_EXEMPT_TOOLS = frozenset(TOOLS_EXCLUDED_FROM_EVICTION) | {"open_kb_document"}

# 文件工具 allowlist：显式排除 destructive delete。BaseSandbox 自带的默认 delete 会在
# VM 内 rm -rf，没有审批与审计语义；allowlist 是唯一的暴露边界，开放删除前需要独立设计。
AGENT_FS_TOOLS: tuple[FsToolName, ...] = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
)


class YuxiFilesystemMiddleware(FilesystemMiddleware):
    """按明确文件工具集注册，并在进入模型上下文前处理大结果。"""

    def __init__(
        self,
        *,
        backend: CompositeBackend,
        tools: tuple[FsToolName, ...] = AGENT_FS_TOOLS,
        tool_token_limit_before_evict: int | None = None,
    ):
        """默认排除删除工具，转换为 DeepAgents 要求的列表。"""
        super().__init__(backend=backend, tools=list(tools), tool_token_limit_before_evict=tool_token_limit_before_evict)

    def wrap_tool_call(self, request, handler):
        tool_result = handler(request)

        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result
        if self._tool_token_limit_before_evict is None:
            return tool_result

        return self._intercept_large_tool_result(tool_result)

    async def awrap_tool_call(self, request, handler):
        tool_result = await handler(request)

        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result
        if self._tool_token_limit_before_evict is None:
            return tool_result

        return await self._aintercept_large_tool_result(tool_result)
