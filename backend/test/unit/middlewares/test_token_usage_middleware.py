from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from yuxi.agents.middlewares.token_usage import TokenUsageMiddleware


def _request(*, run_id: str, model_spec: str, state: dict | None = None, model_name: str = "model-a"):
    return SimpleNamespace(
        model=SimpleNamespace(
            profile={"max_input_tokens": 2000},
            metadata={
                "yuxi_provider_id": model_spec.split(":", 1)[0],
                "yuxi_provider_type": "openai",
                "yuxi_model_id": model_name,
                "yuxi_model_spec": model_spec,
            },
        ),
        state=state or {"messages": [HumanMessage(content="old message")]},
        messages=[
            HumanMessage(content="current message"),
            ToolMessage(content="tool result", tool_call_id="call_1"),
        ],
        system_message=SystemMessage(content="system prompt"),
        tools=[],
        runtime=SimpleNamespace(context=SimpleNamespace(summary_threshold=2, model=model_spec, run_id=run_id)),
    )


def _response(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int | None,
    response_model: str,
    cache_read_key: str = "cache_read",
):
    input_details = {} if cache_read is None else {cache_read_key: cache_read}
    return ModelResponse(
        result=[
            AIMessage(
                content="answer",
                usage_metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "input_token_details": input_details,
                    "output_token_details": {"reasoning": 3},
                    "provider_label": "priority",
                },
                response_metadata={"model_name": response_model, "model_provider": "openai"},
            )
        ]
    )


def _response_without_usage(*, response_model: str):
    return ModelResponse(
        result=[
            AIMessage(
                content="answer",
                response_metadata={"model_name": response_model, "model_provider": "openai"},
            )
        ]
    )


@pytest.mark.asyncio
async def test_token_usage_middleware_records_context_and_run_usage() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="provider-a:model-a")

    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def handler(_request):
        return _response(input_tokens=12, output_tokens=5, cache_read=8, response_model="model-a-2026")

    result = await middleware.awrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    token_usage = result.command.update["token_usage"]
    assert token_usage["llm_input_tokens"] >= token_usage["llm_messages_tokens"]
    assert token_usage["context_window"] == 2000
    assert token_usage["current_run_id"] == "run-1"
    assert token_usage["latest"]["bucket_key"] == "provider-a:model-a"
    assert token_usage["latest"]["usage"]["provider_label"] == "priority"

    bucket = token_usage["run"]["models"]["provider-a:model-a"]
    assert bucket["model"]["response_model_ids"] == ["model-a-2026"]
    assert bucket["usage"] == {
        "input_tokens": 12,
        "output_tokens": 5,
        "total_tokens": 17,
        "input_token_details": {"cache_read": 8},
        "output_token_details": {"reasoning": 3},
    }
    assert bucket["cache_hit_ratio"] == 0.6667
    assert token_usage["run"]["complete"] is True
    assert token_usage["run"]["total"] == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    assert token_usage["thread"] == token_usage["run"]


@pytest.mark.asyncio
async def test_token_usage_middleware_groups_multiple_models_within_one_run() -> None:
    middleware = TokenUsageMiddleware()
    request_a = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(request_a.state, request_a.runtime)
    request_a.state["token_usage"] = initialized["token_usage"]

    async def handler_a(_request):
        return _response(input_tokens=100, output_tokens=20, cache_read=60, response_model="model-a-v1")

    first = await middleware.awrap_model_call(request_a, handler_a)
    request_b = _request(
        run_id="run-1",
        model_spec="provider-b:model-b",
        model_name="model-b",
        state={"messages": [], "token_usage": first.command.update["token_usage"]},
    )

    async def handler_b(_request):
        return _response(input_tokens=50, output_tokens=10, cache_read=None, response_model="model-b-v2")

    second = await middleware.awrap_model_call(request_b, handler_b)
    token_usage = second.command.update["token_usage"]

    assert set(token_usage["run"]["models"]) == {"provider-a:model-a", "provider-b:model-b"}
    assert token_usage["run"]["model_call_count"] == 2
    assert token_usage["run"]["total"] == {"input_tokens": 150, "output_tokens": 30, "total_tokens": 180}
    bucket_b = token_usage["run"]["models"]["provider-b:model-b"]
    assert bucket_b["cache_observed_call_count"] == 0
    assert bucket_b["cache_hit_ratio"] is None
    assert token_usage["run"]["complete"] is True


