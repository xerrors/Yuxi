"""Yuxi adapter for DeepAgents conversation summarization middleware."""

from __future__ import annotations

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.summarization import ContextSize
from langchain.chat_models import BaseChatModel

from yuxi.agents.backends.composite import create_agent_composite_backend
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS


def create_summary_middleware(
    model: str | BaseChatModel,
    *,
    trigger: ContextSize | list[ContextSize] | None,
    keep: ContextSize,
    trim_tokens_to_summarize: int | None = 4000,
) -> SummarizationMiddleware:
    """Create DeepAgents summarization middleware using Yuxi's virtual outputs root."""
    middleware = SummarizationMiddleware(
        model=model,
        backend=create_agent_composite_backend,
        trigger=trigger,
        keep=keep,
        trim_tokens_to_summarize=trim_tokens_to_summarize,
    )
    middleware._history_path_prefix = VIRTUAL_PATH_CONVERSATION_HISTORY
    middleware._large_tool_results_prefix = VIRTUAL_PATH_LARGE_TOOL_RESULTS
    return middleware
