from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import feedback_service as svc


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        return _FakeResult(self.results.pop(0))

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        item.id = 9
        item.created_at = datetime(2026, 1, 2, 3, 4, 5)

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_submit_message_feedback_syncs_langfuse_score(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        role="assistant",
        message_type="text",
        extra_metadata={"langfuse_trace_id": "trace-1"},
    )
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="like",
        reason=None,
        db=db,
        current_uid="user-1",
    )

    assert result == {
        "id": 9,
        "message_id": 3,
        "rating": "like",
        "reason": None,
        "created_at": "2026-01-02T03:04:05",
    }
    assert db.committed is True
    assert db.rolled_back is False
    assert calls == [
        {
            "trace_id": "trace-1",
            "feedback_id": 9,
            "message_id": 3,
            "conversation_id": 7,
            "uid": "user-1",
            "rating": "like",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", ["text", None])
async def test_submit_message_feedback_skips_langfuse_without_trace_id(monkeypatch: pytest.MonkeyPatch, message_type):
    message = SimpleNamespace(id=3, conversation_id=7, role="assistant", message_type=message_type, extra_metadata={})
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="dislike",
        reason="不相关",
        db=db,
        current_uid="user-1",
    )

    assert result["rating"] == "dislike"
    assert result["reason"] == "不相关"
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].message_id == 3
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "message_type"),
    [
        ("user", "text"),
        ("system", "text"),
        ("tool", "text"),
        ("assistant", "model_audit"),
        ("assistant", "tool_audit"),
    ],
)
async def test_submit_message_feedback_rejects_invalid_target_without_side_effects(monkeypatch, role, message_type):
    """非助手与审计消息在写入、上传评分前被拒绝。"""
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        role=role,
        message_type=message_type,
        extra_metadata={"langfuse_trace_id": "trace-invalid"},
    )
    db = _FakeSession([message, SimpleNamespace(id=7, uid="user-1")])
    calls = []
    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(HTTPException) as exc:
        await svc.submit_message_feedback_view(message_id=3, rating="like", reason=None, db=db, current_uid="user-1")

    assert exc.value.status_code == 422
    assert exc.value.detail == "Feedback is only supported for non-audit assistant messages"
    assert db.added == []
    assert db.committed is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "status", "detail"),
    [
        ("missing", 404, "Message not found"),
        ("foreign", 403, "Access denied"),
        ("missing_conversation", 403, "Access denied"),
        ("duplicate", 409, "Feedback already submitted for this message"),
    ],
)
async def test_submit_message_feedback_preserves_existing_errors(monkeypatch, case, status, detail):
    """保留缺失、所有权与重复反馈错误，所有权检查优先于目标检查。"""
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        role="assistant" if case == "duplicate" else "user",
        message_type="text",
        extra_metadata={"langfuse_trace_id": "trace-1"},
    )
    if case == "missing":
        results = [None]
    elif case == "foreign":
        results = [message, SimpleNamespace(id=7, uid="other-user")]
    elif case == "missing_conversation":
        results = [message, None]
    else:
        results = [message, SimpleNamespace(id=7, uid="user-1"), SimpleNamespace(id=9)]
    db = _FakeSession(results)
    calls = []
    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(HTTPException) as exc:
        await svc.submit_message_feedback_view(message_id=3, rating="like", reason=None, db=db, current_uid="user-1")

    assert exc.value.status_code == status
    assert exc.value.detail == detail
    assert db.added == []
    assert db.committed is False
    assert calls == []
