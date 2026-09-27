import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
import requests
from langchain_core.messages import HumanMessage
from yuxi.models.chat import LangChainChatAdapter, load_chat_model, resolve_chat_model_spec, select_model
from yuxi.models.embed import OtherEmbedding, select_embedding_model
from yuxi.models.providers.cache import ModelInfo
from yuxi.models.rerank import OpenAIReranker, get_reranker


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


def _chat_model_info(
    provider_id: str,
    model_id: str,
    provider_type: str = "openai",
    request_body_overrides: dict | None = None,
    include_user_uid: bool = False,
) -> ModelInfo:
    return ModelInfo(
        provider_id=provider_id,
        model_id=model_id,
        model_type="chat",
        display_name=model_id,
        api_key="test-key",
        base_url="https://example.com/v1",
        provider_type=provider_type,
        request_body_overrides=request_body_overrides or {},
        include_user_uid=include_user_uid,
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


def test_resolve_chat_model_spec_prefers_explicit_then_fallback():
    assert resolve_chat_model_spec(" explicit:model ", fallback="fallback:model") == "explicit:model"
    assert resolve_chat_model_spec("", fallback=" fallback:model ") == "fallback:model"


def test_resolve_chat_model_spec_rejects_all_empty():
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
        lambda spec: (
            _chat_model_info("test-provider", "namespace/chat-model")
            if spec == "test-provider:namespace/chat-model"
            else None
        ),
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
        lambda spec: (
            _chat_model_info("anthropic", "mimo-v2.5", provider_type="anthropic")
            if spec == "anthropic:mimo-v2.5"
            else None
        ),
    )
    monkeypatch.setattr(
        "yuxi.models.chat.load_chat_model",
        lambda spec, **kwargs: captured.update({"spec": spec, "kwargs": kwargs}) or SimpleNamespace(),
    )

    select_model("anthropic:mimo-v2.5", model_params={"max_completion_tokens": 123})

    assert captured == {"spec": "anthropic:mimo-v2.5", "kwargs": {"max_tokens": 123}}


def test_load_chat_model_uses_toolcall_chunk_fix_for_openai_compatible(monkeypatch):
    from yuxi.models.chat import ChatCompletionsAdapter

    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda spec: (
            _chat_model_info("siliconflow-cn", "deepseek-ai/DeepSeek-V4-Flash")
            if spec == "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash"
            else None
        ),
    )

    model = load_chat_model("siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash")

    # 不再按 provider 禁用流式，改用归一化子类规避 v3 流式累积丢 tool_call 字段的缺陷
    assert isinstance(model, ChatCompletionsAdapter)
    assert model.disable_streaming is False
    assert model.metadata["yuxi_provider_id"] == "siliconflow-cn"
    assert model.metadata["yuxi_provider_type"] == "openai"
    assert model.metadata["yuxi_model_id"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert model.metadata["yuxi_model_spec"] == "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash"


def test_load_chat_model_keeps_non_siliconflow_openai_streaming(monkeypatch):
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda spec: (
            _chat_model_info("openai-compatible", "namespace/chat-model")
            if spec == "openai-compatible:namespace/chat-model"
            else None
        ),
    )

    model = load_chat_model("openai-compatible:namespace/chat-model")
    explicit = load_chat_model("openai-compatible:namespace/chat-model", disable_streaming=True)

    assert model.disable_streaming is False
    assert explicit.disable_streaming is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["opencode", "opencode-go", "openai-compatible"])
async def test_opencode_session_headers_reach_stream_and_regular_requests(monkeypatch, provider_id):
    """真实 SDK 请求携带稳定会话头，其他供应商不受影响。"""
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info(provider_id, "test-model"),
    )
    requests_seen = []

    def respond(request):
        """捕获出站协议并返回确定性模型结果。"""
        requests_seen.append(request)
        body = json.loads(request.content)
        assert "session_id" not in body
        if body.get("stream"):
            event = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for session_id in ["thread-a", "thread-a", "thread-b"]:
            model = load_chat_model(
                f"{provider_id}:test-model",
                session_id=session_id,
                http_async_client=client,
                default_headers={"X-Test": "preserved"},
            )
            assert (await model.ainvoke("hi")).text == "ok"
            assert "".join([chunk.text async for chunk in model.astream("hi")]) == "ok"

    assert len(requests_seen) == 6
    for request, session_id in zip(requests_seen, ["thread-a"] * 4 + ["thread-b"] * 2, strict=True):
        assert request.headers["X-Test"] == "preserved"
        if provider_id in {"opencode", "opencode-go"}:
            assert request.headers["x-opencode-session"] == session_id
            assert request.headers["user-agent"].startswith("yuxi/")
        else:
            assert "x-opencode-session" not in request.headers


