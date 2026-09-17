"""主动上下文压缩的 HTTP 与 PostgreSQL checkpoint 集成测试。"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock
from yuxi.agents.context import BaseContext
from typing import Annotated, Any, TypedDict

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.chat_router import chat
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services import context_compression_service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Conversation, Project, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _CheckpointState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    _summarization_event: dict[str, Any]
    _summarization_session_id: str
    token_usage: dict[str, Any]


async def test_compress_thread_persists_canonical_checkpoint_through_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功 HTTP 请求通过真实 PostgreSQL checkpointer 写入摘要事件。"""
    thread_id = f"pytest-compression-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    project_id = str(uuid.uuid4())
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    checkpointer = await pg_manager.setup_langgraph_checkpointer()

    builder = StateGraph(_CheckpointState)
    builder.add_node("idle", lambda _state: {})
    builder.add_edge(START, "idle")
    builder.set_finish_point("idle")
    graph = builder.compile(checkpointer=checkpointer)
    graph_config = {"configurable": {"uid": uid, "thread_id": thread_id}}
    original_messages = [
        HumanMessage(content="first question"),
        AIMessage(content="first answer"),
        HumanMessage(content="latest question"),
    ]
    await graph.ainvoke({"messages": original_messages}, config=graph_config)

    user = User(username=uid, uid=uid, password_hash="test", role="user")
    async with session_factory() as db:
        db.add(user)
        await db.flush()
        db.add(
            Project(
                id=project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )
        )
        await db.flush()
        db.add(
            Conversation(
                thread_id=thread_id,
                uid=uid,
                project_id=project_id,
                agent_id="main",
                status="active",
            )
        )
        await db.commit()

    class Agent:
        capabilities = ["context_compression"]
        context_schema = BaseContext

        async def get_graph(self, *, context):
            assert context.uid == uid
            assert context.thread_id == thread_id
            return graph

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return type(
                "AgentItem",
                (),
                {"backend_id": "ChatbotAgent", "config_json": {"context": {"summary_threshold": 200}}},
            )()

    class Compressor:
        async def aforce_summarize(self, values):
            assert [message.content for message in values["messages"]] == [
                "first question",
                "first answer",
                "latest question",
            ]
            return {
                "_summarization_event": {
                    "cutoff_index": 2,
                    "summary_message": AIMessage(content="summary of the first exchange"),
                    "file_path": "/home/gem/user-data/projects/history.md",
                },
                "_summarization_session_id": "session-1",
            }, {
                "status": "completed",
                "before_tokens": 30,
                "after_tokens": 20,
                "compressed_messages": 2,
                "file_path": "/home/gem/user-data/projects/history.md",
            }

    async def resolve_model(*_args, **_kwargs):
        return "test:model"

    async def workdir(**_kwargs):
        return f"projects/{project_id}"

    async def runtime(**_kwargs):
        return None

    monkeypatch.setattr(context_compression_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(context_compression_service.agent_manager, "get_agent", lambda _backend_id: Agent())
    monkeypatch.setattr(context_compression_service, "resolve_agent_run_model_spec", resolve_model)
    monkeypatch.setattr(context_compression_service, "ensure_conversation_workdir_available", workdir)
    monkeypatch.setattr(context_compression_service, "_ensure_runtime_available", runtime)
    monkeypatch.setattr(context_compression_service, "_release_runtime", runtime)
    monkeypatch.setattr(context_compression_service, "prepare_agent_runtime_context", AsyncMock())
    monkeypatch.setattr(context_compression_service, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(
        context_compression_service,
        "create_summary_middleware_from_context",
        lambda _context, *, backend: Compressor(),
    )

    app = FastAPI()
    app.include_router(chat, prefix="/api")

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = lambda: user

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(f"/api/chat/thread/{thread_id}/compress", json={})

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        state = await graph.aget_state(graph_config)
        assert [message.content for message in state.values["messages"]] == [
            "first question",
            "first answer",
            "latest question",
        ]
        assert state.values["_summarization_event"]["cutoff_index"] == 2
        assert state.values["_summarization_event"]["summary_message"].content == "summary of the first exchange"
        assert state.values["_summarization_session_id"] == "session-1"
        assert state.values["token_usage"]["summary_trigger_tokens"] == 200 * 1024
    finally:
        await checkpointer.adelete_thread(thread_id)
        async with session_factory() as db:
            await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()
