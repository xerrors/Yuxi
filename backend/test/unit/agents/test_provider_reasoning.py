"""使用真实 SDK 与 LangGraph 验证供应商推理协议边界。"""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.modules.models.chat import ChatCompletionsAdapter, load_chat_model
from yuxi.modules.models.providers.cache import ModelInfo
from yuxi.modules.models.utils import parse_assistant_message_body

REASONING = " First\nthen check. "
TOOL = {"type": "function", "function": {"name": "inspect_code", "parameters": {"type": "object", "properties": {}}}}


def make_model(monkeypatch, provider, field="reasoning_content", *, enabled=True, replay=True, answer=""):
    """以 SDK 的真实 JSON/SSE 解析覆盖模型工厂。"""
    requests = []

    def respond(request):
        """核对工具续答在 HTTP 边界上的正文与思考字段。"""
        body = json.loads(request.content)
        requests.append(body)
        resumed = body["messages"][-1]["role"] == "tool"
        if resumed:
            assistant = body["messages"][-2]
            expected_content = answer or None
            if answer and not replay:
                expected_content = [{"type": "text", "text": answer}]
            assert assistant["content"] == expected_content
            if enabled and replay:
                assert assistant["reasoning_content"] == REASONING
            else:
                assert "reasoning_content" not in assistant
                assert "reasoning" not in assistant
        base = {"id": "chat-test", "object": "chat.completion", "created": 1, "model": "test"}
        reasoning = {field: REASONING} if enabled and not resumed else {}
        tool = {"id": "call-test", "type": "function", "function": {"name": "inspect_code", "arguments": "{}"}}
        message = {"role": "assistant", "content": "OK" if resumed else answer, **reasoning}
        if not resumed:
            message["tool_calls"] = [tool]
        finish = "stop" if resumed else "tool_calls"
        if body.get("stream"):
            delta = {**message}
            if not resumed:
                delta["tool_calls"] = [{"index": 0, **tool}]
            events = [
                {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                {**base, "choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
            ]
            if reasoning:
                delta[field] = REASONING[8:]
                events.insert(
                    0,
                    {
                        **base,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "", field: REASONING[:8]},
                                "finish_reason": None,
                            }
                        ],
                    },
                )
            if not resumed:
                events.insert(
                    -2,
                    {
                        **base,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                )
                if answer:
                    delta["content"] = answer[:8]
                    events.insert(
                        -2,
                        {**base, "choices": [{"index": 0, "delta": {"content": answer[8:]}, "finish_reason": None}]},
                    )
            return httpx.Response(
                200,
                text="".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={**base, "choices": [{"index": 0, "message": message, "finish_reason": finish}]})

    model_id = "qwen3.8-flash" if provider in {"alibaba-cn", "alibaba"} else "test"
    info = ModelInfo(provider, model_id, "chat", "Test", "test-key", "https://example.com/v1", "openai")
    monkeypatch.setattr("yuxi.modules.models.chat.model_cache.get_model_info", lambda _: info)
    transport = httpx.MockTransport(respond)
    model = load_chat_model(
        info.spec,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    )
    return model, requests


@pytest.mark.parametrize("provider", ["siliconflow-cn", "opencode-go", "zhipuai-coding-plan", "deepseek", "alibaba-cn", "alibaba"])
@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("output_version", ["v0", "v1"])
async def test_provider_reasoning_and_tool_roundtrip(monkeypatch, provider, field, stream, output_version):
    """各供应商保留推理、工具 ID 与续答原文，异步和同步路径一致。"""
    model, requests = make_model(monkeypatch, provider, field)
    model.output_version = output_version
    bound = model.bind_tools([TOOL])
    for asynchronous in (False, True):
        inputs = [HumanMessage("Inspect this function.")]
        if stream:
            chunks = [chunk async for chunk in bound.astream(inputs)] if asynchronous else list(bound.stream(inputs))
            result = chunks[0]
            for chunk in chunks[1:]:
                result += chunk
            assert result.usage_metadata["total_tokens"] == 5
        else:
            result = await bound.ainvoke(inputs) if asynchronous else bound.invoke(inputs)
        assert "reasoning_content" not in result.additional_kwargs
        assert result.response_metadata["model_provider"] == "openai"
        assert result.response_metadata["output_version"] == "v1"
        assert isinstance(result.content, list)
        assert next(b for b in result.content_blocks if b["type"] == "reasoning")["reasoning"] == REASONING
        assert result.tool_calls[0]["id"] == "call-test"
        inputs.extend([result, ToolMessage("valid", tool_call_id="call-test")])
        answer = await bound.ainvoke(inputs) if asynchronous else bound.invoke(inputs)
        assert answer.text == "OK"
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("provider", "field"),
    [
        ("opencode-go", "reasoning_content"),
        ("deepseek", "reasoning_content"),
        ("alibaba-cn", "reasoning_content"),
        ("my-gateway", "reasoning_content"),
        ("my-gateway", "reasoning"),
    ],
)
async def test_real_v3_reasoning_projection_and_checkpoint(monkeypatch, provider, field):
    """真实 v3 事件保留正文、工具和思考，并以原有格式续答。"""
    text = "Inspect the code."
    model, _ = make_model(monkeypatch, provider, field, replay=provider != "my-gateway", answer=text)

    async def node(state):
        """执行真实模型流桥接。"""
        return {"messages": [await model.ainvoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "reasoning-test"}}
    run = await compiled.astream_events({"messages": [HumanMessage("Inspect code")]}, config, version="v3")
    adapter = OpenAIEventAdapter(run_id="r", turn_id="turn", thread_id="reasoning-test", worker_id="w")

    async def save(operation, role, key, item, content_index=None):
        """协议映射的独立存储边界；数据库语义由 integration 验证。"""
        return {
            **item,
            "id": key,
            "turn_id": "turn",
            "yuxi": {**item.get("yuxi", {}), "output_index": 0, "run_id": "r"},
        }

    monkeypatch.setattr(adapter, "_save", save)
    emitted = []
    async for event in run:
        if event["method"] == "messages":
            emitted.extend(await adapter.consume(event))
    assert "".join(e["delta"] for e in emitted if e["type"] == "yuxi.session.turn.reasoning.delta") == REASONING
    assert any(
        e["type"] == "agent.session.turn.item.added" and e["item"]["type"] == "function_call" and e["item"]["call_id"] == "call-test"
        for e in emitted
    )
    state = await compiled.aget_state(config)
    assistant = state.values["messages"][-1]
    assert parse_assistant_message_body(assistant.content) == {"content": text, "reasoning_content": REASONING}
    turn = [*state.values["messages"], ToolMessage("valid", tool_call_id="call-test")]
    assert (await model.bind_tools([TOOL]).ainvoke(turn)).text == "OK"


