"""通过真实 HTTP 验证子 Run 身份恢复和用户隔离。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import AgentRun, Conversation, Project, SubagentThread
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_state_recovers_children_without_checkpoint_and_rejects_other_user(
    test_client, standard_user, admin_headers
):
    """创建已提交而父 checkpoint 从未写入的子 Run，页面仍能发现并读到终态。"""
    uid = standard_user["user"]["uid"]
    project_id = str(uuid.uuid4())
    parent_thread, child_thread = (f"pytest-subagent-state-{uuid.uuid4()}" for _ in range(2))
    parent_id, child_id = (str(uuid.uuid4()) for _ in range(2))
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
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
            parent = Conversation(
                thread_id=parent_thread, uid=uid, project_id=project_id, agent_id="state-probe", status="active"
            )
            child = Conversation(
                thread_id=child_thread, uid=uid, project_id=project_id, agent_id="state-probe-child", status="subagent"
            )
            db.add_all([parent, child])
            await db.flush()
            db.add(
                AgentRun(
                    id=parent_id,
                    uid=uid,
                    agent_slug="state-probe",
                    run_type="chat",
                    conversation_id=parent.id,
                    conversation_thread_id=parent_thread,
                    runtime_scope_id=parent_thread,
                    request_id=parent_id,
                    status="completed",
                    finished_at=utc_now_naive(),
                    input_payload={},
                )
            )
            await db.flush()
            relation = SubagentThread(
                uid=uid,
                parent_conversation_id=parent.id,
                child_conversation_id=child.id,
                child_thread_id=child_thread,
                subagent_slug="state-probe-child",
                created_by_run_id=parent_id,
            )
            db.add(relation)
            await db.flush()
            db.add(
                AgentRun(
                    id=child_id,
                    uid=uid,
                    agent_slug="state-probe-child",
                    run_type="subagent",
                    conversation_id=child.id,
                    conversation_thread_id=child_thread,
                    runtime_scope_id=parent_thread,
                    request_id=child_id,
                    status="completed",
                    finished_at=utc_now_naive(),
                    created_by_run_id=parent_id,
                    subagent_thread_relation_id=relation.id,
                    input_payload={"runtime": {"tool_call_id": "start-probe"}},
                )
            )
            await db.commit()

        response = await test_client.get(f"/api/chat/thread/{parent_thread}/state", headers=standard_user["headers"])
        assert response.status_code == 200, response.text
        runs = response.json()["agent_state"]["subagent_runs"]
        assert len(runs) == 1, response.json()
        assert runs[0]["run_id"] == child_id
        assert runs[0]["status"] == "completed"
        assert runs[0]["events_url"] == f"/api/agent/runs/{child_id}/events"
        run_response = await test_client.get(f"/api/agent/runs/{child_id}", headers=standard_user["headers"])
        assert run_response.json()["run"]["status"] == "completed"
        for url in (f"/api/chat/thread/{parent_thread}/state", f"/api/agent/runs/{child_id}"):
            denied = await test_client.get(url, headers=admin_headers)
            assert denied.status_code == 404, denied.text
            assert child_id not in denied.text
    finally:
        async with sessions() as db:
            await db.execute(delete(AgentRun).where(AgentRun.id == child_id))
            await db.execute(delete(SubagentThread).where(SubagentThread.child_thread_id == child_thread))
            await db.execute(delete(AgentRun).where(AgentRun.id == parent_id))
            await db.execute(delete(Conversation).where(Conversation.thread_id.in_([parent_thread, child_thread])))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await engine.dispose()
