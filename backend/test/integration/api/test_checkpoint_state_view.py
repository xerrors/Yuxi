"""真实 HTTP 与 PostgreSQL 验证线程快照读取和用户隔离。"""

import os
import uuid
from typing import TypedDict

import pytest
from langchain.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class DisplayState(TypedDict):
    """面板已保存的业务字段。"""

    messages: list
    todos: list
    artifacts: list
    subagent_runs: list
    token_usage: dict


async def test_state_view_reads_postgres_snapshot_and_rejects_other_users(test_client, admin_headers, standard_user):
    """无模型配置也可读快照，未知、删除和其他用户线程均拒绝读取。"""
    slug = f"pytest-checkpoint-{uuid.uuid4().hex[:8]}"
    created = await test_client.post(
        "/api/agent",
        json={"name": slug, "slug": slug, "backend_id": "ChatbotAgent", "config_json": {"context": {}}},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    thread_id = None
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        try:
            created_thread = await test_client.post(
                "/api/chat/thread",
                json={
                    "agent_id": slug,
                    "title": make_test_conversation_title("checkpoint"),
                    "metadata": make_test_conversation_metadata("checkpoint"),
                },
                headers=admin_headers,
            )
            assert created_thread.status_code == 200, created_thread.text
            thread_id = created_thread.json()["id"]
            url = f"/api/chat/thread/{thread_id}/state"
            empty = await test_client.get(url, headers=admin_headers)
            assert empty.status_code == 200, empty.text
            assert empty.json()["agent_state"] == {
                "todos": [],
                "files": {},
                "artifacts": [],
                "subagent_runs": [],
                "token_usage": None,
            }

            payload = {
                "messages": [HumanMessage(content="persisted checkpoint message")],
                "todos": [{"content": "persisted todo", "status": "completed"}],
                "artifacts": ["result.txt"],
                "subagent_runs": [{"run_id": "saved-child"}],
                "token_usage": {"total": 17},
            }
            graph = StateGraph(DisplayState)
            graph.add_node("done", lambda state: {})
            graph.add_edge(START, "done")
            graph.add_edge("done", END)
            await graph.compile(checkpointer=saver).ainvoke(payload, {"configurable": {"thread_id": thread_id}})

            response = await test_client.get(url, params={"include_messages": "true"}, headers=admin_headers)
            assert response.status_code == 200, response.text
            assert response.json()["agent_state"] == {
                key: value for key, value in payload.items() if key != "messages"
            } | {"files": {}}
            assert response.json()["messages"][0]["content"] == "persisted checkpoint message"
            assert "interrupt" not in response.json()
            assert (await test_client.get(url)).status_code == 401
            assert (await test_client.get(url, headers=standard_user["headers"])).status_code == 404
            assert (
                await test_client.get(f"/api/chat/thread/{uuid.uuid4()}/state", headers=admin_headers)
            ).status_code == 404
            deleted = await test_client.delete(f"/api/chat/thread/{thread_id}", headers=admin_headers)
            assert deleted.status_code == 200, deleted.text
            assert (await test_client.get(url, headers=admin_headers)).status_code == 404
        finally:
            if thread_id:
                await saver.adelete_thread(thread_id)
                deleted = await test_client.delete(f"/api/chat/thread/{thread_id}", headers=admin_headers)
                assert deleted.status_code in (200, 404), deleted.text
            deleted_agent = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
            assert deleted_agent.status_code in (200, 404), deleted_agent.text
