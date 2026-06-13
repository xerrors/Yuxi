from types import SimpleNamespace

import httpx
import pytest
import requests

from yuxi.agents.models import load_chat_model, resolve_chat_model_spec
from yuxi.models.chat import LangChainChatAdapter, select_model
from yuxi.models.embed import OtherEmbedding, select_embedding_model
from yuxi.models.rerank import OpenAIReranker, get_reranker
from yuxi.models.providers.cache import ModelInfo


def _model_info(model_type: str) -> ModelInfo:
    return ModelInfo(
        provider_id="test-provider",
        model_id=f"namespace/{model_type}-model",
        model_type=model_type,
        display_name=f"Test {model_type}",
        api_key="test-key",
        base_url="https://example.com/v1",
        provider_type="openai",
        dimension=1024 if model_type == "embedding" else None,
    )


def _chat_model_info(provider_id: str, model_id: str, provider_type: str = "openai") -> ModelInfo:
    return ModelInfo(
        provider_id=provider_id,
        model_id=model_id,
        model_type="chat",
        display_name=model_id,
        api_key="test-key",
        base_url="https://example.com/v1",
        provider_type=provider_type,
    )


def _capture_embed_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    warnings = []
    monkeypatch.setattr(
        "yuxi.models.embed.logger",
        SimpleNamespace(
            warning=warnings.append,
            error=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
        ),
    )
    return warnings


