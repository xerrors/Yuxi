from __future__ import annotations

import http.client
import io
import json
import threading

import pytest
import yuxi_cli.chat_web as chat_web_module
from rich.console import Console
from yuxi_cli.chat_web import ChatWebError, ChatWebServer, _browser_events, run_web_chat
from yuxi_cli.config import ConfigStore


class FakeChatClient:
    def __init__(self):
        self.calls = []

    def create_agent_chat_run(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run-1", "thread_id": "thread-1"}

    def stream_agent_run_events(self, run_id):
        assert run_id == "run-1"
        yield {
            "event": "messages",
            "data": json.dumps(
                {
                    "payload": {
                        "chunk": {
                            "status": "loading",
                            "stream_event": {
                                "type": "message_delta",
                                "message_id": "message-1",
                                "content": "你",
                            },
                        }
                    }
                }
            ),
        }
        yield {"event": "end", "data": json.dumps({"payload": {"status": "completed"}})}


class BlockingChatClient(FakeChatClient):
    def __init__(self):
        super().__init__()
        self.release_stream = threading.Event()

    def stream_agent_run_events(self, run_id):
        assert run_id == "run-1"
        yield {
            "event": "messages",
            "data": json.dumps(
                {
                    "thread_id": "thread-1",
                    "payload": {
                        "chunk": {
                            "stream_event": {
                                "type": "message_delta",
                                "content": "首包",
                            }
                        }
                    },
                }
            ),
        }
        self.release_stream.wait(timeout=5)
        yield {"event": "end", "data": json.dumps({"payload": {"status": "completed"}})}


class TruncatedChatClient(FakeChatClient):
    def stream_agent_run_events(self, run_id):
        assert run_id == "run-1"
        yield {
            "event": "messages",
            "data": json.dumps(
                {
                    "payload": {
                        "chunk": {
                            "stream_event": {
                                "type": "message_delta",
                                "content": "未完成",
                            }
                        }
                    }
                }
            ),
        }


class StateChatClient(FakeChatClient):
    def create_agent_chat_run(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "kind": "command",
            "command": "state",
            "thread_id": "thread-1",
            "state": {"agent_state": {"todos": []}},
        }

    def stream_agent_run_events(self, _run_id):
        raise AssertionError("state command must not open a Run stream")


class QueuedChatClient(FakeChatClient):
    def create_agent_chat_run(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "queued",
            "thread_id": "thread-1",
            "request_events_url": "/api/agent/requests/request-1/events",
        }

    def stream_agent_request_events(self, request_events_url):
        assert request_events_url == "/api/agent/requests/request-1/events"
        yield {
            "event": "queued",
            "data": json.dumps({"request_id": "request-1", "queue_position": 1}),
        }
        yield {
            "event": "run_created",
            "data": json.dumps(
                {
                    "request_id": "request-1",
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                }
            ),
        }


@pytest.mark.parametrize("stream_fails", [False, True])
def test_queued_request_recovers_run_after_event_stream_ends(stream_fails):
    """排队流漏掉派发事件时，从持久结果恢复绑定的 Run。"""

    class LostEventClient(FakeChatClient):
        def stream_agent_request_events(self, request_events_url):
            assert request_events_url == "/api/agent/requests/req-2/events"
            if stream_fails:
                raise chat_web_module.ClientError("SSE connection lost")
            yield {"event": "queued", "data": "{}"}

        def get_agent_request_result(self, request_id):
            assert request_id == "req-2"
            return {"status": "in_progress", "run_id": "run-2"}

    server = ChatWebServer(("127.0.0.1", 0), LostEventClient(), "agent-1", "secret")
    try:
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.server = server
        result = handler._wait_queued_run(
            {
                "request_id": "req-2",
                "request_events_url": "/api/agent/requests/req-2/events",
            }
        )
    finally:
        server.server_close()
    assert result["run_id"] == "run-2"


def test_queued_request_keeps_tracking_after_disconnect(monkeypatch):
    """流断开时持久状态仍排队，继续跟踪同一 request_id。"""

    class DelayedRunClient(FakeChatClient):
        checks = 0

        def stream_agent_request_events(self, request_events_url):
            assert request_events_url == "/api/agent/requests/req-3/events"
            return iter(())

        def get_agent_request_result(self, request_id):
            assert request_id == "req-3"
            self.checks += 1
            return (
                {"status": "queued", "run_id": None}
                if self.checks == 1
                else {"status": "in_progress", "run_id": "run-3"}
            )

    monkeypatch.setattr(chat_web_module.time, "sleep", lambda _: None)
    client = DelayedRunClient()
    server = ChatWebServer(("127.0.0.1", 0), client, "agent-1", "secret")
    try:
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.server = server
        result = handler._wait_queued_run(
            {
                "request_id": "req-3",
                "request_events_url": "/api/agent/requests/req-3/events",
            }
        )
    finally:
        server.server_close()
    assert client.checks == 2
    assert result["run_id"] == "run-3"


@pytest.mark.parametrize("status_code", [401, 403, 404])
def test_queued_request_stops_on_permanent_lookup_error(status_code):
    """永久查询错误不能使本地聊天一直等待。"""

    class MissingRequestClient(FakeChatClient):
        def stream_agent_request_events(self, _url):
            return iter(())

        def get_agent_request_result(self, _request_id):
            raise chat_web_module.ClientError("查询失败", status_code=status_code)

    server = ChatWebServer(
        ("127.0.0.1", 0), MissingRequestClient(), "agent-1", "secret"
    )
    try:
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.server = server
        with pytest.raises(chat_web_module.ClientError, match="查询失败"):
            handler._wait_queued_run(
                {
                    "request_id": "req-4",
                    "request_events_url": "/api/agent/requests/req-4/events",
                }
            )
    finally:
        server.server_close()


def test_queued_request_bounds_transient_lookup_failures(monkeypatch):
    """状态查询持续不可用时带着 Request ID 结束等待。"""

    class UnavailableClient(FakeChatClient):
        def stream_agent_request_events(self, _url):
            return iter(())

        def get_agent_request_result(self, _request_id):
            raise chat_web_module.ClientError("暂不可用", status_code=503)

    ticks = iter([1, 2, 62, 63])
    monkeypatch.setattr(chat_web_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(chat_web_module.time, "sleep", lambda _: None)
    server = ChatWebServer(("127.0.0.1", 0), UnavailableClient(), "agent-1", "secret")
    try:
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.server = server
        with pytest.raises(ChatWebError, match="request_id=req-4"):
            handler._wait_queued_run(
                {
                    "request_id": "req-4",
                    "request_events_url": "/api/agent/requests/req-4/events",
                }
            )
    finally:
        server.server_close()


class ApprovalChatClient(FakeChatClient):
    def stream_agent_run_events(self, run_id):
        assert run_id == "run-1"
        approval_chunk = {
            "status": "human_approval_required",
            "approval": {
                "action_requests": [{"name": "write_file"}],
                "review_configs": [{"allowed_decisions": ["approve", "reject"]}],
            },
        }
        yield {
            "event": "interrupt",
            "data": json.dumps({"payload": {"chunk": approval_chunk}}),
        }
        yield {
            "event": "end",
            "data": json.dumps(
                {"payload": {"status": "interrupted", "chunk": approval_chunk}}
            ),
        }


def test_browser_events_extracts_text_delta_and_terminal_status():
    client = FakeChatClient()

    assert list(_browser_events(client.stream_agent_run_events("run-1"))) == [
        {"type": "delta", "content": "你"},
        {"type": "done", "status": "completed"},
    ]


def test_browser_events_handles_retry_interrupt_and_child_thread():
    events = iter(
        [
            {
                "event": "error",
                "data": json.dumps({"payload": {"chunk": {"retryable": True}}}),
            },
            {
                "event": "messages",
                "data": json.dumps(
                    {
                        "thread_id": "child-thread",
                        "payload": {
                            "chunk": {
                                "stream_event": {
                                    "type": "message_delta",
                                    "content": "子线程",
                                }
                            }
                        },
                    }
                ),
            },
            {
                "event": "end",
                "data": json.dumps(
                    {"thread_id": "thread-1", "payload": {"status": "interrupted"}}
                ),
            },
        ]
    )

    assert list(_browser_events(events, thread_id="thread-1")) == [
        {"type": "error", "message": "运行结束：interrupted"},
        {"type": "done", "status": "interrupted"},
    ]


def test_browser_events_maps_tool_approval_interrupt_to_waiting_state():
    events = ApprovalChatClient().stream_agent_run_events("run-1")

    assert list(_browser_events(events, thread_id="thread-1")) == [
        {
            "type": "approval_required",
            "message": "等待工具审批，请输入 /approve 继续",
        },
        {"type": "done", "status": "waiting_approval"},
    ]


def test_browser_events_rejects_eof_without_terminal_event():
    events = TruncatedChatClient().stream_agent_run_events("run-1")

    with pytest.raises(ChatWebError, match="终态前断开"):
        list(_browser_events(events))


def test_local_server_streams_chat_without_exposing_api_key():
    client = FakeChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)

    try:
        connection.request("GET", "/")
        page_response = connection.getresponse()
        page = page_response.read().decode()
        assert page_response.status == 200
        assert "session-secret" in page
        assert "yxkey_" not in page

        body = json.dumps({"message": "你好", "thread_id": None})
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode())),
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        stream_response = connection.getresponse()
        events = [
            json.loads(line) for line in stream_response.read().decode().splitlines()
        ]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert stream_response.status == 200
    assert events == [
        {"type": "meta", "run_id": "run-1", "thread_id": "thread-1"},
        {"type": "delta", "content": "你"},
        {"type": "done", "status": "completed"},
    ]
    assert client.calls[0]["message"] == "你好"


