"""真实 HTTP 验证统一会话资源、接收回执和活动时间。"""

import uuid

import pytest
from sqlalchemy import select

from test.integration.api.helpers import delete_created_threads
from test.live_api_cleanup import make_test_session_title
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.runtime_paths import runtime_workspace_path
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_public_error_and_media_projections_preserve_stored_resources(test_client, standard_user, postgres_sessions):
    """真实 HTTP 隐藏解释器转储、保留原始错误并按音频字节响应。"""
    headers = standard_user["headers"]
    created = await test_client.post(
        "/api/v1/agents/sessions",
        json={"agent_id": "default-chatbot", "title": make_test_session_title("resource-projections")},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    turn_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    stored_error = "Token <ContextVar name='input_messages' at 0x12345678> belongs to another Context"
    workspace, file_path = None, None
    try:
        async with postgres_sessions() as db:
            session = await db.scalar(select(Session).where(Session.thread_id == session_id))
            project = await db.get(Project, session.project_id)
            workspace = Workspace(session.uid)
            file_path = f"/{project.workdir_path.strip('/')}/recording.txt"
            turn = AgentTurn(id=turn_id, thread_id=session_id, uid=session.uid, status="failed", finished_at=utc_now())
            db.add(turn)
            await db.flush()
            db.add(
                AgentRun(
                    id=run_id,
                    thread_id=session_id,
                    runtime_scope_id=session.tree_root_thread_id,
                    session_record_id=session.id,
                    turn_id=turn_id,
                    uid=session.uid,
                    agent_slug="default-chatbot",
                    status="failed",
                    error_type="worker_error",
                    error_message=stored_error,
                    finished_at=utc_now(),
                )
            )
            await db.flush()
            turn.current_run_id = turn.result_run_id = run_id
            await db.commit()

        audio = b"RIFF\x10\x00\x00\x00WAVEfmt "
        workspace.replace_authorized_file(file_path, audio)
        base = f"/api/v1/agents/sessions/{session_id}"
        artifact = await test_client.get(f"{base}/artifacts/{runtime_workspace_path(file_path).lstrip('/')}", headers=headers)
        assert artifact.status_code == 200, artifact.text
        assert artifact.content == audio
        assert artifact.headers["content-type"] == "audio/wav"
        for error, expected in ((stored_error, "执行未能完成，请稍后重试。"), ("权限已撤销", "权限已撤销")):
            async with postgres_sessions() as db:
                run = await db.get(AgentRun, run_id)
                run.error_message = error
                await db.commit()
            run_response = await test_client.get(f"{base}/runs/{run_id}", headers=headers)
            turn_response = await test_client.get(f"{base}/turns/{turn_id}", headers=headers)
            assert run_response.status_code == turn_response.status_code == 200
            assert run_response.json()["error_message"] == expected
            assert turn_response.json()["error"]["message"] == expected
            async with postgres_sessions() as db:
                assert (await db.get(AgentRun, run_id)).error_message == error
    finally:
        if workspace is not None and file_path is not None:
            workspace.delete_authorized_path(file_path, root="/")
        await delete_created_threads(session_id)


async def test_session_resource_operations_preserve_identity_and_activity(test_client, admin_headers, standard_user):
    """创建、读取、列表与更新共用模型，展示更新不伪造执行活动。"""
    session_ids = []
    try:
        for title in ("资源甲", "资源乙"):
            response = await test_client.post(
                "/api/v1/agents/sessions",
                json={"agent_id": "default-chatbot", "title": title},
                headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 201, response.text
            created = response.json()
            session_ids.append(created["id"])
            assert created["object"] == "agent.session"
            assert created["status"] == "idle"
            assert created["agent"]["model"]
            assert created["yuxi"]["receipt"]["status"] == "accepted"
            assert created["yuxi"]["receipt"]["session_id"] == created["id"]
            assert not {"thread_id", "agent_id", "status", "metadata", "input_id"} & created["yuxi"].keys()
        target = session_ids[-1]
        before = (await test_client.get(f"/api/v1/agents/sessions/{target}", headers=admin_headers)).json()
        changed = await test_client.post(
            f"/api/v1/agents/sessions/{target}",
            json={"yuxi": {"title": "更名", "is_pinned": True}},
            headers=admin_headers,
        )
        assert changed.status_code == 200, changed.text
        updated = changed.json()
        assert updated.keys() == before.keys() == created.keys()
        assert updated["yuxi"].keys() == before["yuxi"].keys() == created["yuxi"].keys()
        assert updated["yuxi"]["title"] == "更名"
        assert updated["agent"] == before["agent"] == created["agent"]
        assert updated["last_active_at"] == before["last_active_at"]
        assert (await test_client.patch(f"/api/v1/agents/sessions/{target}", json={}, headers=admin_headers)).status_code == 405
        assert (
            await test_client.post(f"/api/v1/agents/sessions/{target}", json={"model_spec": "old"}, headers=admin_headers)
        ).status_code == 422
        listed = await test_client.get("/api/v1/agents/sessions", params={"limit": 1}, headers=admin_headers)
        assert listed.status_code == 200, listed.text
        page = listed.json()
        assert page["object"] == "list"
        assert len(page["data"]) == 1
        assert page["data"][0].keys() == before.keys()
        next_page = await test_client.get("/api/v1/agents/sessions", params={"limit": 1, "after": page["last_id"]}, headers=admin_headers)
        assert next_page.status_code == 200, next_page.text
        assert next_page.json()["data"][0]["id"] != page["data"][0]["id"]
        invalid = await test_client.get("/api/v1/agents/sessions", params={"after": str(uuid.uuid4())}, headers=admin_headers)
        assert invalid.status_code == 400
        foreign_cursor = await test_client.get("/api/v1/agents/sessions", params={"after": target}, headers=standard_user["headers"])
        assert foreign_cursor.status_code == 400
        pinned = await test_client.get("/api/v1/agents/sessions", params={"is_pinned": True, "limit": 100}, headers=admin_headers)
        assert pinned.status_code == 200, pinned.text
        assert target in {item["id"] for item in pinned.json()["data"]}
        assert all(item["yuxi"]["is_pinned"] for item in pinned.json()["data"])
        viewed = await test_client.post(f"/api/v1/agents/sessions/{target}/viewed", headers=admin_headers)
        assert viewed.status_code == 200, viewed.text
        assert viewed.json()["last_active_at"] == before["last_active_at"]
        archived = await test_client.post(f"/api/v1/agents/sessions/{target}/archive", headers=admin_headers)
        assert archived.status_code == 200, archived.text
        assert archived.json()["yuxi"]["archived"] is True
        assert archived.json()["status"] == "idle"
    finally:
        await delete_created_threads(*session_ids)


async def test_merged_steer_receipts_advance_session_activity(test_client, standard_user):
    """同一 pending Input 的第二次接收仍推进活动时间，列表与详情一致。"""
    import os
    from datetime import UTC, datetime, timedelta

    import asyncpg

    admin_headers = standard_user["headers"]
    created = await test_client.post(
        "/api/v1/agents/sessions",
        json={"agent_id": "default-chatbot"},
        headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        await conn.execute("UPDATE sessions SET queue_paused=true WHERE thread_id=$1", session_id)
        receipts = []
        for text in ("first steer", "second steer"):
            response = await test_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
                            "yuxi": {"mode": "steer"},
                        }
                    ]
                },
                headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 202, response.text
            receipts.append(response.json())
        assert receipts[0]["input_id"] != receipts[1]["input_id"]
        assert receipts[0]["event_id"] != receipts[1]["event_id"]
        first_at = datetime.now(UTC) + timedelta(seconds=10)
        second_at = first_at + timedelta(seconds=2)
        await conn.execute("UPDATE agent_inputs SET created_at=$2 WHERE id=$1", receipts[0]["input_id"], first_at)
        for receipt, timestamp in zip(receipts, (first_at, second_at), strict=True):
            await conn.execute("UPDATE agent_input_receipts SET created_at=$2 WHERE id=$1", receipt["event_id"], timestamp)
        detail = await test_client.get(f"/api/v1/agents/sessions/{session_id}", headers=admin_headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["last_active_at"] == int(second_at.timestamp())
        listed = await test_client.get("/api/v1/agents/sessions", headers=admin_headers, params={"limit": 100})
        assert listed.status_code == 200, listed.text
        resource = next(item for item in listed.json()["data"] if item["id"] == session_id)
        assert resource == detail.json()
    finally:
        await conn.close()
        await delete_created_threads(session_id)


async def test_title_search_finds_empty_sessions_and_preserves_user_scope(test_client, admin_headers, standard_user):
    """真实 HTTP 按标题检索无消息会话，保持分页、归档和用户隔离。"""
    prefix = f"GUI标题_{uuid.uuid4().hex}"
    created = []
    try:
        for headers, suffix in [(admin_headers, "甲"), (admin_headers, "乙"), (standard_user["headers"], "他人")]:
            response = await test_client.post(
                "/api/v1/agents/sessions",
                json={"agent_id": "default-chatbot", "title": prefix + suffix},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 201, response.text
            created.append(response.json()["id"])
        response = await test_client.get("/api/v1/agents/sessions/search", params={"q": prefix, "limit": 1}, headers=admin_headers)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["has_more"] is True
        assert len(first["items"]) == 1
        hit = first["items"][0]
        assert hit["session"]["id"] in created[:2]
        assert hit["matched_count"] == 0 and hit["snippets"] == [] and hit["message_id"] is None
        second = (
            await test_client.get("/api/v1/agents/sessions/search", params={"q": prefix, "limit": 1, "offset": 1}, headers=admin_headers)
        ).json()
        assert second["has_more"] is False
        assert {hit["session"]["id"], second["items"][0]["session"]["id"]} == set(created[:2])
        await test_client.post(f"/api/v1/agents/sessions/{created[0]}/archive", headers=admin_headers)
        active = (await test_client.get("/api/v1/agents/sessions/search", params={"q": prefix}, headers=admin_headers)).json()
        assert [item["session"]["id"] for item in active["items"]] == [created[1]]
    finally:
        await delete_created_threads(*created)


async def test_cooperation_depth_config_accepts_zero_and_rejects_invalid_values(test_client, admin_headers):
    """真实配置 HTTP 持久化整数，非法更新不能覆盖原值。"""
    previous = (await test_client.get("/api/system/config", headers=admin_headers)).json()["max_cooperation_depth"]
    try:
        response = await test_client.post("/api/system/config/update", json={"max_cooperation_depth": 0}, headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["max_cooperation_depth"] == 0
        assert (await test_client.get("/api/system/config", headers=admin_headers)).json()["max_cooperation_depth"] == 0
        for invalid in [-1, True, 1.5, "2", None]:
            response = await test_client.post("/api/system/config/update", json={"max_cooperation_depth": invalid}, headers=admin_headers)
            assert response.status_code == 400, response.text
            assert (await test_client.get("/api/system/config", headers=admin_headers)).json()["max_cooperation_depth"] == 0
    finally:
        restored = await test_client.post("/api/system/config/update", json={"max_cooperation_depth": previous}, headers=admin_headers)
        assert restored.status_code == 200, restored.text