def _requests_embedding_response(status_code: int, content: bytes | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://example.com/v1/embeddings"
    response._content = content or b'{"error":"temporary error"}'
    return response


def _httpx_embedding_response(status_code: int, content: str | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://example.com/v1/embeddings")
    return httpx.Response(status_code, request=request, text=content or '{"error":"temporary error"}')


@pytest.mark.parametrize(
    "selector,args",
    [
        (select_model, {"model_spec": "unknown-provider:namespace/model"}),
        (load_chat_model, {"fully_specified_name": "unknown-provider:namespace/model"}),
        (select_embedding_model, {"model_id": "unknown-provider:namespace/model"}),
        (get_reranker, {"model_id": "unknown-provider:namespace/model"}),
    ],
)
def test_selectors_report_unknown_unconfigured_specs(selector, args):
    with pytest.raises(ValueError, match="Unknown|未找到模型"):
        selector(**args)


def test_resolve_chat_model_spec_prefers_explicit_then_fallback_then_default(monkeypatch):
    monkeypatch.setattr("yuxi.agents.models.sys_config.default_model", "system-default:model")

    assert resolve_chat_model_spec(" explicit:model ", fallback="fallback:model") == "explicit:model"
    assert resolve_chat_model_spec("", fallback=" fallback:model ") == "fallback:model"
    assert resolve_chat_model_spec(None, fallback="") == "system-default:model"


def test_resolve_chat_model_spec_rejects_all_empty(monkeypatch):
    monkeypatch.setattr("yuxi.agents.models.sys_config.default_model", "")

    with pytest.raises(ValueError, match="model spec 不能为空"):
        resolve_chat_model_spec("", fallback=None)


def test_select_embedding_model_loads_model_from_cache(monkeypatch):
    monkeypatch.setattr(
        "yuxi.models.embed.model_cache.get_model_info",
        lambda spec: _model_info("embedding") if spec == "test-provider:namespace/embedding-model" else None,
    )

    model = select_embedding_model("test-provider:namespace/embedding-model")

    assert isinstance(model, OtherEmbedding)
    assert model.model == "namespace/embedding-model"
    assert model.dimension == 1024


def test_select_model_wraps_langchain_model_and_expands_model_params(monkeypatch):
    fake_model = SimpleNamespace()
    captured = {}

    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda spec: _chat_model_info("test-provider", "namespace/chat-model")
        if spec == "test-provider:namespace/chat-model"
        else None,
    )

    def fake_load_chat_model(spec, **kwargs):
        captured["spec"] = spec
        captured["kwargs"] = kwargs
        return fake_model

    monkeypatch.setattr("yuxi.models.chat.load_chat_model", fake_load_chat_model)

    model = select_model(
        "test-provider:namespace/chat-model",
        model_params={"temperature": 0.2},
        timeout=60.0,
    )

    assert isinstance(model, LangChainChatAdapter)
    assert model.model is fake_model
    assert model.model_name == "namespace/chat-model"
    assert captured == {
        "spec": "test-provider:namespace/chat-model",
        "kwargs": {"temperature": 0.2, "timeout": 60.0},
    }


def test_select_model_maps_anthropic_max_completion_tokens(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda spec: _chat_model_info("anthropic", "mimo-v2.5", provider_type="anthropic")
        if spec == "anthropic:mimo-v2.5"
        else None,
    )
    monkeypatch.setattr(
        "yuxi.models.chat.load_chat_model",
        lambda spec, **kwargs: captured.update({"spec": spec, "kwargs": kwargs}) or SimpleNamespace(),
    )

    select_model("anthropic:mimo-v2.5", model_params={"max_completion_tokens": 123})

    assert captured == {"spec": "anthropic:mimo-v2.5", "kwargs": {"max_tokens": 123}}


def test_load_chat_model_disables_tool_call_streaming_for_all_siliconflow(monkeypatch):
    monkeypatch.setattr(
        "yuxi.agents.models.model_cache.get_model_info",
        lambda spec: _chat_model_info("siliconflow-cn", "deepseek-ai/DeepSeek-V4-Flash")
        if spec == "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash"
        else None,
    )

    model = load_chat_model("siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash")
    explicit = load_chat_model("siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash", disable_streaming=False)

    assert model.disable_streaming == "tool_calling"
    assert explicit.disable_streaming is False


def test_load_chat_model_keeps_non_siliconflow_openai_streaming(monkeypatch):
    monkeypatch.setattr(
        "yuxi.agents.models.model_cache.get_model_info",
        lambda spec: _chat_model_info("openai-compatible", "namespace/chat-model")
        if spec == "openai-compatible:namespace/chat-model"
        else None,
    )

    model = load_chat_model("openai-compatible:namespace/chat-model")
    explicit = load_chat_model("openai-compatible:namespace/chat-model", disable_streaming=True)

    assert model.disable_streaming is False
    assert explicit.disable_streaming is True


@pytest.mark.asyncio
async def test_langchain_chat_adapter_preserves_call_response_contract():
    from langchain_core.messages import AIMessage

    captured = {}

    class FakeLangChainModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content=[{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}])

    adapter = LangChainChatAdapter(FakeLangChainModel(), model_name="test-model")

    response = await adapter.call([{"role": "user", "content": "Say hello"}], stream=False)

    assert response.content == "hello"
    assert response.is_full is False
    assert type(captured["messages"][0]).__name__ == "HumanMessage"


@pytest.mark.asyncio
async def test_embedding_connection_checks_configured_dimension(monkeypatch):
    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
        dimension=3,
    )

    async def fake_aencode(_messages):
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(model, "aencode", fake_aencode)

    assert await model.test_connection() == (True, "连接正常")


@pytest.mark.asyncio
async def test_embedding_connection_reports_dimension_mismatch(monkeypatch):
    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
        dimension=4,
    )

    async def fake_aencode(_messages):
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(model, "aencode", fake_aencode)

    assert await model.test_connection() == (False, "Embedding 维度不一致：配置 4，实际 3")


def test_embedding_sync_400_logs_warning(monkeypatch):
    warnings = _capture_embed_warnings(monkeypatch)
    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
    )
    response = _requests_embedding_response(400, b'{"error":"bad embedding input"}')
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr("yuxi.models.embed.requests.post", fake_post)

    with pytest.raises(ValueError, match="400 Client Error"):
        model.encode(["hello", "test"])

    assert len(calls) == 1
    assert len(warnings) == 1
    warning = warnings[0]
    assert "400 Bad Request" in warning
    assert "model=namespace/embedding-model" in warning
    assert "input_count=2" in warning
    assert "input_lengths=[5, 4]" in warning
    assert "bad embedding input" in warning