def test_local_server_returns_state_command_without_run_stream():
    client = StateChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "/state", "thread_id": "thread-1"})

    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode())),
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert events == [
        {"type": "meta", "thread_id": "thread-1"},
        {
            "type": "command",
            "command": "state",
            "result": {"agent_state": {"todos": []}},
        },
        {"type": "done", "status": "completed"},
    ]


def test_local_server_waits_queued_request_before_run_stream():
    client = QueuedChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "排队消息", "thread_id": "thread-1"})

    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode())),
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert events == [
        {"type": "meta", "run_id": "run-1", "thread_id": "thread-1"},
        {"type": "delta", "content": "你"},
        {"type": "done", "status": "completed"},
    ]


def test_local_server_returns_approval_hint_without_error():
    client = ApprovalChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "执行敏感操作", "thread_id": "thread-1"})

    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode())),
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert events == [
        {"type": "meta", "run_id": "run-1", "thread_id": "thread-1"},
        {
            "type": "approval_required",
            "message": "等待工具审批，请输入 /approve 继续",
        },
        {"type": "done", "status": "waiting_approval"},
    ]


def test_local_server_flushes_delta_before_remote_stream_ends():
    client = BlockingChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "流式测试", "thread_id": None})

    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        meta = json.loads(response.readline())
        first_delta = json.loads(response.readline())

        assert response.status == 200
        assert meta["type"] == "meta"
        assert first_delta == {"type": "delta", "content": "首包"}
        assert client.release_stream.is_set() is False

        client.release_stream.set()
        remaining = [json.loads(line) for line in response.read().decode().splitlines()]
        assert remaining == [{"type": "done", "status": "completed"}]
    finally:
        client.release_stream.set()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_server_reports_truncated_remote_stream():
    client = TruncatedChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "截断测试", "thread_id": None})

    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 200
    assert events[-2:] == [
        {"type": "delta", "content": "未完成"},
        {"type": "error", "message": "运行事件流在终态前断开，请重试"},
    ]


