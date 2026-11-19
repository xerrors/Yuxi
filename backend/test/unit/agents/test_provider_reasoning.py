"""使用真实 SDK 与 LangGraph 验证供应商推理协议边界。"""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict
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
            assert assistant["content"] == (answer or None)
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


@pytest.mark.parametrize("provider", ["opencode-go", "deepseek", "alibaba-cn", "my-gateway"])
async def test_real_v3_reasoning_projection_and_checkpoint(monkeypatch, provider):
    """真实 v3 事件经服务层投影后与 checkpoint 的历史推理一致。"""
    model, _ = make_model(monkeypatch, provider)

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
    persisted = state.values["messages"][-1].model_dump()
    assert parse_assistant_message_body(persisted["content"])["reasoning_content"] == REASONING


def test_non_reasoning_provider_is_unchanged(monkeypatch):
    """普通 OpenAI 请求不添加第三方字段。"""
    model, _ = make_model(monkeypatch, "openai", enabled=False)
    payload = model._get_request_payload([AIMessage("OK", additional_kwargs={"reasoning_content": "private"})])
    assert "reasoning_content" not in payload["messages"][0]


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
@pytest.mark.parametrize("stream", [False, True])
async def test_new_provider_receives_reasoning_without_replaying_it(monkeypatch, field, stream):
    """自定义供应商的思考与正文分别合并，续答只发原有字段。"""
    text = "Inspect the code."
    model, _ = make_model(monkeypatch, "my-gateway", field, replay=False, answer=text)
    bound = model.bind_tools([TOOL])
    inputs = [HumanMessage("Inspect code")]
    if stream:
        chunks = [chunk async for chunk in bound.astream(inputs)]
        result = chunks[0]
        for chunk in chunks[1:]:
            result += chunk
        assert result.usage_metadata["total_tokens"] == 5
    else:
        result = await bound.ainvoke(inputs)
    assert parse_assistant_message_body(result.content) == {"content": text, "reasoning_content": REASONING}
    assert result.tool_calls[0]["id"] == "call-test"
    inputs.extend([result, ToolMessage("valid", tool_call_id="call-test")])
    assert (await bound.ainvoke(inputs)).text == "OK"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"reasoning_content": "preferred", "reasoning": "other"}, "preferred"),
        ({"reasoning_content": {"text": "invalid"}, "reasoning": "fallback"}, "fallback"),
        ({"reasoning_content": "", "reasoning": ["unsupported"], "reasoning_details": [{"text": "ignored"}]}, ""),
        (
            {
                "content": [{"type": "reasoning", "reasoning": ""}, {"type": "text", "text": "Answer"}],
                "reasoning_content": "fallback",
            },
            "fallback",
        ),
        (
            {
                "content": [
                    {"type": "reasoning", "reasoning": "standard", "index": 0},
                    {"type": "text", "text": "Answer", "index": 1},
                ],
                "reasoning_content": "duplicate",
            },
            "standard",
        ),
    ],
)
async def test_reasoning_fallback_priority_and_types(httpx_mock, stream, payload, expected):
    """标准块优先，扩展字段只接受有文本的字符串。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    message = {"role": "assistant", "content": "Answer", **payload}
    if stream:
        chunk = {"choices": [{"index": 0, "delta": message, "finish_reason": "stop"}]}
        httpx_mock.add_response(
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
        result = [part async for part in model.astream("Test")][0]
    else:
        httpx_mock.add_response(json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})
        result = await model.ainvoke("Test")
    assert result.text == "Answer"
    assert "".join(block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning") == expected


@pytest.mark.parametrize("standard_first", [True, False])
@pytest.mark.parametrize("entrypoint", ["stream", "astream", "v3"])
@pytest.mark.parametrize("indexed", [True, False])
async def test_mixed_stream_keeps_one_reasoning_source_and_text_order(httpx_mock, standard_first, entrypoint, indexed):
    """首个有效思考来源贯穿请求，混合文本的顺序保持一致。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    standard = {"content": [{"type": "reasoning", "reasoning": "standard", "index": 0}]}
    fallback = {"reasoning_content": "fallback"}
    thinking = [standard, fallback] if standard_first else [fallback, standard]
    text_block = {"type": "text", "index": 1} if indexed else {"type": "text"}
    deltas = [
        {"role": "assistant", **thinking[0]},
        {"content": "A"},
        {**thinking[1], "content": [*thinking[1].get("content", []), {**text_block, "text": "B"}]},
        {"content": [{"type": "reasoning", "reasoning": " continue", "index": 0}], "reasoning_content": " continue"},
        {"content": "C"},
        {"content": [{**text_block, "text": "D"}]},
    ]
    chunks = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
    httpx_mock.add_response(
        text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )
    if entrypoint == "v3":
        async with await model.astream_events("Test", version="v3") as run:
            result = await run.output
    else:
        results = [part async for part in model.astream("Test")] if entrypoint == "astream" else list(model.stream("Test"))
        result = results[0]
        for part in results[1:]:
            result += part
    assert result.text == "ABCD"
    assert [block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning"] == [
        "standard continue" if standard_first else "fallback continue"
    ]


