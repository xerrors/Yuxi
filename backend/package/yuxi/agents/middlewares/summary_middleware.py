"""Summary + ToolResult Offload Middleware.

基于 LangChain SummarizationMiddleware 实现，额外添加工具结果卸载功能：
- 保留原有的 summary 历史记录逻辑
- 将 ToolMessage 的 results 卸载到虚拟文件系统（默认 > 1k 字符）
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Any, Literal, cast, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import BaseChatModel, init_chat_model
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    MessageLikeRepresentation,
    RemoveMessage,
    ToolMessage,
)
from langchain_core.messages.utils import (
    count_tokens_approximately,
    get_buffer_string,
    trim_messages,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

TokenCounter = Callable[[Iterable[MessageLikeRepresentation]], int]

DEFAULT_SUMMARY_PROMPT = """<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective in this task is to extract the highest quality/most relevant
context from the conversation history below.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must
extract the highest quality/most relevant pieces of information from your conversation
history. This context will then overwrite the conversation history presented below.
Because of this, ensure the context you extract is only the most important information
to your overall goal.
</objective_information>

<instructions>
The conversation history below will be replaced with the context you extract in
this step. Because of this, you must do your very best to extract and record all
of the most important context from the conversation history. You want to ensure
that you don't repeat any actions you've already completed, so the context you
extract from the conversation history should be focused on the most important
information to your overall goal.
</instructions>

The user will message you with the full message history you'll be extracting context
from, to then replace. Carefully read over it all, and think deeply about what
information is most important to your overall goal that should be saved.

With all of this in mind, please carefully read over the entire conversation history,
and extract the most important and relevant context to replace it so that you can
free up space in the conversation history. Respond ONLY with the extracted context.
Do not include any additional information, or text before or after the extracted context.

<messages>
Messages to summarize:
{messages}
</messages>"""

_DEFAULT_MESSAGES_TO_KEEP = 20
_DEFAULT_TRIM_TOKEN_LIMIT = 4000
_DEFAULT_FALLBACK_MESSAGE_COUNT = 15
_DEFAULT_OFFLOAD_THRESHOLD = 1000  # Token 数阈值，超过此值则卸载到文件系统
_OFFLOAD_DIR = "/summary_offload"  # 虚拟文件系统路径

ContextFraction = tuple[Literal["fraction"], float]
ContextTokens = tuple[Literal["tokens"], int]
ContextMessages = tuple[Literal["messages"], int]

ContextSize = ContextFraction | ContextTokens | ContextMessages


def _get_approximate_token_counter(model: BaseChatModel) -> TokenCounter:
    """Tune parameters of approximate token counter based on model type."""
    if model._llm_type == "anthropic-chat":  # noqa: SLF001
        return partial(count_tokens_approximately, chars_per_token=3.3)
    return count_tokens_approximately


def _get_content_str(content: Any) -> str | None:
    """Convert ToolMessage content to string for size checking."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
            return str(content[0].get("text", ""))
        return str(content)
    return str(content)


def _format_offload_placeholder(file_path: str, content_sample: str) -> str:
    """Format the placeholder message for offloaded content."""
    return (
        f"[ToolResultOffloaded]\n\n"
        f"文件路径: {file_path}\n"
        f"可以使用 read_file 工具读取完整内容\n\n"
        f"--- 内容预览 ---\n{content_sample}"
    )