def test_local_server_restores_final_output_after_run_stream_disconnect():
    """Run SSE 丢失终态时，以同一 Request 的持久输出替换部分文本。"""

    class RecoveringClient(TruncatedChatClient):
        def create_agent_chat_run(self, **kwargs):
            return {
                "run_id": "run-1",
                "thread_id": "thread-1",
                "request_id": "req-1",
                "result_url": "/api/agent/request-result?request_id=req-1",
            }

        def get_agent_request_result(self, request_id):
            assert request_id == "req-1"
            return {"status": "completed", "run_id": "run-1", "output": "完整回答"}

    server = ChatWebServer(
        ("127.0.0.1", 0), RecoveringClient(), "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    body = json.dumps({"message": "截断测试", "thread_id": None})
    try:
        connection.request(
            "POST",
            "/api/chat",
            body=body,
            headers={"X-Yuxi-Chat-Token": "session-secret"},
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status == 200
    assert events[-3:] == [
        {"type": "delta", "content": "未完成"},
        {"type": "snapshot", "content": "完整回答"},
        {"type": "done", "status": "completed"},
    ]


def test_local_server_reports_permanent_result_lookup_error():
    """Run 断流后若认证失效，应向浏览器返回错误并结束请求。"""

    class UnauthorizedClient(TruncatedChatClient):
        def create_agent_chat_run(self, **kwargs):
            return {
                "run_id": "run-1",
                "thread_id": "thread-1",
                "request_id": "req-1",
                "result_url": "/api/agent/request-result?request_id=req-1",
            }

        def get_agent_request_result(self, request_id):
            assert request_id == "req-1"
            raise chat_web_module.ClientError("认证已失效", status_code=401)

    server = ChatWebServer(
        ("127.0.0.1", 0), UnauthorizedClient(), "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/chat",
            body=json.dumps({"message": "测试", "thread_id": None}),
            headers={"X-Yuxi-Chat-Token": "session-secret"},
        )
        response = connection.getresponse()
        events = [json.loads(line) for line in response.read().decode().splitlines()]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status == 200
    assert events[-1] == {"type": "error", "message": "认证已失效"}


@pytest.mark.parametrize(
    ("headers", "expected_error"),
    [
        ({"Origin": "http://evil.example"}, "请求来源无效"),
        ({"X-Yuxi-Chat-Token": "wrong-token"}, "会话令牌无效"),
    ],
)
def test_local_server_rejects_untrusted_requests(headers, expected_error):
    client = FakeChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    request_headers = {
        "Origin": server.origin,
        "X-Yuxi-Chat-Token": "session-secret",
        **headers,
    }

    try:
        connection.request("POST", "/api/chat", body="{}", headers=request_headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 403
    assert payload == {"error": expected_error}
    assert client.calls == []


def test_local_server_rejects_invalid_json():
    client = FakeChatClient()
    server = ChatWebServer(
        ("127.0.0.1", 0), client, "default-chatbot", "session-secret"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)

    try:
        connection.request(
            "POST",
            "/api/chat",
            body="not-json",
            headers={
                "Origin": server.origin,
                "X-Yuxi-Chat-Token": "session-secret",
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 400
    assert payload["error"]
    assert client.calls == []


def test_run_web_chat_requires_login(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")

    with pytest.raises(ChatWebError, match="尚未登录"):
        run_web_chat(store, None, "default-chatbot", console=None, no_open=True)


def test_run_web_chat_opens_browser_and_closes_resources(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    config.get_remote("local").api_key = "yxkey_test"
    store.save(config)
    opened_urls = []
    fake_clients = []
    fake_servers = []

    class FakeClient:
        def __init__(self, remote):
            self.remote = remote
            self.closed = False
            fake_clients.append(self)

        def close(self):
            self.closed = True

    class FakeServer:
        origin = "http://127.0.0.1:43210"

        def __init__(self, address, client, agent_slug, session_token):
            assert address == ("127.0.0.1", 0)
            assert agent_slug == "default-chatbot"
            assert session_token
            self.client = client
            self.closed = False
            fake_servers.append(self)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(chat_web_module, "YuxiClient", FakeClient)
    monkeypatch.setattr(chat_web_module, "ChatWebServer", FakeServer)
    console = Console(file=io.StringIO(), force_terminal=False)

    run_web_chat(
        store,
        "local",
        "default-chatbot",
        console,
        open_browser=lambda url: opened_urls.append(url) or True,
    )

    assert opened_urls == ["http://127.0.0.1:43210"]
    assert fake_servers[0].closed is True
    assert fake_clients[0].closed is True