def test_opencode_standalone_models_have_distinct_stable_sessions(monkeypatch):
    """无 Thread 的独立操作使用各模型实例自己的会话 ID。"""
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info", lambda _spec: _chat_model_info("opencode-go", "test-model")
    )
    first = load_chat_model("opencode-go:test-model")
    second = load_chat_model("opencode-go:test-model")
    assert first.default_headers["x-opencode-session"]
    assert first.default_headers["x-opencode-session"] != second.default_headers["x-opencode-session"]


@pytest.mark.parametrize(
    ("include_user_uid", "uid", "expected"),
    [
        (True, "user-42", "user-42"),
        (False, "user-42", None),
        (True, None, None),
        (True, "   ", None),
    ],
)
async def test_load_chat_model_user_uid_header_follows_opt_in(monkeypatch, include_user_uid, uid, expected):
    """按开关与 uid 存在性注入 x-yuxi-uid；缺 uid 的路径不注入也不伪造身份。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=include_user_uid),
    )
    requests_seen = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(_deterministic_chat_responder(requests_seen))) as client:
        model = load_chat_model("uid-provider:test-model", uid=uid, http_async_client=client)
        await model.ainvoke("hi")

    if expected is None:
        assert "x-yuxi-uid" not in requests_seen[0].headers
        assert "x-yuxi-uid-sig" not in requests_seen[0].headers
    else:
        assert requests_seen[0].headers["x-yuxi-uid"] == expected


def test_load_chat_model_rejects_header_unsafe_uid(monkeypatch):
    """含 CR/LF 等头注入字符的 uid 显式失败，不发出畸形请求。"""
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )

    with pytest.raises(ValueError, match="x-yuxi-uid"):
        load_chat_model("uid-provider:test-model", uid="user-42\r\nX-Evil: 1")


async def test_load_chat_model_signs_user_uid_header(monkeypatch):
    """开启开关后，出站请求附带时间戳与可独立重算的 HMAC-SHA256 签名。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )
    requests_seen = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(_deterministic_chat_responder(requests_seen))) as client:
        model = load_chat_model("uid-provider:test-model", uid="user-42", http_async_client=client)
        await model.ainvoke("hi")

    headers = requests_seen[0].headers
    assert headers["x-yuxi-uid"] == "user-42"
    timestamp = headers["x-yuxi-uid-ts"]
    assert abs(int(timestamp) - int(time.time())) <= 5
    assert headers["x-yuxi-uid-sig"] == _user_uid_signature("user-42", timestamp)


def test_load_chat_model_rejects_signing_without_secret(monkeypatch):
    """开启 UID 头但固定签名密钥缺失时拒绝发请求，不静默降级为未签名。"""
    monkeypatch.delenv("YUXI_UID_SIGNATURE_SECRET", raising=False)
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )

    with pytest.raises(ValueError, match="YUXI_UID_SIGNATURE_SECRET"):
        load_chat_model("uid-provider:test-model", uid="user-42")


def test_load_chat_model_rejects_uid_header_for_gemini(monkeypatch):
    """直接遇到启用 UID 头的 Gemini 缓存配置时显式失败。"""
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", provider_type="gemini", include_user_uid=True),
    )

    with pytest.raises(ValueError, match="Gemini.*不支持"):
        load_chat_model("uid-provider:test-model", uid="user-42")


def _user_uid_signature(uid: str, timestamp: str) -> str:
    """按网关契约独立重算 HMAC-SHA256 签名。"""
    return base64.b64encode(
        hmac.new(b"unit-test-signing-secret", f"uid={uid}\nts={timestamp}".encode(), hashlib.sha256).digest()
    ).decode()


def _deterministic_chat_responder(requests_seen: list):
    """构造记录出站请求的确定性 OpenAI 响应，覆盖流式与非流式两条协议。"""

    def respond(request):
        requests_seen.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            event = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    return respond


def test_user_uid_signature_binds_uid_and_timestamp():
    """签名绑定 uid 与时间戳：任意一侧被篡改，网关侧重算即失配。"""
    secret = "unit-test-signing-secret"
    uid, timestamp = "user-42", "1758537600"
    message = f"uid={uid}\nts={timestamp}"
    signature = base64.b64encode(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()).decode()

    def gateway_verify(signed_uid: str, signed_ts: str) -> bool:
        expected = base64.b64encode(
            hmac.new(secret.encode(), f"uid={signed_uid}\nts={signed_ts}".encode(), hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)

    assert gateway_verify(uid, timestamp) is True
    assert gateway_verify("attacker-99", timestamp) is False
    assert gateway_verify(uid, "1758537999") is False


@pytest.mark.asyncio
async def test_user_uid_header_reaches_real_stream_and_regular_requests(monkeypatch):
    """开启后流式与非流式出站请求都携带 x-yuxi-uid，调用方已有请求头保留。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )
    requests_seen = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(_deterministic_chat_responder(requests_seen))) as client:
        model = load_chat_model(
            "uid-provider:test-model",
            uid="user-42",
            http_async_client=client,
            default_headers={"X-Test": "preserved"},
        )
        assert (await model.ainvoke("hi")).text == "ok"
        assert "".join([chunk.text async for chunk in model.astream("hi")]) == "ok"

    assert len(requests_seen) == 2
    for request in requests_seen:
        assert request.headers["x-yuxi-uid"] == "user-42"
        assert request.headers["X-Test"] == "preserved"
        timestamp = request.headers["x-yuxi-uid-ts"]
        assert request.headers["x-yuxi-uid-sig"] == _user_uid_signature("user-42", timestamp)


@pytest.mark.asyncio
async def test_user_uid_signing_fails_closed_when_secret_removed_after_load(monkeypatch):
    """构图后密钥被移除（例如只重建了部分容器）时，发送边界显式失败而非发未签名头。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )
    model = load_chat_model("uid-provider:test-model", uid="user-42")
    monkeypatch.delenv("YUXI_UID_SIGNATURE_SECRET")

    with pytest.raises(ValueError, match="YUXI_UID_SIGNATURE_SECRET"):
        await model.ainvoke("hi")


