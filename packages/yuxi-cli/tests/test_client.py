from __future__ import annotations

from yuxi_cli.client import YuxiClient
from yuxi_cli.config import Remote


def test_run_agent_eval_uses_invocation_endpoint(monkeypatch):
    client = YuxiClient(Remote(name="local", url="http://localhost:5173", api_key="yxkey_test"))
    calls: dict[str, object] = {}

    def fake_request(method, path, **kwargs):
        calls["method"] = method
        calls["path"] = path
        calls["kwargs"] = kwargs
        return {"status": "completed", "output": "final answer"}

    monkeypatch.setattr(client, "_request", fake_request)

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

    assert result == {"status": "completed", "output": "final answer"}
    assert calls["method"] == "POST"
    assert calls["path"] == "/agent-invocation/eval/runs"
    assert calls["kwargs"]["timeout"] == 123
