"""Memory 历史协议的输入范围、预览及 UTF-8 响应预算。"""

import json

from yuxi.modules.agents.message_preview import build_message_search_snippet
from yuxi.modules.agents.repositories.memory_history import MemoryHistoryRepository
from yuxi.modules.agents.repositories.messages import MessageRepository
from yuxi.modules.agents.tool_results import normalize_tool_result
from yuxi.shared.strings import truncate_utf8

# ==== 历史对话检索参数 ====
MEMORY_HISTORY_SEARCH_MAX_LIMIT = 10  # 单次历史搜索最多返回的消息条数。
MEMORY_HISTORY_SEARCH_QUERY_MAX_CHARS = 256  # 历史搜索关键词允许的最大字符数。
MEMORY_HISTORY_SEARCH_SNIPPET_MAX_BYTES = 512  # 单条搜索结果摘要的最大 UTF-8 字节数。
MEMORY_HISTORY_SEARCH_RESPONSE_MAX_BYTES = 16 * 1024  # 历史搜索完整 JSON 响应的最大字节数。
MEMORY_HISTORY_READ_MAX_LIMIT = 20  # 单次历史读取最多返回的消息条数。
MEMORY_HISTORY_MESSAGE_MAX_BYTES = 8 * 1024  # 单条历史消息正文的最大 UTF-8 字节数。
MEMORY_HISTORY_MESSAGES_MAX_BYTES = 32 * 1024  # 一次历史读取中全部消息正文的合计字节上限。
MEMORY_HISTORY_TOOL_CALL_MAX_COUNT = 10  # 显式读取工具调用时最多返回的记录数。
MEMORY_HISTORY_TOOL_CALL_MAX_BYTES = 4 * 1024  # 单条工具调用序列化后的最大字节数。
MEMORY_HISTORY_TOOL_CALLS_MAX_BYTES = 16 * 1024  # 一次历史读取中全部工具调用的合计字节上限。
MEMORY_HISTORY_READ_RESPONSE_MAX_BYTES = 64 * 1024  # 历史读取完整 JSON 响应的最大字节数。


def _json_size(value: object) -> int:
    """计算协议 JSON 的 UTF-8 字节数。"""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