def test_non_reasoning_provider_is_unchanged(monkeypatch):
    """普通 OpenAI 请求不添加第三方字段。"""
    model, _ = make_model(monkeypatch, "openai", enabled=False)
    payload = model._get_request_payload([AIMessage("OK", additional_kwargs={"reasoning_content": "private"})])
    assert "reasoning_content" not in payload["messages"][0]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"reasoning_content": "preferred", "reasoning": "other"}, "preferred"),
        ({"reasoning_content": {"text": "invalid"}, "reasoning": "fallback"}, "fallback"),
        ({"reasoning": ["unsupported"], "reasoning_details": [{"text": "ignored"}]}, ""),
        ({"content": [{"type": "text", "text": "Answer"}], "reasoning_content": "ignored"}, ""),
        (
            {
                "content": [{"type": "reasoning", "reasoning": "native"}, {"type": "text", "text": "Answer"}],
                "reasoning_content": "ignored",
            },
            "native",
        ),
    ],
)
async def test_only_plain_string_reasoning_is_used_as_fallback(httpx_mock, stream, payload, expected):
    """复杂字段与结构化内容沿用 SDK，普通字符串仍可兜底。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    message = {"role": "assistant", "content": "Answer", **payload}
    if stream:
        frame = {"choices": [{"index": 0, "delta": message}]}
        httpx_mock.add_response(text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
        async with await model.astream_events("Test", version="v3") as run:
            result = await run.output
    else:
        httpx_mock.add_response(json={"choices": [{"message": message}]})
        result = await model.ainvoke("Test")
    assert result.text == "Answer"
    assert "".join(block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning") == expected


async def test_plain_stream_keeps_sdk_content(httpx_mock):
    """无思考字段的自定义供应商直接流式返回文本。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    frame = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Answer"}}]}
    httpx_mock.add_response(text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    chunks = [chunk async for chunk in model.astream("Test")]
    assert chunks[0].content == "Answer"


async def test_synthetic_thought_is_not_sent_to_responses(httpx_mock):
    """简单兜底不产生可回传的 Responses 原生思考项。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    httpx_mock.add_response(
        json={"choices": [{"message": {"role": "assistant", "content": "answer", "reasoning_content": "private thought"}}]}
    )
    reply = await model.ainvoke("First")
    assert any(block["type"] == "reasoning" for block in reply.content_blocks)
    native = AIMessage(
        content_blocks=[{"type": "reasoning", "reasoning": "native summary", "id": "rs_native"}],
        response_metadata={"output_version": "v1", "model_provider": "openai", "object": "response"},
    )
    responses = ChatCompletionsAdapter(model="gpt-5-pro", api_key="test-key")
    items = responses._get_request_payload([HumanMessage("First"), reply, native, HumanMessage("Next")])["input"]
    assert [item["id"] for item in items if item["type"] == "reasoning"] == ["rs_native"]
    assert [item["content"][0]["text"] for item in items if item.get("role") == "assistant"] == ["answer"]


@pytest.mark.parametrize("provider", ["siliconflow-cn", "opencode-go", "zhipuai-coding-plan", "deepseek", "alibaba-cn", "alibaba"])
async def test_missing_reasoning_still_completes_tool_roundtrip(monkeypatch, provider):
    """供应商没有输出推理时，不伪造字段、不妨碍工具及续答。"""
    model, _ = make_model(monkeypatch, provider, enabled=False)
    inputs = [HumanMessage("Inspect code")]
    chunks = [chunk async for chunk in model.bind_tools([TOOL]).astream(inputs)]
    result = chunks[0]
    for chunk in chunks[1:]:
        result += chunk
    assert not any(block["type"] == "reasoning" for block in result.content_blocks)
    assert result.tool_calls[0]["id"] == "call-test"
    inputs.extend([result, ToolMessage("valid", tool_call_id="call-test")])
    answer = await model.ainvoke(inputs)
    assert answer.text == "OK"
    assert not any(block["type"] == "reasoning" for block in answer.content_blocks)


@pytest.mark.parametrize(
    "content,expected",
    [
        (
            [
                None,
                "ignored",
                {"type": "text", "text": 42},
                {"type": "reasoning", "reasoning": None},
                {"type": "text", "text": " first "},
                {"type": "reasoning", "reasoning": " thought "},
                {"type": "text", "text": "second"},
                {"type": "reasoning", "reasoning": "next"},
                {"type": "tool_call", "name": "inspect_code"},
            ],
            {"content": " first second", "reasoning_content": " thought next"},
        ),
        ("stored answer", {"content": "stored answer", "reasoning_content": ""}),
    ],
)
def test_body_projection_is_block_canonical(content, expected):
    """展示正文只来自标准 text/reasoning 块；缺失或畸形块不影响正文。"""
    assert parse_assistant_message_body(content) == expected


def test_standard_blocks_encode_history_once(monkeypatch):
    """下一轮将标准文本和推理各编码一次，工具结构不泄漏到正文。"""
    model, _ = make_model(monkeypatch, "zhipuai-coding-plan")
    message = AIMessage(
        content_blocks=[
            {"type": "reasoning", "reasoning": REASONING},
            {"type": "text", "text": "answer"},
            {"type": "tool_call", "id": "call-test", "name": "inspect_code", "args": {}},
        ],
        response_metadata={"output_version": "v1"},
    )
    wire = model._get_request_payload([message])["messages"][0]
    assert wire["content"] == "answer"
    assert wire["reasoning_content"] == REASONING
    assert len(wire["tool_calls"]) == 1
    assert wire["tool_calls"][0]["id"] == "call-test"
