"""Public API 确定性 ID 的历史重放边界。"""

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services.agents import public_api
from server.routers.public_v1.agents import InputMessage, _ordered_message_content
from yuxi.services import agent_request_service
from yuxi.services.input_message_service import build_chat_input_message_from_openai_content
from yuxi.utils.hash_utils import hash_id
from yuxi.utils.sse_utils import format_sse


@pytest.mark.asyncio
async def test_dispatched_request_stream_announces_run_before_replaying_events(monkeypatch):
    """订阅前已派发时仍须先通知产品退出排队态。"""

    @asynccontextmanager
    async def session():
        """提供一次持久 Request 读取。"""
        yield object()

    class RequestRepository:
        """模拟订阅前已派发的请求。"""

        def __init__(self, _db):
            pass

        async def get_by_request_id(self, _request_id):
            """返回明确关联的 Run。"""
            return SimpleNamespace(dispatched_run_id="run-1")

    async def run_events(**_kwargs):
        """提供执行事件供顺序断言。"""
        yield format_sse({"run_id": "run-1"}, event="message")

    monkeypatch.setattr(public_api.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(public_api, "AgentRunRequestRepository", RequestRepository)
    monkeypatch.setattr(public_api, "stream_agent_run_events", run_events)
    events = [
        event
        async for event in public_api.stream_public_request_events(
            request_id="request-1", uid="user-1", after_seq="0-0", run_stream_url="/request/events"
        )
    ]
    assert events == [
        format_sse(
            {"request_id": "request-1", "run_id": "run-1", "stream_url": "/request/events"},
            event="run_created",
        ),
        format_sse({"run_id": "run-1"}, event="message"),
    ]


def test_public_multimodal_input_keeps_order_and_image_media_type():
    """图文交错和 PNG 类型在模型输入及 Items 回读中保持原样。"""
    message = InputMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "第一张"},
                {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
                {"type": "input_text", "text": "第二张"},
                {"type": "input_image", "image_url": "data:image/webp;base64,Yg=="},
            ],
        }
    )
    ordered = _ordered_message_content([message])
    built = build_chat_input_message_from_openai_content(ordered)
    assert [part["type"] for part in built.langchain_message.content] == ["text", "image_url", "text", "image_url"]
    assert built.langchain_message.content[1]["image_url"]["url"] == "data:image/png;base64,YQ=="
    assert built.langchain_message.content[3]["image_url"]["url"] == "data:image/webp;base64,Yg=="
    item = SimpleNamespace(
        role="user",
        content="第一张\n第二张",
        image_content="YQ==",
        extra_metadata={"raw_message": {"content": built.langchain_message.content}},
    )
    assert public_api._public_item_content(item) == [
        {"type": "input_text", "text": "第一张"},
        {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
        {"type": "input_text", "text": "第二张"},
        {"type": "input_image", "image_url": "data:image/webp;base64,Yg=="},
    ]


@pytest.mark.asyncio
async def test_empty_session_workdir_failure_exposes_recoverable_creation(monkeypatch):
    """目录物化失败后返回已持久 Session ID，原键重试恢复同一会话。"""
    stored = None
    attempts = 0

    class Repository:
        """模拟唯一创建键查询。"""

        def __init__(self, _db):
            pass

        async def get_conversation_by_creation_request_id(self, _uid, _request_id):
            """读取已提交的会话。"""
            return stored

        async def get_conversation_by_thread_id(self, _thread_id):
            """按服务端 Thread ID 读取同一会话。"""
            return stored

    async def create_thread_view(**kwargs):
        """第一次模拟提交后目录故障，第二次按旧键重试。"""
        nonlocal stored, attempts
        attempts += 1
        if stored is None:
            stored = SimpleNamespace(
                thread_id="thread",
                extra_metadata=kwargs["metadata"],
                agent_id="agent",
                title="新的对话",
                app_id=kwargs["app_id"],
            )
        if attempts == 1:
            raise HTTPException(status_code=400, detail="工作目录暂不可用")
        return {"id": "thread"}

    async def get_session(**_kwargs):
        """返回持久 Session 投影。"""
        return {"thread_id": "thread"}

    monkeypatch.setattr(public_api, "ConversationRepository", Repository)
    monkeypatch.setattr(public_api, "create_thread_view", create_thread_view)
    monkeypatch.setattr(public_api, "get_public_session", get_session)
    args = {
        "agent_id": "agent",
        "idempotency_key": "key",
        "user": SimpleNamespace(uid="user"),
        "api_key": None,
        "project_id": None,
        "title": None,
        "model_spec": None,
        "tool_approval_mode": None,
        "db": object(),
    }
    with pytest.raises(HTTPException) as error:
        await public_api.create_public_session(**args)
    assert error.value.status_code == 503
    assert error.value.detail["session_id"] == "thread"
    assert (await public_api.create_public_session(**args))["thread_id"] == "thread"
    assert attempts == 2


@pytest.mark.asyncio
async def test_existing_legacy_request_keeps_its_id_and_intent_conflict(monkeypatch):
    """已持久化的旧 ID 即使输入改变也交给 Request Owner 判定冲突。"""
    uid, app_id, key = "user-1", "app:new-session:part", "key:one"
    legacy_identity = f"{uid}:{app_id}:new-session:{key}"
    legacy_request_id = hash_id("pubreq_", legacy_identity, length=64)
    legacy_thread_id = hash_id("pubsess_", legacy_identity, length=64)
    legacy_intent = '{"agent_id":"agent-1","session_id":"' + legacy_thread_id + '","text_parts":["原始输入"]}'
    legacy = SimpleNamespace(
        request_id=legacy_request_id,
        uid=uid,
        app_id=app_id,
        conversation_thread_id=legacy_thread_id,
        agent_slug="agent-1",
        source="public_api",
        channel="api",
        external_id=legacy_request_id,
        intent_hash=hashlib.sha256(legacy_intent.encode()).hexdigest(),
    )

    class Repository:
        """返回已持久化的旧协议 Request。"""

        async def get_by_request_id(self, request_id):
            assert request_id == legacy_request_id
            return legacy

    class AgentRepository:
        """使测试只聚焦已有 Request 的重放分支。"""

        async def get_visible_by_slug(self, **kwargs):
            return SimpleNamespace(slug="agent-1")

    class ConversationRepository:
        """返回历史 Request 绑定的现存 Thread。"""

        async def get_conversation_by_creation_request_id(self, uid, request_id):
            """旧入口没有 Conversation 创建键。"""
            return None

        async def get_conversation_by_thread_id(self, thread_id):
            assert thread_id == legacy_thread_id
            return SimpleNamespace(uid=uid, status="active", agent_id="agent-1", project_id=1)

    class ProjectRepository:
        """返回历史 Thread 的可访问 Project。"""

        async def get_for_user(self, project_id, current_uid):
            assert (project_id, current_uid) == (1, uid)
            return SimpleNamespace(status="active")

    async def request_view(*, repo, request):
        """返回真实作用域校验通过后的旧 Request 回执。"""
        assert request is legacy
        return {"request_id": request.request_id, "status": "queued", "run_id": None}

    monkeypatch.setattr(public_api, "AgentRunRequestRepository", lambda db: Repository())
    monkeypatch.setattr(public_api, "ConversationRepository", lambda db: ConversationRepository())
    monkeypatch.setattr(agent_request_service, "AgentRepository", lambda db: AgentRepository())
    monkeypatch.setattr(agent_request_service, "AgentRunRequestRepository", lambda db: Repository())
    monkeypatch.setattr(agent_request_service, "ConversationRepository", lambda db: ConversationRepository())
    monkeypatch.setattr(agent_request_service, "ProjectRepository", lambda db: ProjectRepository())
    monkeypatch.setattr(agent_request_service, "request_view", request_view)

    class FakeDb:
        """模拟旧 Request 重放后的事务完成。"""

        async def commit(self):
            """保持旧请求的只读重放路径可提交。"""
            return None

    args = {
        "agent_id": "agent-1",
        "thread_id": None,
        "idempotency_key": key,
        "user": SimpleNamespace(uid=uid),
        "api_key": SimpleNamespace(app_id=app_id, id=1),
        "db": FakeDb(),
    }
    receipt = await public_api.submit_public_message(text_parts=["原始输入"], **args)
    assert receipt["request_id"] == legacy_request_id
    assert receipt["thread_id"] == legacy_thread_id
    with pytest.raises(HTTPException) as exc:
        await public_api.submit_public_message(text_parts=["变更输入"], **args)
    assert exc.value.status_code == 409
