"""
对话域持久化 Repository（Async）
"""

import json
import uuid as uuid_lib

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only, selectinload
from sqlalchemy.orm.attributes import flag_modified

from yuxi.storage.postgres.models_business import (
    AGENT_RUN_TERMINAL_STATUSES,
    AUDIT_MESSAGE_TYPES,
    MODEL_AUDIT_MESSAGE_TYPE,
    TOOL_AUDIT_MESSAGE_TYPE,
    UNVIEWED_RUN_MARKER,
    AgentRun,
    Conversation,
    ConversationStats,
    Message,
    SubagentThread,
    ToolCall,
)
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.string_utils import truncate_utf8

MAX_CONVERSATION_TITLE_LENGTH = 255
MESSAGE_SEARCH_SNIPPET_RADIUS = 72
MESSAGE_SEARCH_SNIPPET_MAX_LENGTH = 180
MESSAGE_SEARCH_SNIPPETS_PER_THREAD = 2
MESSAGE_SEARCH_ROLES = ("user", "assistant")
MESSAGE_SEARCH_EXCLUDED_TYPES = (
    "tool_call",
    "tool_result",
    MODEL_AUDIT_MESSAGE_TYPE,
    TOOL_AUDIT_MESSAGE_TYPE,
)
INVOCATION_CONVERSATION_SOURCES = ("agent_call", "agent_evaluation")

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
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def _state_proven_model_tool_call_condition():
    """只允许终态 State 已证明的 Model 兼容行进入普通读模型。"""
    return and_(
        Message.message_type == MODEL_AUDIT_MESSAGE_TYPE,
        Message.tool_calls.any(),
        Message.extra_metadata["state_reconciled"].as_boolean().is_(True),
        Message.run_id.in_(select(AgentRun.id).where(AgentRun.status.in_(AGENT_RUN_TERMINAL_STATUSES))),
    )


class ConversationRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    def _normalize_title(self, title: str | None) -> str | None:
        if title is None:
            return None
        normalized = str(title).strip()
        if not normalized:
            return ""
        if len(normalized) > MAX_CONVERSATION_TITLE_LENGTH:
            logger.warning(
                f"Conversation title too long ({len(normalized)}), truncate to {MAX_CONVERSATION_TITLE_LENGTH}"
            )
            return normalized[:MAX_CONVERSATION_TITLE_LENGTH]
        return normalized

    def _escape_like_query(self, query: str) -> str:
        return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _message_search_conditions(self, query: str):
        pattern = f"%{self._escape_like_query(query)}%"
        return [
            Message.role.in_(MESSAGE_SEARCH_ROLES),
            or_(Message.message_type.is_(None), Message.message_type.notin_(MESSAGE_SEARCH_EXCLUDED_TYPES)),
            Message.content.ilike(pattern, escape="\\"),
        ]

    def _exclude_source_conditions(self, sources: tuple[str, ...]):
        if not sources:
            return []
        source = Conversation.extra_metadata["source"].as_string()
        return [
            or_(
                Conversation.extra_metadata.is_(None),
                source.is_(None),
                source.notin_(sources),
            )
        ]

    def _build_message_search_snippet(self, content: str, query: str) -> str:
        normalized = " ".join(str(content or "").split())
        if not normalized:
            return ""

        match_index = normalized.lower().find(query.lower())
        if match_index < 0:
            return normalized[:MESSAGE_SEARCH_SNIPPET_MAX_LENGTH]

        start = max(0, match_index - MESSAGE_SEARCH_SNIPPET_RADIUS)
        end = min(len(normalized), match_index + len(query) + MESSAGE_SEARCH_SNIPPET_RADIUS)
        snippet = normalized[start:end].strip()
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(normalized):
            snippet = f"{snippet}..."
        return snippet[:MESSAGE_SEARCH_SNIPPET_MAX_LENGTH]

    async def add_conversation(
        self,
        *,
        uid: str,
        agent_id: str,
        title: str | None = None,
        thread_id: str | None = None,
        metadata: dict | None = None,
        project_id: str,
        creation_request_id: str | None = None,
    ) -> Conversation:
        """创建对话和统计记录但只 flush，供外层事务继续绑定关系。"""
        if not thread_id:
            thread_id = str(uuid_lib.uuid4())

        metadata = (metadata or {}).copy()
        metadata["attachments"] = []

        if metadata and "memory_history_started_at" in metadata:
            raise ValueError("memory_history_started_at is owned by personal file history")
        normalized_title = self._normalize_title(title)

        conversation = Conversation(
            thread_id=thread_id,
            creation_request_id=creation_request_id,
            uid=str(uid),
            agent_id=agent_id,
            title=normalized_title or "New Conversation",
            status="active",
            extra_metadata=metadata,
            last_viewed_run_id=UNVIEWED_RUN_MARKER,
            project_id=project_id,
        )

        self.db.add(conversation)
        await self.db.flush()

        stats = ConversationStats(conversation_id=conversation.id)
        self.db.add(stats)
        await self.db.flush()

        logger.info(f"Created conversation: {conversation.thread_id} for user {uid}")
        return conversation

    async def create_conversation(
        self,
        uid: str,
        agent_id: str,
        project_id: str,
        title: str | None = None,
        thread_id: str | None = None,
        metadata: dict | None = None,
        creation_request_id: str | None = None,
    ) -> Conversation:
        """创建并提交一个完整对话，适用于不需要外层事务编排的入口。"""
        conversation = await self.add_conversation(
            uid=uid,
            agent_id=agent_id,
            title=title,
            thread_id=thread_id,
            metadata=metadata,
            project_id=project_id,
            creation_request_id=creation_request_id,
        )
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def get_conversation_by_thread_id(self, thread_id: str) -> Conversation | None:
        result = await self.db.execute(select(Conversation).where(Conversation.thread_id == thread_id))
        return result.scalar_one_or_none()

    async def get_conversation_by_creation_request_id(self, uid: str, request_id: str) -> Conversation | None:
        """按用户和创建幂等键读取 Conversation。"""
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.uid == str(uid),
                Conversation.creation_request_id == request_id,
            )
        )
        return result.scalar_one_or_none()

    async def lock_conversation_by_thread_id(self, thread_id: str) -> Conversation | None:
        """锁定线程根记录，串行化同一对话的调度决策。"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.thread_id == thread_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_conversation_by_id(self, conversation_id: int) -> Conversation | None:
        result = await self.db.execute(select(Conversation).where(Conversation.id == conversation_id))
        return result.scalar_one_or_none()

    async def mark_thread_viewed(self, thread_id: str, run_id: str) -> Conversation | None:
        """记录用户最近查看过的顶层 run id；重复标记同一 run 时保持幂等。"""
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            return None
        if conversation.last_viewed_run_id != run_id:
            conversation.last_viewed_run_id = run_id
            await self.db.commit()
            await self.db.refresh(conversation)
        return conversation

    def _ensure_metadata(self, conversation: Conversation) -> dict:
        metadata = dict(conversation.extra_metadata or {})
        attachments = metadata.get("attachments", [])
        metadata["attachments"] = [dict(item) for item in attachments if isinstance(item, dict)]
        return metadata

    async def _save_metadata(self, conversation: Conversation, metadata: dict) -> None:
        """普通元数据更新保留个人历史消费Owner持有的来源时间。"""
        current = await self._lock_conversation_by_id(conversation.id)
        metadata = dict(metadata)
        metadata.pop("memory_history_started_at", None)
        started = (current.extra_metadata or {}).get("memory_history_started_at")
        if started is not None:
            metadata["memory_history_started_at"] = started
        conversation.extra_metadata = metadata
        flag_modified(conversation, "extra_metadata")
        conversation.updated_at = utc_now_naive()
        await self.db.flush()

    async def set_model_spec(self, conversation: Conversation, model_spec: str) -> None:
        """在请求事务内更新对话绑定模型。"""
        conversation = await self._lock_conversation_by_id(conversation.id)
        metadata = dict(conversation.extra_metadata or {})
        metadata["model_spec"] = model_spec
        await self._save_metadata(conversation, metadata)

    async def _lock_conversation_by_id(self, conversation_id: int) -> Conversation | None:
        """锁定会话元数据，串行化同一线程的附件更新。"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        delivery_status: str = "complete",
        commit: bool = True,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            message_type=message_type,
            extra_metadata=extra_metadata or {},
            image_content=image_content,
            run_id=run_id,
            request_id=request_id,
            delivery_status=delivery_status,
        )

        self.db.add(message)
        conversation = await self.get_conversation_by_id(conversation_id)
        if conversation:
            conversation.updated_at = utc_now_naive()

        await self.db.flush()
        await self.db.refresh(message)

        await self._update_message_count(conversation_id)
        if commit:
            await self.db.commit()

        logger.debug(f"Added {role} message to conversation {conversation_id}")
        return message

    async def add_message_by_thread_id(
        self,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        delivery_status: str = "complete",
        commit: bool = True,
    ) -> Message | None:
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            logger.warning(f"Conversation not found for thread_id: {thread_id}")
            return None

        return await self.add_message(
            conversation_id=conversation.id,
            role=role,
            content=content,
            message_type=message_type,
            extra_metadata=extra_metadata,
            image_content=image_content,
            run_id=run_id,
            request_id=request_id,
            delivery_status=delivery_status,
            commit=commit,
        )

    async def add_tool_call(
        self,
        message_id: int,
        tool_name: str,
        tool_input: dict | None = None,
        tool_output: str | None = None,
        status: str = "pending",
        error_message: str | None = None,
        langgraph_tool_call_id: str | None = None,
        commit: bool = True,
    ) -> ToolCall:
        if langgraph_tool_call_id:
            existing = await self.get_tool_call_by_langgraph_id(langgraph_tool_call_id)
            if existing:
                logger.debug(
                    "Tool call already exists for langgraph_tool_call_id=%s, skip insert",
                    langgraph_tool_call_id,
                )
                return existing

        tool_call = ToolCall(
            message_id=message_id,
            tool_name=tool_name,
            tool_input=tool_input or {},
            tool_output=tool_output,
            status=status,
            error_message=error_message,
            langgraph_tool_call_id=langgraph_tool_call_id,
        )

        self.db.add(tool_call)
        await self.db.flush()
        await self.db.refresh(tool_call)
        if commit:
            await self.db.commit()

        logger.debug(f"Added tool call {tool_name} to message {message_id}")
        return tool_call

    async def publish_assistant_output(self, message: Message) -> None:
        """把最终 AIMessage 发布到普通历史并刷新 Conversation 读模型。"""
        if message.role != "assistant":
            raise ValueError("只有 assistant Message 可以发布为最终输出")
        if message.message_type == MODEL_AUDIT_MESSAGE_TYPE:
            message.message_type = "text"
        conversation = await self.get_conversation_by_id(message.conversation_id)
        if conversation is None:
            raise ValueError("最终输出缺少 Conversation")
        conversation.updated_at = utc_now_naive()
        await self._update_message_count(message.conversation_id)
        await self.db.flush()

    async def get_messages(self, conversation_id: int, limit: int | None = None, offset: int = 0) -> list[Message]:
        query = (
            select(Message)
            .options(
                selectinload(Message.tool_calls),
                selectinload(Message.feedbacks),
            )
            .where(
                Message.conversation_id == conversation_id,
                or_(
                    Message.message_type.is_(None),
                    Message.message_type.notin_(AUDIT_MESSAGE_TYPES),
                    _state_proven_model_tool_call_condition(),
                ),
            )
            .order_by(Message.created_at.asc())
        )

        if limit:
            query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().unique().all())

    async def get_message_source_ids_by_thread_id(self, thread_id: str) -> set[str]:
        """读取全部持久 Message 来源 ID，包括普通历史隐藏的审计行。"""
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if conversation is None:
            return set()
        result = await self.db.execute(select(Message.extra_metadata).where(Message.conversation_id == conversation.id))
        return {
            str(metadata["id"])
            for metadata in result.scalars().all()
            if isinstance(metadata, dict) and isinstance(metadata.get("id"), str)
        }

    async def list_message_audits(self, conversation_id: int, *, limit: int) -> tuple[list[Message], bool]:
        """返回有界审计时间线；operation_id 同时覆盖已发布的最终 Model。"""
        result = await self.db.execute(
            select(Message)
            .join(AgentRun, AgentRun.id == Message.run_id)
            .options(selectinload(Message.tool_calls))
            .where(
                Message.conversation_id == conversation_id,
                Message.operation_id.is_not(None),
                Message.role.in_(("assistant", "tool")),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc(), Message.sequence.desc(), Message.id.desc())
            .limit(limit + 1)
        )
        messages = list(result.scalars().unique().all())
        truncated = len(messages) > limit
        return list(reversed(messages[:limit])), truncated

    async def list_agent_runs_for_history(self, conversation_id: int) -> list[AgentRun]:
        """按历史顺序读取当前会话全部轻量 Run，包含没有消息的运行。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
            .options(
                load_only(
                    AgentRun.id,
                    AgentRun.request_id,
                    AgentRun.run_type,
                    AgentRun.created_by_run_id,
                    AgentRun.status,
                    AgentRun.created_at,
                    AgentRun.started_at,
                    AgentRun.prepared_at,
                    AgentRun.first_model_request_at,
                    AgentRun.first_output_at,
                    AgentRun.finished_at,
                    raiseload=True,
                )
            )
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
        return list(result.scalars().all())

    async def list_agent_runs_for_trace(self, conversation_id: int, *, limit: int) -> tuple[list[AgentRun], bool]:
        """按创建顺序返回有界 AgentRun 调试事实。"""
        result = await self.db.execute(
            select(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit + 1)
        )
        runs = list(result.scalars().all())
        truncated = len(runs) > limit
        return list(reversed(runs[:limit])), truncated

    async def get_messages_by_thread_id(
        self, thread_id: str, limit: int | None = None, offset: int = 0
    ) -> list[Message]:
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            logger.warning(f"Conversation not found for thread_id: {thread_id}")
            return []

        return await self.get_messages(conversation.id, limit, offset)

    async def list_conversations(
        self,
        uid: str | None = None,
        agent_id: str | None = None,
        status: str = "active",
        limit: int | None = None,
        offset: int = 0,
        exclude_sources: tuple[str, ...] = (),
    ) -> list[Conversation]:
        """List conversations with pinned conversations always included first.

        The limit applies only to non-pinned conversations to ensure pinned
        conversations are always visible in the list.
        """

        base_conditions = [Conversation.status == status]
        if uid:
            base_conditions.append(Conversation.uid == str(uid))
        if agent_id:
            base_conditions.append(Conversation.agent_id == agent_id)
        base_conditions.extend(self._exclude_source_conditions(exclude_sources))

        # First, get all pinned conversations (no limit)
        pinned_query = (
            select(Conversation)
            .options(joinedload(Conversation.project))
            .where(*base_conditions)
            .where(Conversation.is_pinned)
            .order_by(Conversation.updated_at.desc())
        )
        result = await self.db.execute(pinned_query)
        pinned_conversations = list(result.scalars().all())

        # limit/offset 只作用于非置顶对话，避免重复附带的置顶项改变分页游标。
        non_pinned_query = (
            select(Conversation)
            .options(joinedload(Conversation.project))
            .where(*base_conditions)
            .where(~Conversation.is_pinned)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
        )
        if limit is not None:
            non_pinned_query = non_pinned_query.limit(limit)
        result = await self.db.execute(non_pinned_query)
        non_pinned_conversations = list(result.scalars().all())

        return pinned_conversations + non_pinned_conversations

    async def list_active_conversations_for_user(self, uid: str) -> list[Conversation]:
        """返回用户全部 active 对话，按最近更新时间排序。"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.uid == str(uid), Conversation.status == "active")
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def search_conversations_by_message_content(
        self,
        *,
        uid: str,
        query: str,
        agent_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        exclude_sources: tuple[str, ...] = (),
    ) -> tuple[list[dict], bool]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return [], False

        conversation_conditions = [
            Conversation.uid == str(uid),
            Conversation.status == "active",
        ]
        if agent_id:
            conversation_conditions.append(Conversation.agent_id == agent_id)
        conversation_conditions.extend(self._exclude_source_conditions(exclude_sources))

        message_conditions = self._message_search_conditions(normalized_query)
        summary = (
            select(
                Message.conversation_id.label("conversation_id"),
                func.count(Message.id).label("matched_count"),
                func.max(Message.created_at).label("latest_match_at"),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conversation_conditions, *message_conditions)
            .group_by(Message.conversation_id)
            .subquery()
        )

        result = await self.db.execute(
            select(Conversation, summary.c.matched_count, summary.c.latest_match_at)
            .join(summary, Conversation.id == summary.c.conversation_id)
            .order_by(summary.c.latest_match_at.desc(), Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )
        rows = list(result.all())
        has_more = len(rows) > limit
        rows = rows[:limit]

        items: list[dict] = []
        for conversation, matched_count, latest_match_at in rows:
            snippet_result = await self.db.execute(
                select(Message.id, Message.content, Message.created_at)
                .where(Message.conversation_id == conversation.id, *message_conditions)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(MESSAGE_SEARCH_SNIPPETS_PER_THREAD)
            )
            snippet_rows = list(snippet_result.all())
            snippets = [
                {
                    "message_id": message_id,
                    "content": self._build_message_search_snippet(content, normalized_query),
                    "created_at": created_at,
                }
                for message_id, content, created_at in snippet_rows
            ]

            items.append(
                {
                    "conversation": conversation,
                    "matched_count": int(matched_count or 0),
                    "latest_match_at": latest_match_at,
                    "message_id": snippets[0]["message_id"] if snippets else None,
                    "snippets": snippets,
                }
            )

        return items, has_more

    async def search_memory_messages(self, *, uid: str, query: str, limit: int = 5) -> dict:
        """搜索当前用户可见的普通主 Agent 历史消息。"""
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        if len(normalized_query) > MEMORY_HISTORY_SEARCH_QUERY_MAX_CHARS:
            raise ValueError(f"query 最多 {MEMORY_HISTORY_SEARCH_QUERY_MAX_CHARS} 个字符")
        bounded_limit = max(1, min(int(limit), MEMORY_HISTORY_SEARCH_MAX_LIMIT))
        message_conditions = self._message_search_conditions(normalized_query)

        result = await self.db.execute(
            select(
                Conversation.thread_id,
                Conversation.title,
                Message.id,
                Message.role,
                Message.content,
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                *self._memory_conversation_conditions(uid),
                *message_conditions,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(bounded_limit)
        )

        items: list[dict] = []
        response_truncated = False
        for row in result.all():
            snippet = self._build_message_search_snippet(row.content, normalized_query)
            snippet, snippet_truncated = truncate_utf8(snippet, MEMORY_HISTORY_SEARCH_SNIPPET_MAX_BYTES)
            item = {
                "thread_id": row.thread_id,
                "title": row.title,
                "message_id": row.id,
                "role": row.role,
                "content": snippet,
            }
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
        conversation_result = await self.db.execute(
            select(Conversation.id, Conversation.thread_id, Conversation.title).where(
                Conversation.thread_id == str(thread_id),
                *self._memory_conversation_conditions(uid),
            )
        )
        conversation = conversation_result.one_or_none()
        if conversation is None:
            raise ValueError("历史线程不存在或不可见")

        message_type_condition = or_(
            Message.message_type.is_(None),
            Message.message_type.notin_(MESSAGE_SEARCH_EXCLUDED_TYPES),
        )
        if include_tools:
            message_type_condition = or_(message_type_condition, _state_proven_model_tool_call_condition())
        message_conditions = [
            Message.conversation_id == conversation.id,
            Message.role.in_(MESSAGE_SEARCH_ROLES),
            message_type_condition,
        ]
        if message_id is None:
            query = (
                self._memory_message_select()
                .where(*message_conditions)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(bounded_limit)
            )
            result = await self.db.execute(query)
            rows = list(result.all())
            rows.reverse()
        else:
            anchor_id = int(message_id)
            anchor_result = await self.db.execute(
                select(Message.id).where(Message.id == anchor_id, *message_conditions)
            )
            if anchor_result.scalar_one_or_none() is None:
                raise ValueError("历史消息不存在或不属于该线程")

            before_limit = (bounded_limit + 1) // 2
            before_query = (
                self._memory_message_select()
                .where(*message_conditions, Message.id <= anchor_id)
                .order_by(Message.id.desc())
                .limit(before_limit)
            )
            before_result = await self.db.execute(before_query)
            before = list(before_result.all())
            before.reverse()

            after_limit = bounded_limit - len(before)
            after = []
            if after_limit:
                after_query = (
                    self._memory_message_select()
                    .where(*message_conditions, Message.id > anchor_id)
                    .order_by(Message.id.asc())
                    .limit(after_limit)
                )
                after_result = await self.db.execute(after_query)
                after = list(after_result.all())
            rows = before + after

        messages = self._serialize_memory_messages(rows)
        tool_calls, tools_truncated = await self._serialize_memory_tool_calls(
            [item["message_id"] for item in messages],
            include_tools=include_tools,
        )
        is_truncated = tools_truncated or any(item.get("truncated") for item in messages)
        payload = {
            "thread_id": conversation.thread_id,
            "title": conversation.title,
            "messages": messages,
            "tool_calls": tool_calls,
        }
        if is_truncated:
            payload["truncated"] = True
        self._fit_memory_read_response(payload)
        return payload

    def _memory_conversation_conditions(self, uid: str) -> list:
        """构建用户可见普通主 Agent Conversation 条件。"""
        child_thread_exists = select(SubagentThread.id).where(SubagentThread.child_conversation_id == Conversation.id)
        return [
            Conversation.uid == str(uid),
            Conversation.status == "active",
            *self._exclude_source_conditions(INVOCATION_CONVERSATION_SOURCES),
            ~child_thread_exists.exists(),
        ]

    @staticmethod
    def _memory_message_select():
        return select(
            Message.id,
            Message.role,
            Message.content,
        )

    @staticmethod
    def _serialize_memory_messages(rows: list) -> list[dict]:
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
        if not include_tools or not message_ids:
            return [], False
        result = await self.db.execute(
            select(
                ToolCall.langgraph_tool_call_id,
                ToolCall.tool_name,
                ToolCall.tool_input,
                ToolCall.tool_output,
                ToolCall.status,
                ToolCall.error_message,
            )
            .where(ToolCall.message_id.in_(message_ids))
            .order_by(ToolCall.created_at.asc(), ToolCall.id.asc())
            .limit(MEMORY_HISTORY_TOOL_CALL_MAX_COUNT + 1)
        )
        rows = list(result.all())
        truncated = len(rows) > MEMORY_HISTORY_TOOL_CALL_MAX_COUNT
        tool_calls: list[dict] = []
        used_bytes = 0
        for row in rows[:MEMORY_HISTORY_TOOL_CALL_MAX_COUNT]:
            tool_input = json.dumps(row.tool_input or {}, ensure_ascii=False, separators=(",", ":"))
            tool_input, input_truncated = truncate_utf8(tool_input, 1024)
            tool_output, output_truncated = truncate_utf8(row.tool_output, 2048)
            error, error_truncated = truncate_utf8(row.error_message, 512)
            tool_item = {
                "tool_call_id": row.langgraph_tool_call_id,
                "name": row.tool_name,
                "input": tool_input,
                "output": tool_output,
                "status": row.status,
                "error": error,
            }
            if input_truncated or output_truncated or error_truncated:
                tool_item["truncated"] = True
            item_size = _json_size(tool_item)
            if (
                item_size > MEMORY_HISTORY_TOOL_CALL_MAX_BYTES
                or used_bytes + item_size > MEMORY_HISTORY_TOOL_CALLS_MAX_BYTES
            ):
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

    async def update_conversation(
        self,
        thread_id: str,
        title: str | None = None,
        status: str | None = None,
        metadata: dict | None = None,
        is_pinned: bool | None = None,
    ) -> Conversation | None:
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            return None

        if metadata and "memory_history_started_at" in metadata:
            raise ValueError("memory_history_started_at is owned by personal file history")
        conversation = await self._lock_conversation_by_id(conversation.id)
        normalized_title = self._normalize_title(title)
        if normalized_title is not None:
            conversation.title = normalized_title
        if status is not None:
            conversation.status = status
        if is_pinned is not None:
            conversation.is_pinned = is_pinned

        if metadata is not None:
            current_metadata = dict(conversation.extra_metadata or {})
            current_metadata.update(metadata)
            conversation.extra_metadata = current_metadata

        conversation.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(conversation)

        logger.info(f"Updated conversation {thread_id}")
        return conversation

    async def delete_conversation(self, thread_id: str, soft_delete: bool = True) -> bool:
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            return False

        if soft_delete:
            conversation.status = "deleted"
            await self.db.commit()
            logger.info(f"Soft deleted conversation {thread_id}")
        else:
            self.db.delete(conversation)
            await self.db.commit()
            logger.info(f"Permanently deleted conversation {thread_id}")

        return True

    async def get_stats(self, conversation_id: int) -> ConversationStats | None:
        result = await self.db.execute(
            select(ConversationStats).where(ConversationStats.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def update_stats(
        self,
        conversation_id: int,
        tokens_used: int | None = None,
        model_used: str | None = None,
        user_feedback: dict | None = None,
    ) -> ConversationStats | None:
        stats = await self.get_stats(conversation_id)
        if not stats:
            return None

        if tokens_used is not None:
            stats.total_tokens += tokens_used
        if model_used is not None:
            stats.model_used = model_used
        if user_feedback is not None:
            stats.user_feedback = user_feedback

        stats.updated_at = utc_now_naive()
        await self.db.commit()
        await self.db.refresh(stats)

        return stats

    async def get_tool_call_by_langgraph_id(self, langgraph_tool_call_id: str) -> ToolCall | None:
        result = await self.db.execute(
            select(ToolCall)
            .where(ToolCall.langgraph_tool_call_id == langgraph_tool_call_id)
            .order_by(ToolCall.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_tool_call_output(
        self,
        langgraph_tool_call_id: str,
        tool_output: str,
        status: str = "success",
        error_message: str | None = None,
        commit: bool = True,
    ) -> ToolCall | None:
        tool_call = await self.get_tool_call_by_langgraph_id(langgraph_tool_call_id)
        if not tool_call:
            logger.warning(f"Tool call not found for langgraph_tool_call_id: {langgraph_tool_call_id}")
            return None

        tool_call.tool_output = tool_output
        tool_call.status = status
        if error_message:
            tool_call.error_message = error_message

        await self.db.flush()
        await self.db.refresh(tool_call)
        if commit:
            await self.db.commit()

        logger.debug(f"Updated tool call {langgraph_tool_call_id} with output")
        return tool_call

    async def _update_message_count(self, conversation_id: int) -> None:
        from sqlalchemy import func

        stats = await self.get_stats(conversation_id)
        if stats:
            result = await self.db.execute(
                select(func.count()).where(
                    Message.conversation_id == conversation_id,
                    or_(Message.message_type.is_(None), Message.message_type.notin_(AUDIT_MESSAGE_TYPES)),
                )
            )
            message_count = result.scalar()
            stats.message_count = message_count
            await self.db.flush()

    async def get_attachments(self, conversation_id: int) -> list[dict]:
        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            return []
        metadata = self._ensure_metadata(conversation)
        return [item for item in metadata.get("attachments", []) if not item.get("trash_id")]

    async def lock_attachments(self, conversation_id: int) -> list[dict]:
        """锁定会话并返回当前附件，用于需要检查后更新的用例。"""
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation:
            return []
        return list(self._ensure_metadata(conversation).get("attachments", []))

    async def get_attachments_by_thread_id(self, thread_id: str) -> list[dict]:
        conversation = await self.get_conversation_by_thread_id(thread_id)
        if not conversation:
            return []
        return await self.get_attachments(conversation.id)

    async def add_attachment(self, conversation_id: int, attachment_info: dict) -> dict | None:
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation:
            return None

        metadata = self._ensure_metadata(conversation)
        attachments = metadata.get("attachments", [])
        attachments = [item for item in attachments if item.get("file_id") != attachment_info.get("file_id")]
        attachments.append(attachment_info)
        metadata["attachments"] = attachments
        await self._save_metadata(conversation, metadata)
        return attachment_info

    async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]) -> list[dict] | None:
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation:
            return None

        metadata = self._ensure_metadata(conversation)
        attachments = metadata.get("attachments", [])
        incoming_ids = {item.get("file_id") for item in attachment_infos}
        attachments = [item for item in attachments if item.get("file_id") not in incoming_ids]
        attachments.extend(attachment_infos)
        metadata["attachments"] = attachments
        await self._save_metadata(conversation, metadata)
        return attachment_infos

    async def update_attachment_status(
        self, conversation_id: int, file_id: str, status: str, update_fields: dict | None = None
    ) -> dict | None:
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation:
            return None

        metadata = self._ensure_metadata(conversation)
        attachments = metadata.get("attachments", [])
        target = None
        for item in attachments:
            if item.get("file_id") == file_id:
                item["status"] = status
                if update_fields:
                    item.update(update_fields)
                target = item
                break

        if target is not None:
            metadata["attachments"] = attachments
            await self._save_metadata(conversation, metadata)
        return target

    async def bind_attachments_to_request(
        self, conversation_id: int, request_id: str, file_ids: list[str]
    ) -> list[dict]:
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation or not request_id or not file_ids:
            return []

        file_id_set = {str(file_id).strip() for file_id in file_ids if str(file_id).strip()}
        if not file_id_set:
            return []

        metadata = self._ensure_metadata(conversation)
        attachments = metadata.get("attachments", [])
        changed = False

        for item in attachments:
            if item.get("file_id") not in file_id_set or item.get("trash_id"):
                continue
            if item.get("request_id"):
                continue
            item["request_id"] = request_id
            changed = True

        if changed:
            metadata["attachments"] = attachments
            await self._save_metadata(conversation, metadata)
        return [dict(item) for item in attachments if item.get("request_id") == request_id and not item.get("trash_id")]

    async def get_attachments_by_request_id(self, conversation_id: int, request_id: str) -> list[dict]:
        attachments = await self.get_attachments(conversation_id)
        return [item for item in attachments if item.get("request_id") == request_id and not item.get("trash_id")]

    async def remove_attachment(self, conversation_id: int, file_id: str) -> bool:
        conversation = await self._lock_conversation_by_id(conversation_id)
        if not conversation:
            return False

        metadata = self._ensure_metadata(conversation)
        attachments = metadata.get("attachments", [])
        new_attachments = [item for item in attachments if item.get("file_id") != file_id]

        if len(new_attachments) == len(attachments):
            return False

        metadata["attachments"] = new_attachments
        await self._save_metadata(conversation, metadata)
        return True