def _offload_tool_result(msg: ToolMessage, threshold: int, token_counter: TokenCounter) -> dict[str, Any] | None:
    """卸载单个超阈值的工具结果.

    Args:
        msg: ToolMessage
        threshold: token 数阈值
        token_counter: token 计数函数

    Returns:
        包含 file 更新的字典，如果没有卸载则返回 None
    """
    content = msg.content
    content_str = _get_content_str(content)

    if content_str is None:
        return None

    # 计算 token 数
    msg_tokens = token_counter([msg])
    if msg_tokens <= threshold:
        return None

    # 获取工具名称和参数
    tool_name = msg.name or "unknown"
    tool_call_id = msg.tool_call_id or ""

    # 生成文件路径 (工具名称-xxx)
    message_id = msg.id or str(uuid.uuid4())[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
    file_path = f"{_OFFLOAD_DIR}/{safe_name}-{message_id}"

    # 构建文件头部信息
    header_lines = [
        "=== Tool Invocation ===",
        f"Tool: {tool_name}",
        f"Tool Call ID: {tool_call_id}",
        "=" * 40,
        "",
    ]
    header = "\n".join(header_lines)

    # 保存到 files 格式（包含头部信息）
    from datetime import datetime

    timestamp = datetime.now().isoformat()
    files_update = {
        file_path: {
            "content": [header + content_str],
            "created_at": timestamp,
            "modified_at": timestamp,
        }
    }

    # 创建预览内容
    preview_lines = content_str.splitlines()[:10]
    content_sample = "\n".join(line[:500] for line in preview_lines)

    # 替换消息内容为占位符
    msg.content = _format_offload_placeholder(file_path, content_sample)

    return files_update


def _offload_tool_results(
    messages: list[AnyMessage], threshold: int, token_counter: TokenCounter
) -> tuple[dict[str, Any], list[AnyMessage]]:
    """扫描消息列表，卸载所有超阈值的工具结果.

    Args:
        messages: 消息列表
        threshold: token 数阈值
        token_counter: token 计数函数

    Returns:
        tuple[files 更新字典, 被修改的消息列表]
    """
    files_update: dict[str, Any] = {}
    modified_messages: list[AnyMessage] = []

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue

        result = _offload_tool_result(msg, threshold, token_counter)
        if result:
            files_update.update(result)
            modified_messages.append(msg)

    return files_update, modified_messages


class SummaryOffloadMiddleware(AgentMiddleware):
    """总结+工具结果卸载中间件.

    基于 LangChain SummarizationMiddleware，额外功能：
    - 保留原有的 summary 历史记录逻辑
    - 将 ToolMessage 的 results 卸载到虚拟文件系统
      1. 触发 Summary 时，卸载超过阈值的工具结果
      2. 智能保留策略：
        - 触发 Summary 时，首先进行卸载
        - 只有当总 Token 数超过 max_retention_ratio * trigger 时，才进行消息清理(Summary)
        - 始终保留 System Message
    """

    def __init__(
        self,
        model: str | BaseChatModel,
        *,
        trigger: ContextSize | list[ContextSize] | None = None,
        keep: ContextSize = ("messages", _DEFAULT_MESSAGES_TO_KEEP),
        token_counter: TokenCounter = count_tokens_approximately,
        summary_prompt: str = DEFAULT_SUMMARY_PROMPT,
        trim_tokens_to_summarize: int | None = _DEFAULT_TRIM_TOKEN_LIMIT,
        # 工具结果卸载参数
        summary_offload_threshold: int = 1000,
        max_retention_ratio: float = 0.6,
        **deprecated_kwargs: Any,
    ) -> None:
        """初始化中间件.

        Args:
            model: 用于生成摘要的语言模型
            trigger: 触发摘要的阈值条件 (建议使用 ("tokens", N))
            keep: 摘要后保留的消息数量/ token 策略 (作为 fallback)
            token_counter: token 计数函数
            summary_prompt: 生成摘要的提示词模板
            trim_tokens_to_summarize: 准备摘要消息时的最大 token 数
            summary_offload_threshold: Summary 时卸载阈值（token 数），默认 1000
            max_retention_ratio: 触发 Summary 后，如果不超过此比例（相对于 trigger），则不删除消息。默认 0.6
        """
        super().__init__()

        # Handle renamed argument for backward compatibility if needed,
        # but since we are refactoring, we map deprecated 'result_offload_threshold'
        # to 'summary_offload_threshold' if present in kwargs.
        if "result_offload_threshold" in deprecated_kwargs:
            summary_offload_threshold = deprecated_kwargs.pop("result_offload_threshold")

        if isinstance(model, str):
            model = init_chat_model(model)

        self.model = model
        if trigger is None:
            self.trigger: ContextSize | list[ContextSize] | None = None
            trigger_conditions: list[ContextSize] = []
        elif isinstance(trigger, list):
            validated_list = [self._validate_context_size(item, "trigger") for item in trigger]
            self.trigger = validated_list
            trigger_conditions = validated_list
        else:
            validated = self._validate_context_size(trigger, "trigger")
            self.trigger = validated
            trigger_conditions = [validated]
        self._trigger_conditions = trigger_conditions

        self.keep = self._validate_context_size(keep, "keep")
        if token_counter is count_tokens_approximately:
            self.token_counter = _get_approximate_token_counter(self.model)
        else:
            self.token_counter = token_counter
        self.summary_prompt = summary_prompt
        self.trim_tokens_to_summarize = trim_tokens_to_summarize

        # 工具结果卸载配置
        self.summary_offload_threshold = summary_offload_threshold
        self.max_retention_ratio = max_retention_ratio

        # 检查 fractional 配置需要的 model profile
        requires_profile = any(condition[0] == "fraction" for condition in self._trigger_conditions)
        if self.keep[0] == "fraction":
            requires_profile = True
        if requires_profile and self._get_profile_limits() is None:
            msg = (
                "Model profile information is required to use fractional token limits, "
                "and is unavailable for the specified model. Please use absolute token "
                "counts instead, or pass "
                '`ChatModel(..., profile={"max_input_tokens": ...})`.'
            )
            raise ValueError(msg)

    def _get_token_trigger_value(self) -> int | None:
        """Helper to get the token trigger value."""
        if not self._trigger_conditions:
            return None

        for kind, value in self._trigger_conditions:
            if kind == "tokens":
                return int(value)
            # Support fractional if needed, converting to tokens using profile
            if kind == "fraction":
                max_input_tokens = self._get_profile_limits()
                if max_input_tokens:
                    return int(max_input_tokens * value)
        return None

    @override
    def before_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        """Process messages before model invocation, potentially triggering summarization."""

        messages = state["messages"]

        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)

        # 1. 检查是否触发 Summary
        if not self._should_summarize(messages, total_tokens):
            return None

        # 2. 触发 Summary：卸载超阈值的工具结果
        files_update: dict[str, Any] = {}
        modified_messages: list[AnyMessage] = []

        agg_files, agg_msgs = _offload_tool_results(messages, self.summary_offload_threshold, self.token_counter)
        files_update = agg_files
        modified_messages = agg_msgs

        # 3. 检查 Retention Ratio
        current_tokens = self.token_counter(messages)
        trigger_value = self._get_token_trigger_value()

        retention_limit = float("inf")
        if trigger_value:
            retention_limit = trigger_value * self.max_retention_ratio

        if current_tokens <= retention_limit:
            if files_update:
                return {"files": files_update, "messages": modified_messages}
            return None

        # 4. 超过 limit，需要 Eviction (Summary)
        system_msg_count = 0
        messages_to_process = messages

        if messages and messages[0].type == "system":
            system_msg_count = 1
            messages_to_process = messages[1:]

        cutoff_relative = self._find_cutoff_by_token_limit(messages_to_process, int(retention_limit))
        cutoff_index = system_msg_count + cutoff_relative

        if cutoff_index <= system_msg_count:
            if files_update:
                return {"files": files_update, "messages": modified_messages}
            return None

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)
        summary = self._create_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary)

        # 如果有 System Message，需要保留在最前面
        final_messages = []

        if system_msg_count > 0:
            final_messages.append(messages[0])

        final_messages.extend(new_messages)
        final_messages.extend(preserved_messages)

        result: dict[str, Any] = {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *final_messages]}

        if files_update:
            result["files"] = files_update

        return result

    @override
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        """Process messages before model invocation, potentially triggering summarization."""

        messages = state["messages"]

        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)

        # 1. 检查是否触发 Summary
        if not self._should_summarize(messages, total_tokens):
            return None

        # 2. 触发 Summary：卸载超阈值的工具结果
        files_update: dict[str, Any] = {}
        modified_messages: list[AnyMessage] = []

        agg_files, agg_msgs = _offload_tool_results(messages, self.summary_offload_threshold, self.token_counter)
        files_update = agg_files
        modified_messages = agg_msgs

        # 3. 检查 Retention Ratio
        current_tokens = self.token_counter(messages)
        trigger_value = self._get_token_trigger_value()

        retention_limit = float("inf")
        if trigger_value:
            retention_limit = trigger_value * self.max_retention_ratio

        if current_tokens <= retention_limit:
            if files_update:
                return {"files": files_update, "messages": modified_messages}
            return None

        # 4. 超过 limit，需要 Eviction (Summary)
        system_msg_count = 0
        messages_to_process = messages

        if messages and messages[0].type == "system":
            system_msg_count = 1
            messages_to_process = messages[1:]

        cutoff_relative = self._find_cutoff_by_token_limit(messages_to_process, int(retention_limit))
        cutoff_index = system_msg_count + cutoff_relative

        if cutoff_index <= system_msg_count:
            if files_update:
                return {"files": files_update, "messages": modified_messages}
            return None

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)

        summary = await self._acreate_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary)

        final_messages = []

        if system_msg_count > 0:
            final_messages.append(messages[0])

        final_messages.extend(new_messages)
        final_messages.extend(preserved_messages)

        result: dict[str, Any] = {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *final_messages]}

        if files_update:
            result["files"] = files_update

        return result

    def _should_summarize(self, messages: list[AnyMessage], total_tokens: int) -> bool:
        """Determine whether summarization should run for the current token usage."""
        if not self._trigger_conditions:
            return False

        for kind, value in self._trigger_conditions:
            if kind == "messages" and len(messages) >= value:
                return True
            if kind == "tokens" and total_tokens >= value:
                return True
            if kind == "fraction":
                max_input_tokens = self._get_profile_limits()
                if max_input_tokens is None:
                    continue
                threshold = int(max_input_tokens * value)
                if threshold <= 0:
                    threshold = 1
                if total_tokens >= threshold:
                    return True
        return False

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        """Choose cutoff index respecting retention configuration."""
        kind, value = self.keep
        if kind in {"tokens", "fraction"}:
            token_based_cutoff = self._find_token_based_cutoff(messages)
            if token_based_cutoff is not None:
                return token_based_cutoff
            return self._find_safe_cutoff(messages, _DEFAULT_MESSAGES_TO_KEEP)
        return self._find_safe_cutoff(messages, cast("int", value))

    def _find_token_based_cutoff(self, messages: list[AnyMessage]) -> int | None:
        """Find cutoff index based on target token retention."""
        if not messages:
            return 0

        kind, value = self.keep
        if kind == "fraction":
            max_input_tokens = self._get_profile_limits()
            if max_input_tokens is None:
                return None
            target_token_count = int(max_input_tokens * value)
        elif kind == "tokens":
            target_token_count = int(value)
        else:
            return None

        if target_token_count <= 0:
            target_token_count = 1

        if self.token_counter(messages) <= target_token_count:
            return 0

        # 二分查找
        left, right = 0, len(messages)
        cutoff_candidate = len(messages)
        max_iterations = len(messages).bit_length() + 1
        for _ in range(max_iterations):
            if left >= right:
                break

            mid = (left + right) // 2
            if self.token_counter(messages[mid:]) <= target_token_count:
                cutoff_candidate = mid
                right = mid
            else:
                left = mid + 1

        if cutoff_candidate == len(messages):
            cutoff_candidate = left

        if cutoff_candidate >= len(messages):
            if len(messages) == 1:
                return 0
            cutoff_candidate = len(messages) - 1

        return self._find_safe_cutoff_point(messages, cutoff_candidate)

    def _find_cutoff_by_token_limit(self, messages: list[AnyMessage], max_tokens: int) -> int:
        """Find cutoff index to ensure total tokens <= max_tokens."""
        if not messages or self.token_counter(messages) <= max_tokens:
            return 0

        # Binary search for cutoff
        left, right = 0, len(messages)
        cutoff_candidate = len(messages)
        max_iterations = len(messages).bit_length() + 1

        for _ in range(max_iterations):
            if left >= right:
                break

            mid = (left + right) // 2
            # Calculate tokens for preserved part: messages[mid:]
            if self.token_counter(messages[mid:]) <= max_tokens:
                cutoff_candidate = mid
                right = mid
            else:
                left = mid + 1

        if cutoff_candidate == len(messages):
            cutoff_candidate = left

        return self._find_safe_cutoff_point(messages, cutoff_candidate)

    def _get_profile_limits(self) -> int | None:
        """Retrieve max input token limit from the model profile."""
        try:
            profile = self.model.profile
        except AttributeError:
            return None

        if not isinstance(profile, Mapping):
            return None

        max_input_tokens = profile.get("max_input_tokens")

        if not isinstance(max_input_tokens, int):
            return None

        return max_input_tokens

    @staticmethod
    def _validate_context_size(context: ContextSize, parameter_name: str) -> ContextSize:
        """Validate context configuration tuples."""
        kind, value = context
        if kind == "fraction":
            if not 0 < value <= 1:
                msg = f"Fractional {parameter_name} values must be between 0 and 1, got {value}."
                raise ValueError(msg)
        elif kind in {"tokens", "messages"}:
            if value <= 0:
                msg = f"{parameter_name} thresholds must be greater than 0, got {value}."
                raise ValueError(msg)
        else:
            msg = f"Unsupported context size type {kind} for {parameter_name}."
            raise ValueError(msg)
        return context

    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        return [
            HumanMessage(
                content=f"Here is a summary of the conversation to date:\n\n{summary}",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]

    @staticmethod
    def _ensure_message_ids(messages: list[AnyMessage]) -> None:
        """Ensure all messages have unique IDs for the add_messages reducer."""
        for msg in messages:
            if msg.id is None:
                msg.id = str(uuid.uuid4())

    @staticmethod
    def _partition_messages(
        conversation_messages: list[AnyMessage],
        cutoff_index: int,
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Partition messages into those to summarize and those to preserve."""
        messages_to_summarize = conversation_messages[:cutoff_index]
        preserved_messages = conversation_messages[cutoff_index:]

        return messages_to_summarize, preserved_messages

    def _find_safe_cutoff(self, messages: list[AnyMessage], messages_to_keep: int) -> int:
        """Find safe cutoff point that preserves AI/Tool message pairs."""
        if len(messages) <= messages_to_keep:
            return 0

        target_cutoff = len(messages) - messages_to_keep
        return self._find_safe_cutoff_point(messages, target_cutoff)

    @staticmethod
    def _find_safe_cutoff_point(messages: list[AnyMessage], cutoff_index: int) -> int:
        """Find a safe cutoff point that doesn't split AI/Tool message pairs."""
        if cutoff_index >= len(messages) or not isinstance(messages[cutoff_index], ToolMessage):
            return cutoff_index

        tool_call_ids: set[str] = set()
        idx = cutoff_index
        while idx < len(messages) and isinstance(messages[idx], ToolMessage):
            tool_msg = cast("ToolMessage", messages[idx])
            if tool_msg.tool_call_id:
                tool_call_ids.add(tool_msg.tool_call_id)
            idx += 1

        for i in range(cutoff_index - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, AIMessage) and msg.tool_calls:
                ai_tool_call_ids = {tc.get("id") for tc in msg.tool_calls if tc.get("id")}
                if tool_call_ids & ai_tool_call_ids:
                    return i

        return idx

    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary for the given messages."""
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)

        try:
            response = self.model.invoke(self.summary_prompt.format(messages=formatted_messages))
            return response.text.strip()
        except Exception as e:
            return f"Error generating summary: {e!s}"

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary for the given messages."""
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)

        try:
            response = await self.model.ainvoke(self.summary_prompt.format(messages=formatted_messages))
            return response.text.strip()
        except Exception as e:
            return f"Error generating summary: {e!s}"

    def _trim_messages_for_summary(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Trim messages to fit within summary generation limits."""
        try:
            if self.trim_tokens_to_summarize is None:
                return messages
            return cast(
                "list[AnyMessage]",
                trim_messages(
                    messages,
                    max_tokens=self.trim_tokens_to_summarize,
                    token_counter=self.token_counter,
                    start_on="human",
                    strategy="last",
                    allow_partial=True,
                    include_system=True,
                ),
            )
        except Exception:
            return messages[-_DEFAULT_FALLBACK_MESSAGE_COUNT:]


# 便捷函数：创建中间件实例
def create_summary_offload_middleware(
    model: str | BaseChatModel,
    *,
    trigger: ContextSize | list[ContextSize] | None = None,
    keep: ContextSize = ("messages", _DEFAULT_MESSAGES_TO_KEEP),
    summary_offload_threshold: int = 1000,
    max_retention_ratio: float = 0.6,
) -> SummaryOffloadMiddleware:
    """创建 SummaryOffloadMiddleware 实例的便捷函数"""
    return SummaryOffloadMiddleware(
        model=model,
        trigger=trigger,
        keep=keep,
        summary_offload_threshold=summary_offload_threshold,
        max_retention_ratio=max_retention_ratio,
    )