def test_interleaved_streams_keep_independent_reasoning_sources(httpx_mock):
    """交错消费同一模型的两条流时，仅使用各自已选的思考来源。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    native = {"content": [{"type": "reasoning", "reasoning": "native", "index": 0}]}
    fallback = {"reasoning_content": "fallback"}
    for first in (native, fallback):
        deltas = [{"role": "assistant", **first}, {**native, **fallback}]
        chunks = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
        httpx_mock.add_response(
            text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    native_stream, fallback_stream = model.stream("Native"), model.stream("Fallback")
    native_first, fallback_first = next(native_stream), next(fallback_stream)
    native_result = native_first + next(native_stream)
    fallback_result = fallback_first + next(fallback_stream)
    assert [block["reasoning"] for block in native_result.content_blocks] == ["nativenative"]
    assert [block["reasoning"] for block in fallback_result.content_blocks] == ["fallbackfallback"]
    list(native_stream)
    list(fallback_stream)


@pytest.mark.parametrize(
    ("entrypoint", "same_frame"),
    [("invoke", False), ("astream", True), ("astream", False), ("v3", True), ("v3", False)],
)
async def test_structured_reasoning_and_openai_tools_roundtrip(httpx_mock, entrypoint, same_frame):
    """标准思考与通常的工具调用独立累积，续答逐字回传供应商字段。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1", replay_reasoning=True)
    bound = model.bind_tools([TOOL])
    standard = [{"type": "reasoning", "reasoning": "visible thought", "index": 0}]
    tool = {"id": "call-test", "type": "function", "function": {"name": "inspect_code", "arguments": "{}"}}
    if entrypoint == "invoke":
        message = {"role": "assistant", "content": standard, "reasoning_content": REASONING, "tool_calls": [tool]}
        httpx_mock.add_response(json={"choices": [{"message": message, "finish_reason": "tool_calls"}]})
    else:
        first = {"role": "assistant", "content": standard, "reasoning_content": REASONING[:8]}
        tool_delta = {"tool_calls": [{"index": 0, **tool}]}
        last = {"reasoning_content": REASONING[8:]}
        deltas = [{**first, **tool_delta}, last] if same_frame else [first, {**last, **tool_delta}]
        events = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
        httpx_mock.add_response(
            text="".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
    httpx_mock.add_response(json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    inputs = [HumanMessage("Inspect code")]
    if entrypoint == "v3":
        async with await model.astream_events(inputs, version="v3") as run:
            result = await run.output
    elif entrypoint == "astream":
        chunks = [part async for part in bound.astream(inputs)]
        result = chunks[0]
        for part in chunks[1:]:
            result += part
    else:
        result = await bound.ainvoke(inputs)
    assert [(call["name"], call["id"]) for call in result.tool_calls] == [("inspect_code", "call-test")]
    assert [block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning"] == ["visible thought"]
    inputs.extend([result, ToolMessage("valid", tool_call_id="call-test")])
    assert (await bound.ainvoke(inputs)).text == "OK"
    assistant = json.loads(httpx_mock.get_requests()[-1].content)["messages"][-2]
    assert assistant["reasoning_content"] == REASONING
    assert assistant["tool_calls"] == [tool]


@pytest.mark.parametrize("structured", [None, [{"type": "text", "text": "One"}, {"type": "text", "text": "Two"}]])
async def test_new_provider_retains_assistant_text_format_in_followup(httpx_mock, structured):
    """带兼容思考的空正文与结构化正文保留原本的请求格式。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    httpx_mock.add_response(json={"choices": [{"message": {"role": "assistant", "content": structured, "reasoning_content": "separate"}}]})
    httpx_mock.add_response(json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    first = await model.ainvoke("Test")
    assert [block["reasoning"] for block in first.content_blocks if block["type"] == "reasoning"] == ["separate"]
    assert (await model.ainvoke([HumanMessage("Test"), first, HumanMessage("Next")])).text == "OK"
    assistant = json.loads(httpx_mock.get_requests()[-1].content)["messages"][1]
    assert assistant == {"role": "assistant", "content": structured if structured is not None else ""}


@pytest.mark.parametrize("thinking", [{}, {"reasoning_content": "thought"}])
async def test_new_provider_retains_v3_text_array_in_followup(httpx_mock, thinking):
    """v3 历史按既有的文本块数组回放，独立思考不进入请求。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    deltas = [{"role": "assistant", "content": "A", **thinking}, {"content": "B"}]
    frames = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
    httpx_mock.add_response(
        text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )
    async with await model.astream_events("First", version="v3") as run:
        reply = await run.output
    history = messages_from_dict(messages_to_dict([HumanMessage("First"), reply, HumanMessage("Next")]))
    httpx_mock.add_response(json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    assert (await model.ainvoke(history)).text == "OK"
    assistant = json.loads(httpx_mock.get_requests()[-1].content)["messages"][1]
    assert assistant == {"role": "assistant", "content": [{"type": "text", "text": "AB"}]}


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "first answer", "reasoning_content": "display only"},
        {
            "content": [
                {"type": "reasoning", "reasoning": "display only", "id": "vendor-thought-1"},
                {"type": "text", "text": "first answer", "id": "vendor-answer-1"},
            ]
        },
        {
            "content": [
                {
                    "type": "non_standard",
                    "value": {"type": "reasoning", "id": "rs_wrapped_foreign", "summary": []},
                },
                {"type": "non_standard", "value": {"type": "text", "text": "first answer", "id": "vendor-answer-1"}},
            ]
        },
    ],
)
@pytest.mark.parametrize("legacy_reasoning", ["old text", {"id": "rs_legacy_extension", "summary": []}])
async def test_switch_to_responses_preserves_native_reasoning_items(httpx_mock, payload, legacy_reasoning):
    """切换模型后，Responses 只接收拥有原生 ID 的思考项及原正文。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    httpx_mock.add_response(
        json={
            "id": "resp_vendor_request",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "id": "msg_vendor", **payload}, "finish_reason": "stop"}],
        }
    )
    first = HumanMessage("First")
    reply = await model.ainvoke([first])
    responses = ChatCompletionsAdapter(model="gpt-5-pro", api_key="test-key", base_url="https://example.com/v1")
    httpx_mock.add_response(
        url="https://example.com/v1/responses",
        json={
            "id": "resp_native",
            "object": "response",
            "model": "gpt-5-pro",
            "created_at": 1,
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_native", "summary": [{"type": "summary_text", "text": "native summary"}]},
                {
                    "type": "message",
                    "id": "msg_native",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "native answer", "annotations": []}],
                },
            ],
        },
    )
    native = await responses.ainvoke("Native turn")
    legacy = AIMessage(
        id="msg_foreign_outer",
        content_blocks=[
            {"type": "reasoning", "reasoning": "old thought", "id": "rs_foreign_legacy"},
            {"type": "text", "text": "legacy answer", "id": "vendor-answer-old"},
        ],
        additional_kwargs={
            "reasoning": legacy_reasoning,
            "tool_outputs": [{"type": "reasoning", "id": "rs_legacy_tool", "summary": []}],
        },
        response_metadata={"model_provider": "openai", "output_version": "v1"},
    )
    native_legacy = AIMessage(
        id="msg_native_legacy",
        content=[{"type": "text", "text": "native legacy answer"}],
        additional_kwargs={"reasoning": {"id": "rs_native_legacy", "summary": []}},
        response_metadata={"id": "resp_native_legacy"},
    )
    history = messages_from_dict(
        messages_to_dict([first, reply, HumanMessage("Second"), native, HumanMessage("Third"), legacy, native_legacy])
    )
    items = responses._get_request_payload(history)["input"]
    assert [item.get("id") for item in items if item["type"] == "reasoning"] == ["rs_native", "rs_native_legacy"]
    assert [item["content"][0]["text"] for item in items if item.get("role") == "assistant"] == [
        "first answer",
        "native answer",
        "legacy answer",
        "native legacy answer",
    ]
    assert not any(item.get("id") in {"vendor-answer-1", "vendor-answer-old", "msg_foreign_outer"} for item in items)
    assert history[1] == reply
    resumed = ChatCompletionsAdapter(model="gpt-5-pro", api_key="test-key", use_previous_response_id=True)
    resume_payload = resumed._get_request_payload([native, first, reply, HumanMessage("Next")])
    assert resume_payload["previous_response_id"] == "resp_native"
    assert [item["content"][0]["text"] for item in resume_payload["input"] if item.get("role") == "assistant"] == ["first answer"]


@pytest.mark.parametrize("stream", [False, True])
async def test_chat_completions_preserves_refusal_content_in_followup(httpx_mock, stream):
    """适配器保留供应商正文中的合法 refusal 部分。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    refusal = {"type": "refusal", "refusal": "Cannot do this"}
    assistant = {"role": "assistant", "content": [refusal], "reasoning_content": "separate"}
    if stream:
        frame = {"choices": [{"index": 0, "delta": assistant}]}
        httpx_mock.add_response(text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
        async with await model.astream_events("First", version="v3") as run:
            reply = await run.output
    else:
        httpx_mock.add_response(json={"choices": [{"message": assistant}]})
        reply = await model.ainvoke("First")
    httpx_mock.add_response(json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    assert (await model.ainvoke([HumanMessage("First"), reply, HumanMessage("Next")])).text == "OK"
    messages = json.loads(httpx_mock.get_requests()[-1].content)["messages"]
    assert messages[1] == {"role": "assistant", "content": [refusal]}


@pytest.mark.parametrize("parts", [["A", "B", "CD"], ["AB", "CD"]])
async def test_v3_unindexed_text_order_is_independent_of_reasoning_deltas(httpx_mock, parts):
    """无索引的文本块按到达顺序合并，思考增量不会改变文本位置。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    deltas = [{"content": [{"type": "text", "text": text} for text in part]} for part in parts]
    deltas[0].update(role="assistant", reasoning_content="think")
    deltas[-1]["reasoning_content"] = " again"
    frames = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
    httpx_mock.add_response(
        text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )
    async with await model.astream_events("Test", version="v3") as run:
        result = await run.output
    assert result.text == "ABCD"
    assert [block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning"] == ["think again"]


@pytest.mark.parametrize("prefix", [None, 3, ""])
async def test_v3_ignores_unusable_standard_reasoning_before_fallback(httpx_mock, prefix):
    """首帧的空值或错误类型不阻止后续思考和正文进入 v3。"""
    model = ChatCompletionsAdapter(model="test", api_key="test-key", base_url="https://example.com/v1")
    deltas = [
        {"role": "assistant", "content": [{"type": "reasoning", "reasoning": prefix, "index": 0}]},
        {"reasoning_content": "usable"},
        {"content": "OK"},
    ]
    chunks = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
    httpx_mock.add_response(
        text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )
    async with await model.astream_events("Test", version="v3") as run:
        result = await run.output
    assert [block["reasoning"] for block in result.content_blocks if block["type"] == "reasoning"] == ["usable"]
    assert result.text == "OK"


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