def test_embedding_sync_429_retries_ten_times_before_success(monkeypatch):
    warnings = _capture_embed_warnings(monkeypatch)
    sleeps = []
    monkeypatch.setattr("yuxi.models.embed.time.sleep", sleeps.append)

    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
    )
    success = _requests_embedding_response(200, b'{"data":[{"embedding":[0.1,0.2]}]}')
    responses = [_requests_embedding_response(429) for _ in range(10)] + [success]

    monkeypatch.setattr("yuxi.models.embed.requests.post", lambda *_args, **_kwargs: responses.pop(0))

    assert model.encode(["hello"]) == [[0.1, 0.2]]
    assert len(sleeps) == 10
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    assert len(warnings) == 10
    assert "status=429" in warnings[-1]
    assert "retry=10/10" in warnings[-1]


def test_embedding_sync_5xx_uses_short_retry_budget(monkeypatch):
    warnings = _capture_embed_warnings(monkeypatch)
    sleeps = []
    calls = []
    monkeypatch.setattr("yuxi.models.embed.time.sleep", sleeps.append)

    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
    )

    def fake_post(*_args, **_kwargs):
        calls.append(1)
        return _requests_embedding_response(503)

    monkeypatch.setattr("yuxi.models.embed.requests.post", fake_post)

    with pytest.raises(ValueError, match="503 Server Error"):
        model.encode(["hello"])

    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert len(warnings) == 2
    assert "retry=2/2" in warnings[-1]


@pytest.mark.asyncio
async def test_embedding_async_400_logs_warning(monkeypatch):
    warnings = _capture_embed_warnings(monkeypatch)
    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
    )

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def post(self, url, **_kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(400, request=request, text='{"error":"bad embedding input"}')

    monkeypatch.setattr("yuxi.models.embed.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(httpx.HTTPStatusError, match="400 Bad Request"):
        await model.aencode(["hello", "test"])

    assert len(warnings) == 1
    warning = warnings[0]
    assert "400 Bad Request" in warning
    assert "model=namespace/embedding-model" in warning
    assert "input_count=2" in warning
    assert "input_lengths=[5, 4]" in warning
    assert "bad embedding input" in warning


@pytest.mark.asyncio
async def test_embedding_async_429_retries_ten_times_before_success(monkeypatch):
    warnings = _capture_embed_warnings(monkeypatch)
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("yuxi.models.embed.asyncio.sleep", fake_sleep)

    model = OtherEmbedding(
        model="namespace/embedding-model",
        base_url="https://example.com/v1/embeddings",
        api_key="test-key",
    )
    success = _httpx_embedding_response(200, '{"data":[{"embedding":[0.1,0.2]}]}')
    responses = [_httpx_embedding_response(429) for _ in range(10)] + [success]

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def post(self, *_args, **_kwargs):
            return responses.pop(0)

    monkeypatch.setattr("yuxi.models.embed.httpx.AsyncClient", FakeAsyncClient)

    assert await model.aencode(["hello"]) == [[0.1, 0.2]]
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    assert len(warnings) == 10
    assert "status=429" in warnings[-1]
    assert "retry=10/10" in warnings[-1]


def test_get_reranker_loads_model_from_cache(monkeypatch):
    monkeypatch.setattr(
        "yuxi.models.rerank.model_cache.get_model_info",
        lambda spec: _model_info("rerank") if spec == "test-provider:namespace/rerank-model" else None,
    )

    reranker = get_reranker("test-provider:namespace/rerank-model")

    assert isinstance(reranker, OpenAIReranker)
    assert reranker.model == "namespace/rerank-model"