@pytest.mark.asyncio
async def test_user_uid_signature_refreshed_after_window_expiry(monkeypatch):
    """同一模型实例跨越验签窗口后再次发送时重新签名，不复用构图时的时间戳。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", include_user_uid=True),
    )
    clock = {"now": 1_700_000_000}
    monkeypatch.setattr("yuxi.models.chat.time", SimpleNamespace(time=lambda: clock["now"]))
    requests_seen = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(_deterministic_chat_responder(requests_seen))) as client:
        model = load_chat_model("uid-provider:test-model", uid="user-42", http_async_client=client)
        await model.ainvoke("hi")
        clock["now"] += 301
        await model.ainvoke("hi")

    first, second = (request.headers for request in requests_seen)
    assert int(first["x-yuxi-uid-ts"]) == int(second["x-yuxi-uid-ts"]) - 301
    assert first["x-yuxi-uid-sig"] != second["x-yuxi-uid-sig"]
    for headers in (first, second):
        assert headers["x-yuxi-uid-sig"] == _user_uid_signature(headers["x-yuxi-uid"], headers["x-yuxi-uid-ts"])


def test_anthropic_model_signs_user_uid_at_every_send_boundary(monkeypatch):
    """anthropic 家族同样在发送边界现算签名，跨窗口复用实例不带过期时间戳。"""
    monkeypatch.setenv("YUXI_UID_SIGNATURE_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", provider_type="anthropic", include_user_uid=True),
    )
    clock = {"now": 1_700_000_000}
    monkeypatch.setattr("yuxi.models.chat.time", SimpleNamespace(time=lambda: clock["now"]))

    model = load_chat_model("uid-provider:test-model", uid="user-42")

    # 流式与非流式都经 _get_request_payload 出站，这里直接验证同一发送边界两次调用。
    first = model._get_request_payload([HumanMessage(content="hi")])["extra_headers"]
    clock["now"] += 301
    second = model._get_request_payload([HumanMessage(content="hi")])["extra_headers"]

    assert first["x-yuxi-uid"] == second["x-yuxi-uid"] == "user-42"
    assert int(second["x-yuxi-uid-ts"]) == int(first["x-yuxi-uid-ts"]) + 301
    for headers in (first, second):
        assert headers["x-yuxi-uid-sig"] == _user_uid_signature(headers["x-yuxi-uid"], headers["x-yuxi-uid-ts"])


def test_anthropic_model_without_uid_switch_sends_no_extra_headers(monkeypatch):
    """未开启开关的 anthropic provider 不写入任何 UID 头。"""
    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda _spec: _chat_model_info("uid-provider", "test-model", provider_type="anthropic"),
    )

    model = load_chat_model("uid-provider:test-model", uid="user-42")

    assert "extra_headers" not in model._get_request_payload([HumanMessage(content="hi")])


def test_load_chat_model_merges_request_body_overrides_into_extra_body(monkeypatch):
    captured_body = {}

    monkeypatch.setattr(
        "yuxi.models.chat.model_cache.get_model_info",
        lambda spec: (
            _chat_model_info(
                "siliconflow-cn",
                "Qwen/Qwen3-8B",
                request_body_overrides={
                    "enable_thinking": False,
                    "reasoning_effort": "high",
                    "thinking_budget": 1024,
                },
            )
            if spec == "siliconflow-cn:Qwen/Qwen3-8B"
            else None
        ),
    )

    def capture_request(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "Qwen/Qwen3-8B",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(capture_request)) as http_client:
        model = load_chat_model(
            "siliconflow-cn:Qwen/Qwen3-8B",
            reasoning_effort="low",
            temperature=0.1,
            extra_body={"thinking_budget": 256, "caller_only": True},
            http_client=http_client,
        )
        response = model.invoke("hello")

    assert response.text == "ok"
    assert captured_body["temperature"] == 0.1
    assert captured_body["caller_only"] is True
    assert captured_body["enable_thinking"] is False
    assert captured_body["reasoning_effort"] == "high"
    assert captured_body["thinking_budget"] == 1024


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
