"""用真实 HTTP 验证模型停顿恢复与持续流输出的超时语义。"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from langchain.agents import create_agent
from langgraph.stream.transformers import CustomTransformer
from yuxi.modules.agents.runtime.middlewares.network_retry import NetworkRetryMiddleware
from yuxi.modules.models.chat import load_chat_model
from yuxi.modules.models.providers.cache import ModelInfo, model_cache

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


@asynccontextmanager
async def model_server(*, stall_first, partial_first=False):
    """首个请求故意停顿，成功请求持续发送总耗时超过读取超时的片段。"""
    requests = []
    tasks = set()

    async def serve(reader, writer):
        """消费真实请求字节，并保持失败请求等待到测试清理。"""
        task = asyncio.current_task()
        tasks.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            size = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:"))
            requests.append(json.loads(await reader.readexactly(size)))
            attempt = len(requests)
            if stall_first and attempt == 1 and not partial_first:
                await asyncio.Event().wait()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            await writer.drain()
            parts = ["discarded"] if partial_first and attempt == 1 else ["a", "b", "c", "d", "e"]
            for text in parts:
                chunk = {
                    "id": f"attempt-{attempt}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
                }
                writer.write(f"data: {json.dumps(chunk)}\n\n".encode())
                await writer.drain()
                # 有意产生间隔；总生成耗时大于 read=0.25，每段间隔小于它。
                await asyncio.sleep(0.08)
            if partial_first and attempt == 1:
                await asyncio.Event().wait()
            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            writer.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1", requests
    finally:
        server.close()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


@pytest.mark.parametrize("failure", ["none", "headers", "partial"])
async def test_real_http_timeout_retries_without_combining_attempts(monkeypatch, failure):
    """真实模型图在读取停顿后重试，持续流与失败片段保持独立。"""
    monkeypatch.setenv("RUNNING_IN_DOCKER", "false")
    async with model_server(stall_first=failure != "none", partial_first=failure == "partial") as (url, requests):
        info = ModelInfo(
            provider_id="replay",
            model_id="test",
            model_type="chat",
            display_name="test",
            provider_type="openai",
            api_key="test-key",
            base_url=url,
        )
        monkeypatch.setattr(model_cache, "get_model_info", lambda _: info)
        model = load_chat_model("replay:test", max_retries=0, timeout=httpx.Timeout(connect=1, write=1, read=0.25, pool=1))
        graph = create_agent(
            model=model, tools=[], middleware=[NetworkRetryMiddleware(network_initial_delay=0.01, network_budget_seconds=3)]
        )
        custom = []
        messages = []
        final = None
        async with asyncio.timeout(10):
            async with await graph.astream_events(
                {"messages": [{"role": "user", "content": "test"}]}, version="v3", transformers=[CustomTransformer]
            ) as stream:
                async for event in stream:
                    if event["method"] == "custom":
                        custom.append(event["params"]["data"])
                    elif event["method"] == "messages":
                        messages.append(event["params"]["data"])
                final = await stream.output()
        assert final["messages"][-1].text == "abcde"
        assert len(requests) == (1 if failure == "none" else 2), "SDK must not add hidden retries"
        assert len(custom) == (0 if failure == "none" else 1)
        if custom:
            assert custom[0] == {"type": "yuxi.model_retry", "attempt": 1, "delay_seconds": 0.01}
        if failure == "partial":
            failed_ids = {metadata["run_id"] for message, metadata in messages if message.get("delta", {}).get("text") == "discarded"}
            successful_ids = {metadata["run_id"] for message, metadata in messages if message.get("delta", {}).get("text") == "a"}
            assert failed_ids and successful_ids and failed_ids.isdisjoint(successful_ids)
        await model.root_async_client.close()
        model.root_client.close()
