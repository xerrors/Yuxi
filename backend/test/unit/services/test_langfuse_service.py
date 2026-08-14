from __future__ import annotations

import pytest

from yuxi.services import langfuse_service as svc


class _FakeLangfuseClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.scores = []
        self.flush_count = 0
        self.raise_on_score = False
        self.__class__.instances.append(self)

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return f"trace-{seed}"

    def create_score(self, **kwargs) -> None:
        if self.raise_on_score:
            raise RuntimeError("score failed")
        self.scores.append(kwargs)

    def get_trace_url(self, *, trace_id: str | None = None) -> str | None:
        if trace_id is None:
            return None
        return f"https://langfuse.local/trace/{trace_id}"

    def flush(self) -> None:
        self.flush_count += 1


class _FakeCallbackHandler:
    def __init__(self, *, public_key=None, trace_context=None):
        self.public_key = public_key
        self.trace_context = trace_context
        self.last_trace_id = None


@pytest.fixture
def run_context_with_last_trace(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(svc, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    svc.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        request_id="req-1",
        operation="agent_chat_stream",
    )
    run_context.callbacks[0].last_trace_id = "trace-runtime"
    return run_context


def test_build_run_context_includes_trace_metadata(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.example")
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setattr(svc, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    svc.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        request_id="req-1",
        operation="agent_chat_stream",
        backend_id="ChatbotAgent",
        message_type="text",
        username="alice",
        login_user_id="alice-login",
        department_id=7,
    )

    assert run_context.trace_id == "trace-req-1"
    assert len(run_context.callbacks) == 1
    assert run_context.callbacks[0].trace_context == {"trace_id": "trace-req-1"}
    assert run_context.metadata["langfuse_user_id"] == "user-1"
    assert run_context.metadata["langfuse_session_id"] == "thread-1"
    assert run_context.metadata["backend_id"] == "ChatbotAgent"
    assert run_context.metadata["department_id"] == "7"
    assert run_context.tags == [
        "yuxi",
        "chat",
        "agent_chat_stream",
        "agent:agent-a",
        "message_type:text",
    ]


def test_build_run_context_merges_evaluation_metadata_and_tags(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    svc.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        request_id="req-1",
        operation="agent_chat_stream",
        extra_metadata={
            "source": "agent_evaluation",
            "feature": "agent_evaluation",
            "evaluation": {"dataset_name": "agent-eval-smoke"},
        },
        extra_tags=["agent_evaluation", "dataset:agent-eval-smoke", "agent_evaluation"],
    )

    assert run_context.metadata["source"] == "agent_evaluation"
    assert run_context.metadata["feature"] == "agent_evaluation"
    assert run_context.metadata["evaluation"] == {"dataset_name": "agent-eval-smoke"}
    assert run_context.tags == [
        "yuxi",
        "chat",
        "agent_chat_stream",
        "agent:agent-a",
        "agent_evaluation",
        "dataset:agent-eval-smoke",
    ]


def test_get_trace_info_prefers_handler_last_trace_id(run_context_with_last_trace):
    trace_info = svc.get_trace_info(run_context_with_last_trace)

    assert trace_info == {
        "langfuse_trace_id": "trace-runtime",
        "langfuse_user_id": "user-1",
        "langfuse_session_id": "thread-1",
    }


async def test_get_trace_url_async_returns_trace_url(run_context_with_last_trace):
    trace_url = await svc.get_trace_url_async(run_context_with_last_trace)

    assert trace_url == "https://langfuse.local/trace/trace-runtime"


def test_submit_user_feedback_score_creates_boolean_score(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(svc, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    svc.get_langfuse_client.cache_clear()

    created = svc.submit_user_feedback_score(
        trace_id="trace-1",
        feedback_id=12,
        message_id=34,
        conversation_id=56,
        uid="user-1",
        rating="dislike",
        reason="答案不准确",
    )

    client = _FakeLangfuseClient.instances[-1]
    assert created is True
    assert client.scores == [
        {
            "trace_id": "trace-1",
            "score_id": "yuxi-message-feedback-12",
            "name": "user-feedback",
            "value": 0,
            "data_type": "BOOLEAN",
            "comment": "答案不准确",
            "metadata": {
                "source": "yuxi",
                "feedback_id": 12,
                "message_id": 34,
                "conversation_id": 56,
                "uid": "user-1",
                "rating": "dislike",
            },
        }
    ]
    assert client.flush_count == 1


def test_submit_user_feedback_score_returns_false_when_langfuse_fails(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(svc, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    svc.get_langfuse_client.cache_clear()
    client = svc.get_langfuse_client()
    client.raise_on_score = True

    created = svc.submit_user_feedback_score(
        trace_id="trace-1",
        feedback_id=12,
        message_id=34,
        conversation_id=56,
        uid="user-1",
        rating="like",
    )

    assert created is False
    assert client.flush_count == 0
