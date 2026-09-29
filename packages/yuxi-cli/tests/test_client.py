from __future__ import annotations

import httpx
import pytest

from yuxi_cli.client import ClientError, YuxiClient, _iter_sse_events
from yuxi_cli.config import Remote


def _patched_client(monkeypatch):
    client = YuxiClient(Remote(name="local", url="http://localhost:5173", api_key="yxkey_test"))
    calls: list[dict] = []

    def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        return {"ok": True, "method": method, "path": path}

    monkeypatch.setattr(client, "_request", fake_request)
    return client, calls


def test_run_agent_eval_uses_invocation_endpoint(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        result = client.run_agent_eval(
            query="2+2=?",
            agent_slug="default-chatbot",
            evaluation={"dataset_name": "dataset-1"},
            meta={"request_id": "req-1"},
            timeout_seconds=123,
        )
    finally:
        client.close()

    assert result["method"] == "POST"
    assert result["path"] == "/agent-invocation/eval/runs"
    call = calls[-1]
    assert call["timeout"] == 123
    assert call["json"]["query"] == "2+2=?"
    assert call["json"]["agent_slug"] == "default-chatbot"
    assert call["json"]["evaluation"] == {"dataset_name": "dataset-1"}
    assert call["json"]["meta"] == {"request_id": "req-1"}


def test_create_agent_chat_run_uses_channel_endpoint(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.create_agent_chat_run(
            message="你好",
            agent_slug="default-chatbot",
            thread_id="thread-1",
            request_id="request-1",
        )
    finally:
        client.close()

    call = calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/agent-invocation/channel/messages"
    assert call["json"] == {
        "channel": "cli",
        "account_id": "local",
        "chat_id": "cli",
        "agent_slug": "default-chatbot",
        "thread_id": "thread-1",
        "message_id": "request-1",
        "request_id": "request-1",
        "message": {"type": "text", "text": "你好"},
    }


def test_iter_sse_events_supports_multiline_data_and_ignores_heartbeat():
    lines = iter(
        [
            ": heartbeat",
            "",
            "id: 1-0",
            "event: messages",
            'data: {"payload":',
            'data: {"status":"loading"}}',
            "",
        ]
    )

    assert list(_iter_sse_events(lines)) == [
        {
            "id": "1-0",
            "event": "messages",
            "data": '{"payload":\n{"status":"loading"}}',
        }
    ]


def test_stream_agent_run_events_sends_auth_and_uses_compact_events():
    remote = Remote(name="local", url="http://localhost:5173", api_key="yxkey_test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/runs/run-1/events"
        assert request.url.params["verbose"] == "false"
        assert request.headers["Authorization"] == "Bearer yxkey_test"
        return httpx.Response(
            200,
            text='event: end\ndata: {"payload":{"status":"completed"}}\n\n',
            headers={"Content-Type": "text/event-stream"},
        )

    client = YuxiClient(remote)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        events = list(client.stream_agent_run_events("run-1"))
    finally:
        client.close()

    assert events == [{"event": "end", "data": '{"payload":{"status":"completed"}}'}]


def test_list_external_databases_uses_external_path(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.list_external_databases()
    finally:
        client.close()
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["path"] == "/v1/knowledge/databases/external"


def test_list_agents_uses_visible_agent_path(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.list_agents()
    finally:
        client.close()
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["path"] == "/agent"


def test_get_agent_uses_slug_path(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.get_agent("research-agent")
    finally:
        client.close()
    assert calls[-1]["method"] == "GET"
    assert calls[-1]["path"] == "/agent/research-agent"


def test_get_agent_keeps_slug_in_one_path_segment():
    remote = Remote(name="local", url="http://localhost:5173", api_key="yxkey_test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/api/agent/..%2Fsystem%2Finfo%3Ffull%3Dtrue"
        return httpx.Response(404, json={"detail": "智能体不存在"})

    client = YuxiClient(remote)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ClientError, match="智能体不存在"):
            client.get_agent("../system/info?full=true")
    finally:
        client.close()


def test_get_agent_preserves_server_not_found_response():
    remote = Remote(name="local", url="http://localhost:5173", api_key="yxkey_test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/hidden-agent"
        assert request.headers["Authorization"] == "Bearer yxkey_test"
        return httpx.Response(404, json={"detail": "智能体不存在"})

    client = YuxiClient(remote)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ClientError, match="智能体不存在") as exc_info:
            client.get_agent("hidden-agent")
    finally:
        client.close()

    assert exc_info.value.status_code == 404


def test_list_external_files_passes_query_params(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.list_external_files("kb_1", query="report", offset=10, limit=50, status="indexed")
    finally:
        client.close()
    call = calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/v1/knowledge/databases/external/kb_1/files"
    params = call["params"]
    assert params["query"] == "report"
    assert params["offset"] == 10
    assert params["limit"] == 50
    assert params["status"] == "indexed"


def test_retrieve_external_posts_json_body(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.retrieve_external("kb_1", query="hello", file_name="a.md", options={"final_top_k": 5})
    finally:
        client.close()
    call = calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/knowledge/databases/external/kb_1/retrieve"
    assert call["json"] == {"query": "hello", "file_name": "a.md", "options": {"final_top_k": 5}}


def test_open_external_file_passes_offset_limit(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.open_external_file("kb_1", "file_1", offset=20, limit=80)
    finally:
        client.close()
    call = calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/v1/knowledge/databases/external/kb_1/files/file_1/open"
    assert call["params"] == {"offset": 20, "limit": 80}


def test_find_external_file_posts_patterns(monkeypatch):
    client, calls = _patched_client(monkeypatch)
    try:
        client.find_external_file(
            "kb_1",
            "file_1",
            patterns=["foo", "bar"],
            use_regex=True,
            case_sensitive=True,
            max_windows=3,
            window_size=40,
        )
    finally:
        client.close()
    call = calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/knowledge/databases/external/kb_1/files/file_1/find"
    assert call["json"]["patterns"] == ["foo", "bar"]
    assert call["json"]["use_regex"] is True
    assert call["json"]["case_sensitive"] is True
    assert call["json"]["max_windows"] == 3
    assert call["json"]["window_size"] == 40