@pytest.mark.asyncio
async def test_token_usage_middleware_marks_missing_usage_incomplete() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def handler(_request):
        return _response_without_usage(response_model="model-a")

    result = await middleware.awrap_model_call(request, handler)
    aggregate = result.command.update["token_usage"]["run"]

    assert aggregate["model_call_count"] == 1
    assert aggregate["usage_reported_call_count"] == 0
    assert aggregate["complete"] is False


@pytest.mark.asyncio
async def test_token_usage_middleware_marks_partial_usage_incomplete() -> None:
    middleware = TokenUsageMiddleware()
    first_request = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(first_request.state, first_request.runtime)
    first_request.state["token_usage"] = initialized["token_usage"]

    async def first_handler(_request):
        return _response(input_tokens=10, output_tokens=2, cache_read=None, response_model="model-a")

    first = await middleware.awrap_model_call(first_request, first_handler)
    second_request = _request(
        run_id="run-1",
        model_spec="provider-a:model-a",
        state={"messages": [], "token_usage": first.command.update["token_usage"]},
    )

    async def second_handler(_request):
        return _response_without_usage(response_model="model-a")

    second = await middleware.awrap_model_call(second_request, second_handler)
    aggregate = second.command.update["token_usage"]["run"]

    assert aggregate["model_call_count"] == 2
    assert aggregate["usage_reported_call_count"] == 1
    assert aggregate["total"] == {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    assert aggregate["complete"] is False


@pytest.mark.asyncio
async def test_token_usage_middleware_resets_run_and_keeps_v2_thread_usage() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def first_handler(_request):
        return _response(input_tokens=100, output_tokens=20, cache_read=60, response_model="model-a")

    first = await middleware.awrap_model_call(request, first_handler)
    next_runtime = SimpleNamespace(context=SimpleNamespace(run_id="run-2"))
    next_state = {"messages": [], "token_usage": first.command.update["token_usage"]}
    reset = middleware.before_agent(next_state, next_runtime)["token_usage"]

    assert reset["current_run_id"] == "run-2"
    assert reset["latest"] is None
    assert reset["run"]["models"] == {}
    assert reset["thread"]["total"] == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    next_request = _request(
        run_id="run-2",
        model_spec="provider-a:model-a",
        state={"messages": [], "token_usage": reset},
    )

    async def second_handler(_request):
        return _response(input_tokens=50, output_tokens=10, cache_read=20, response_model="model-a")

    second = await middleware.awrap_model_call(next_request, second_handler)
    second_usage = second.command.update["token_usage"]

    assert second_usage["run"]["total"] == {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
    assert second_usage["thread"]["total"] == {"input_tokens": 150, "output_tokens": 30, "total_tokens": 180}
    thread_bucket = second_usage["thread"]["models"]["provider-a:model-a"]
    assert thread_bucket["cache_read_input_tokens"] == 80
    assert thread_bucket["cache_observed_input_tokens"] == 150


@pytest.mark.parametrize(
    (
        "state",
        "run_id",
        "expected_current_run_id",
        "expected_run_model_keys",
        "expected_thread_model_keys",
        "expected_thread_total",
    ),
    [
        (
            {
                "messages": [],
                "token_usage": {
                    "current_run_id": "run-1",
                    "run": {"schema_version": 2, "models": {}, "total": {}},
                    "thread": {
                        "schema_version": 2,
                        "model_call_count": 2,
                        "usage_reported_call_count": 2,
                        "models": {
                            "siliconflow-cn:model-a": {
                                "model": {"provider_id": "siliconflow-cn"},
                                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                                "model_call_count": 1,
                                "usage_reported_call_count": 1,
                            },
                            "provider-b:model-b": {
                                "model": {"provider_id": "provider-b"},
                                "usage": {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
                                "model_call_count": 1,
                                "usage_reported_call_count": 1,
                            },
                        },
                        "total": {"input_tokens": 150, "output_tokens": 30, "total_tokens": 180},
                    },
                },
            },
            "run-2",
            "run-2",
            set(),
            {"provider-b:model-b"},
            {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        ),
        (
            {
                "messages": [],
                "token_usage": {
                    "current_run_id": "run-1",
                    "latest": {"bucket_key": "siliconflow:model-a"},
                    "run": {
                        "schema_version": 2,
                        "models": {
                            "siliconflow:model-a": {
                                "model": {"provider_id": "siliconflow"},
                                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                                "model_call_count": 1,
                                "usage_reported_call_count": 1,
                            }
                        },
                        "total": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                    },
                    "thread": {
                        "schema_version": 2,
                        "models": {
                            "siliconflow:model-a": {
                                "model": {"provider_id": "siliconflow"},
                                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                                "model_call_count": 1,
                                "usage_reported_call_count": 1,
                            }
                        },
                        "total": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                    },
                },
            },
            "run-1",
            "run-1",
            set(),
            set(),
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        ),
    ],
)
def test_token_usage_middleware_removes_blacklisted_buckets_from_previous_state(
    state: dict,
    run_id: str,
    expected_current_run_id: str,
    expected_run_model_keys: set,
    expected_thread_model_keys: set,
    expected_thread_total: dict,
) -> None:
    middleware = TokenUsageMiddleware()
    runtime = SimpleNamespace(context=SimpleNamespace(run_id=run_id))

    reset = middleware.before_agent(state, runtime)["token_usage"]

    assert reset["current_run_id"] == expected_current_run_id
    assert reset["latest"] is None
    assert set(reset["run"]["models"]) == expected_run_model_keys
    assert set(reset["thread"]["models"]) == expected_thread_model_keys
    assert reset["thread"]["total"] == expected_thread_total


def test_token_usage_middleware_discards_unattributed_v1_thread_totals() -> None:
    middleware = TokenUsageMiddleware()
    state = {
        "messages": [],
        "token_usage": {
            "cumulative_model_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            "model": {"configured_model_spec": "provider-a:model-a"},
        },
    }
    runtime = SimpleNamespace(context=SimpleNamespace(run_id="run-2"))

    reset = middleware.before_agent(state, runtime)["token_usage"]

    assert reset["thread"]["models"] == {}
    assert reset["thread"]["total"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert "cumulative_model_usage" not in reset
    assert "model" not in reset


@pytest.mark.asyncio
async def test_token_usage_middleware_distinguishes_reported_zero_cache_from_unknown() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def handler(_request):
        return _response(input_tokens=0, output_tokens=1, cache_read=0, response_model="model-a")

    result = await middleware.awrap_model_call(request, handler)
    bucket = result.command.update["token_usage"]["run"]["models"]["provider-a:model-a"]

    assert bucket["cache_observed_call_count"] == 1
    assert bucket["cache_read_input_tokens"] == 0
    assert bucket["cache_hit_ratio"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_read_key", ["priority_cache_read", "flex_cache_read"])
async def test_token_usage_middleware_records_service_tier_cache_usage(cache_read_key: str) -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="provider-a:model-a")
    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def handler(_request):
        return _response(
            input_tokens=100,
            output_tokens=20,
            cache_read=40,
            response_model="model-a",
            cache_read_key=cache_read_key,
        )

    result = await middleware.awrap_model_call(request, handler)
    bucket = result.command.update["token_usage"]["run"]["models"]["provider-a:model-a"]

    assert bucket["cache_observed_call_count"] == 1
    assert bucket["cache_read_input_tokens"] == 40
    assert bucket["cache_hit_ratio"] == 0.4


@pytest.mark.asyncio
async def test_token_usage_middleware_skips_blacklisted_siliconflow_usage() -> None:
    middleware = TokenUsageMiddleware()
    request = _request(run_id="run-1", model_spec="siliconflow-cn:model-a")
    initialized = middleware.before_agent(request.state, request.runtime)
    request.state["token_usage"] = initialized["token_usage"]

    async def handler(_request):
        return _response(input_tokens=100, output_tokens=20, cache_read=50, response_model="model-a")

    result = await middleware.awrap_model_call(request, handler)
    token_usage = result.command.update["token_usage"]

    assert token_usage["llm_input_tokens"] > 0
    assert token_usage["latest"] is None
    assert token_usage["run"]["models"] == {}
    assert token_usage["run"]["model_call_count"] == 1
    assert token_usage["run"]["usage_unavailable_call_count"] == 1
    assert token_usage["run"]["complete"] is False
    assert token_usage["run"]["total"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert token_usage["thread"]["models"] == {}