class MemoryHistoryService:
    """从已授权持久行装配受预算约束的 Memory 响应。"""

    def __init__(self, db):
        """绑定查询仓储，不提交事务。"""
        self.repository = MemoryHistoryRepository(db)
        self.messages = MessageRepository(db)

    async def search_memory_messages(self, *, uid: str, query: str, limit: int = 5) -> dict:
        """搜索当前用户可见的普通主 Agent 历史消息。"""
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        if len(normalized_query) > MEMORY_HISTORY_SEARCH_QUERY_MAX_CHARS:
            raise ValueError(f"query 最多 {MEMORY_HISTORY_SEARCH_QUERY_MAX_CHARS} 个字符")
        bounded_limit = max(1, min(int(limit), MEMORY_HISTORY_SEARCH_MAX_LIMIT))
        rows = await self.repository.search(uid=uid, query=normalized_query, limit=bounded_limit)

        items: list[dict] = []
        response_truncated = False
        for row in rows:
            snippet = build_message_search_snippet(row.content, normalized_query)
            snippet, snippet_truncated = truncate_utf8(snippet, MEMORY_HISTORY_SEARCH_SNIPPET_MAX_BYTES)
            item = {
                "thread_id": row.thread_id,
                "title": row.title,
                "message_id": row.id,
                "role": row.role,
                "content": snippet,
            }
            if row.input_source:
                item["input_source"] = row.input_source
            if snippet_truncated:
                item["truncated"] = True
            items.append(item)
            if _json_size({"items": items}) > MEMORY_HISTORY_SEARCH_RESPONSE_MAX_BYTES:
                items.pop()
                response_truncated = True
                break
        response = {"items": items}
        if response_truncated:
            response["truncated"] = True
        return response

    async def read_memory_messages(
        self,
        *,
        uid: str,
        thread_id: str,
        message_id: int | None = None,
        limit: int = 20,
        include_tools: bool = False,
    ) -> dict:
        """读取当前用户普通主 Agent 线程的有界历史。"""
        bounded_limit = max(1, min(int(limit), MEMORY_HISTORY_READ_MAX_LIMIT))
        agent_session, rows = await self.repository.read(
            uid=uid, thread_id=thread_id, message_id=message_id, limit=bounded_limit, include_tools=include_tools
        )

        messages = self._serialize_memory_messages(rows)
        tool_calls, tools_truncated = await self._serialize_memory_tool_calls(
            [item["message_id"] for item in messages],
            include_tools=include_tools,
        )
        is_truncated = tools_truncated or any(item.get("truncated") for item in messages)
        payload = {
            "thread_id": agent_session.thread_id,
            "title": agent_session.title,
            "messages": messages,
            "tool_calls": tool_calls,
        }
        if is_truncated:
            payload["truncated"] = True
        self._fit_memory_read_response(payload)
        return payload

    @staticmethod
    def _serialize_memory_messages(rows: list) -> list[dict]:
        """按单条及总正文预算装配历史消息。"""
        messages: list[dict] = []
        remaining = MEMORY_HISTORY_MESSAGES_MAX_BYTES
        for row in rows:
            content_budget = min(MEMORY_HISTORY_MESSAGE_MAX_BYTES, remaining)
            content, truncated = truncate_utf8(row.content, content_budget)
            remaining = max(0, remaining - len(content.encode("utf-8")))
            msg_item = {
                "message_id": row.id,
                "role": row.role,
                "content": content,
            }
            if row.input_source:
                msg_item["input_source"] = row.input_source
            if truncated:
                msg_item["truncated"] = True
            messages.append(msg_item)
        return messages

    async def _serialize_memory_tool_calls(
        self,
        message_ids: list[int],
        *,
        include_tools: bool,
    ) -> tuple[list[dict], bool]:
        """按数量和字节预算装配显式请求的工具调用。"""
        if not include_tools or not message_ids:
            return [], False
        rows = await self.messages.list_tool_executions(message_ids, limit=MEMORY_HISTORY_TOOL_CALL_MAX_COUNT + 1)
        truncated = len(rows) > MEMORY_HISTORY_TOOL_CALL_MAX_COUNT
        tool_calls: list[dict] = []
        used_bytes = 0
        for row in rows[:MEMORY_HISTORY_TOOL_CALL_MAX_COUNT]:
            metadata = row.extra_metadata or {}
            tool_input = json.dumps(metadata.get("input") or {}, ensure_ascii=False, separators=(",", ":"))
            tool_input, input_truncated = truncate_utf8(tool_input, 1024)
            tool_output, output_truncated = truncate_utf8(normalize_tool_result(metadata.get("output")).audit_content, 2048)
            error, error_truncated = truncate_utf8(metadata.get("error_message"), 512)
            tool_item = {
                "tool_call_id": row.operation_id,
                "name": metadata["tool_name"],
                "input": tool_input,
                "output": tool_output,
                "status": row.execution_status,
                "error": error,
            }
            if input_truncated or output_truncated or error_truncated:
                tool_item["truncated"] = True
            item_size = _json_size(tool_item)
            if item_size > MEMORY_HISTORY_TOOL_CALL_MAX_BYTES or used_bytes + item_size > MEMORY_HISTORY_TOOL_CALLS_MAX_BYTES:
                truncated = True
                break
            used_bytes += item_size
            tool_calls.append(tool_item)
        return tool_calls, truncated

    @staticmethod
    def _fit_memory_read_response(payload: dict) -> None:
        """确保历史读取最终 JSON 响应不超过协议预算。"""
        while _json_size(payload) > MEMORY_HISTORY_READ_RESPONSE_MAX_BYTES and payload["tool_calls"]:
            payload["tool_calls"].pop()
            payload["truncated"] = True
        while _json_size(payload) > MEMORY_HISTORY_READ_RESPONSE_MAX_BYTES and payload["messages"]:
            payload["messages"].pop(0)
            payload["truncated"] = True
