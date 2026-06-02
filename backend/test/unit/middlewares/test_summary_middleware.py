from __future__ import annotations

from types import SimpleNamespace

import pytest
from deepagents.middleware.summarization import SummarizationMiddleware

from yuxi.agents.backends.composite import create_agent_composite_backend
from yuxi.agents.middlewares.summary import create_summary_middleware
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS


class _DummyModel:
    _llm_type = "test-chat"
    profile = {"max_input_tokens": 128000}

    def invoke(self, _prompt: str) -> SimpleNamespace:
        return SimpleNamespace(text="summary")


@pytest.mark.unit
def test_create_summary_middleware_uses_deepagents_with_yuxi_outputs_root() -> None:
    middleware = create_summary_middleware(
        model=_DummyModel(),
        trigger=("tokens", 90_000),
        keep=("tokens", 45_000),
        trim_tokens_to_summarize=4000,
    )

    assert isinstance(middleware, SummarizationMiddleware)
    assert middleware._backend is create_agent_composite_backend
    assert middleware._history_path_prefix == VIRTUAL_PATH_CONVERSATION_HISTORY
    assert middleware._large_tool_results_prefix == VIRTUAL_PATH_LARGE_TOOL_RESULTS
    assert middleware._lc_helper.trigger == ("tokens", 90_000)
    assert middleware._lc_helper.keep == ("tokens", 45_000)
    assert middleware._lc_helper.trim_tokens_to_summarize == 4000
