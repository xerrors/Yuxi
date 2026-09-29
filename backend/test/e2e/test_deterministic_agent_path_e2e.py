"""无外部密钥地验证 shipping API、worker、SSE 与 PostgreSQL 因果链。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid

import asyncpg
import httpx
import pytest
from e2e_helpers import cancel_run, consume_events, delete_agent, postgres_dsn, wait_for_run
from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, get_sandbox_provider
from yuxi.config import get_skill_projection_dir
from yuxi.models.utils import parse_assistant_message_body
from yuxi.utils.hash_utils import hash_id
from yuxi.workspace.paths import user_workspace_dir, workspace_uid_dirname

from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(360)]

EXPECTED_OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"
EXPECTED_PRELOADED_SKILL_MARKER = "# 图片生成技能"
EXPECTED_PRELOADED_TOOL = "present_artifacts"
EXPECTED_TOOL_CALL_ID = "call-preloaded-tool"
EXPECTED_TOOL_RESULT_MARKER = "已将交付物展示给用户"
BLOCK_BEFORE_RESPONSE_MARKER = "DETERMINISTIC_BLOCK_BEFORE_RESPONSE"
TOOL_ERROR_MARKER = "DETERMINISTIC_TOOL_ERROR"
LARGE_TOOL_RESULT_MARKER = "DETERMINISTIC_LARGE_TOOL_RESULT"
LARGE_TOOL_CALL_ID = "call-large-tool-result"
PROVIDER_ID = "ci-replay"
MODEL_SPEC = f"{PROVIDER_ID}:deterministic-chat"


async def test_public_agents_key_request_and_run_keep_source_and_result(e2e_client, e2e_headers):
    """受限 Key 从外部提交到 worker 终态时保持 APP、幂等意图和同一 Run 结果。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    await _create_provider(e2e_client, e2e_headers)
    agent_slug = None
    session_id = None
    run_id = None
    second_run_id = None
    key_ids = []
    collision_thread_ids = []
    collision_request_ids = []
    try:
        agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
        created = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Public deterministic E2E",
                "access_level": "agents",
                "app_id": "ci-public-e2e",
            },
        )
        assert created.status_code == 200, created.text
        key_ids.append(created.json()["api_key"]["id"])
        public_headers = {
            "Authorization": f"Bearer {created.json()['secret']}",
            "Idempotency-Key": f"public-deterministic-first-{uuid.uuid4().hex}",
            "X-App-Id": "forged",
        }
        visible_agent = await e2e_client.get(f"/api/v1/agents/{agent_slug}", headers=public_headers)
        assert visible_agent.status_code == 200, visible_agent.text
        assert visible_agent.json()["id"] == agent_slug
        body = {
            "agent_id": agent_slug,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": f"只输出\n{EXPECTED_OUTPUT}"}]}],
        }
        response = await e2e_client.post("/api/v1/agents/threads", headers=public_headers, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["X-App-Id"] == "ci-public-e2e"
        session_id = response.json()["thread_id"]
        turn_id = response.json()["request_id"]
        assert response.json()["result_url"] == f"/api/v1/agents/threads/{session_id}/requests/{turn_id}"

        replay = await e2e_client.post("/api/v1/agents/sessions", headers=public_headers, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == session_id
        assert replay.json()["turn_id"] == turn_id
        native_thread = await e2e_client.get(f"/api/v1/agents/threads/{session_id}", headers=public_headers)
        assert native_thread.status_code == 200, native_thread.text
        assert native_thread.json()["request_id"] == turn_id
        conflict = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers=public_headers,
            json={**body, "input": [{"role": "user", "content": [{"type": "input_text", "text": "其他输入"}]}]},
        )
        assert conflict.status_code == 409, conflict.text
        split_conflict = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers=public_headers,
            json={
                **body,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "只输出"},
                            {"type": "input_text", "text": EXPECTED_OUTPUT},
                        ],
                    }
                ],
            },
        )
        assert split_conflict.status_code == 409, split_conflict.text
        blocked = await e2e_client.get("/api/agent", headers=public_headers)
        assert blocked.status_code == 403, blocked.text

        result_url = f"/api/v1/agents/sessions/{session_id}/turns/{turn_id}"
        for _ in range(150):
            result = await e2e_client.get(result_url, headers=public_headers)
            assert result.status_code == 200, result.text
            if result.json()["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Public API Turn did not reach a terminal status")
        assert result.json()["status"] == "completed", result.text
        assert result.json()["output"] == EXPECTED_OUTPUT
        run_id = result.json()["run_id"]
        assert run_id
        native_result = await e2e_client.get(response.json()["result_url"], headers=public_headers)
        assert native_result.status_code == 200, native_result.text
        assert native_result.json()["request_id"] == turn_id
        assert native_result.json()["run_id"] == run_id
        assert native_result.json()["output"] == EXPECTED_OUTPUT
        assert "session_id" not in native_result.json()

        async with e2e_client.stream("GET", response.json()["events_url"], headers=public_headers) as native_events:
            assert native_events.status_code == 200, native_events.text
            native_stream_body = (await native_events.aread()).decode()
            assert native_stream_body.startswith("event: run_created\n")
            assert f'"run_id": "{run_id}"' in native_stream_body
            assert "event: end" in native_stream_body

        async with e2e_client.stream(
            "GET",
            f"/api/v1/agents/sessions/{session_id}/events",
            params={"turn_id": turn_id},
            headers=public_headers,
        ) as events:
            assert events.status_code == 200, events.text
            assert events.headers["X-App-Id"] == "ci-public-e2e"
            stream_body = (await events.aread()).decode()
        assert "event: end" in stream_body

        next_headers = {**public_headers, "Idempotency-Key": "public-deterministic-second"}
        next_event = {
            "events": [
                {
                    "type": "agent.session.input.message",
                    "mode": "follow_up",
                    "input": [
                        {"role": "user", "content": [{"type": "input_text", "text": f"只输出 {EXPECTED_OUTPUT}"}]}
                    ],
                }
            ]
        }
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{session_id}/events", headers=next_headers, json=next_event
        )
        assert accepted.status_code == 202, accepted.text
        second_turn_id = accepted.json()["turn_id"]
        assert second_turn_id != turn_id
        stale_cancel = await e2e_client.post(
            f"/api/v1/agents/sessions/{session_id}/events",
            headers={**public_headers, "Idempotency-Key": f"stale-cancel-{uuid.uuid4().hex}"},
            json={"events": [{"type": "agent.session.input.cancel", "run_id": run_id}]},
        )
        assert stale_cancel.status_code == 409, stale_cancel.text
        native_replay = await e2e_client.post(
            f"/api/v1/agents/threads/{session_id}/requests",
            headers=next_headers,
            json={"input": next_event["events"][0]["input"]},
        )
        assert native_replay.status_code == 202, native_replay.text
        assert native_replay.json()["request_id"] == second_turn_id
        assert native_replay.json()["thread_id"] == session_id
        receipt_identity = json.dumps(
            [uid, "ci-public-e2e", session_id, next_headers["Idempotency-Key"]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        pg = await asyncpg.connect(postgres_dsn())
        try:
            await pg.execute(
                "DELETE FROM agent_session_input_receipts WHERE id = $1",
                hash_id("pubevt_", receipt_identity, length=64),
            )
        finally:
            await pg.close()
        omitted_mode_event = {
            "events": [{key: value for key, value in next_event["events"][0].items() if key != "mode"}]
        }
        old_client_replay = await e2e_client.post(
            f"/api/v1/agents/sessions/{session_id}/events", headers=next_headers, json=omitted_mode_event
        )
        assert old_client_replay.status_code == 202, old_client_replay.text
        assert old_client_replay.json()["request_id"] == second_turn_id
        native_conflict = await e2e_client.post(
            f"/api/v1/agents/threads/{session_id}/requests",
            headers=next_headers,
            json={"input": [{"role": "user", "content": [{"type": "input_text", "text": "不同输入"}]}]},
        )
        assert native_conflict.status_code == 409, native_conflict.text
        for _ in range(150):
            second = await e2e_client.get(
                f"/api/v1/agents/sessions/{session_id}/turns/{second_turn_id}", headers=public_headers
            )
            assert second.status_code == 200, second.text
            if second.json()["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Second Public API Turn did not reach a terminal status")
        assert second.json()["status"] == "completed", second.text
        assert second.json()["output"] == EXPECTED_OUTPUT
        second_run_id = second.json()["run_id"]
        session = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}", headers=public_headers)
        assert session.status_code == 200, session.text
        assert session.json()["turn_id"] == second_turn_id
        assert session.json()["status"] == "idle"
        late_native_replay = await e2e_client.post("/api/v1/agents/threads", headers=public_headers, json=body)
        assert late_native_replay.status_code == 200, late_native_replay.text
        assert late_native_replay.json()["request_id"] == turn_id
        assert late_native_replay.json()["run_id"] == run_id
        assert late_native_replay.json()["result_url"] == response.json()["result_url"]
        pg = await asyncpg.connect(postgres_dsn())
        try:
            await pg.execute(
                "UPDATE conversations SET creation_request_id = NULL, "
                "extra_metadata = (extra_metadata::jsonb - 'public_creation_intent')::json "
                "WHERE thread_id = $1",
                session_id,
            )
        finally:
            await pg.close()
        late_session_replay = await e2e_client.post("/api/v1/agents/sessions", headers=public_headers, json=body)
        assert late_session_replay.status_code == 200, late_session_replay.text
        assert late_session_replay.json()["turn_id"] == turn_id
        assert late_session_replay.json()["run_id"] == run_id

        conn = await asyncpg.connect(postgres_dsn())
        try:
            persisted = await conn.fetchrow(
                """
                SELECT req.app_id, req.api_key_id, req.intent_hash, run.app_id AS run_app_id,
                       run.api_key_id AS run_api_key_id, run.id AS run_id,
                       conversation.app_id AS conversation_app_id
                FROM agent_run_requests req
                JOIN agent_runs run ON run.id = req.dispatched_run_id
                JOIN conversations conversation ON conversation.thread_id = req.conversation_thread_id
                WHERE req.request_id = $1
                """,
                turn_id,
            )
            assert persisted["app_id"] == persisted["run_app_id"] == "ci-public-e2e", persisted
            assert persisted["conversation_app_id"] == "ci-public-e2e", persisted
            assert persisted["api_key_id"] == persisted["run_api_key_id"] == key_ids[0], persisted
            assert persisted["run_id"] == run_id and persisted["intent_hash"], persisted
            assert await conn.fetchval("SELECT COUNT(*) FROM agent_run_requests WHERE request_id = $1", turn_id) == 1
        finally:
            await conn.close()

        other = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Other APP E2E",
                "access_level": "agents",
                "app_id": "ci-other-app",
            },
        )
        assert other.status_code == 200, other.text
        key_ids.append(other.json()["api_key"]["id"])
        other_headers = {"Authorization": f"Bearer {other.json()['secret']}"}
        cross_app = await e2e_client.get(result_url, headers=other_headers)
        assert cross_app.status_code == 404, cross_app.text
        native_cross_app = await e2e_client.get(response.json()["result_url"], headers=other_headers)
        assert native_cross_app.status_code == 404, native_cross_app.text

        collision_prefix = f"ci-{uuid.uuid4().hex[:8]}"
        collision_results = []
        for app_id, idempotency_key in (
            (f"{collision_prefix}:new-session:b", "c"),
            (collision_prefix, "b:new-session:c"),
        ):
            app_key = await e2e_client.post(
                "/api/user/apikey/",
                headers=e2e_headers,
                json={
                    "request_id": str(uuid.uuid4()),
                    "name": "Public ID collision E2E",
                    "access_level": "agents",
                    "app_id": app_id,
                },
            )
            assert app_key.status_code == 200, app_key.text
            key_ids.append(app_key.json()["api_key"]["id"])
            submitted = await e2e_client.post(
                "/api/v1/agents/threads",
                headers={
                    "Authorization": f"Bearer {app_key.json()['secret']}",
                    "Idempotency-Key": idempotency_key,
                },
                json=body,
            )
            assert submitted.status_code == 200, submitted.text
            collision_thread_ids.append(submitted.json()["thread_id"])
            collision_request_ids.append(submitted.json()["request_id"])
            collision_results.append(
                (submitted.json()["result_url"], {"Authorization": f"Bearer {app_key.json()['secret']}"})
            )
        assert len(set(collision_thread_ids)) == len(set(collision_request_ids)) == 2
        for result_url, headers in collision_results:
            for _ in range(150):
                collision_result = await e2e_client.get(result_url, headers=headers)
                assert collision_result.status_code == 200, collision_result.text
                if collision_result.json()["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("Colliding legacy identities did not reach independent terminal results")
            assert collision_result.json()["status"] == "completed", collision_result.text
            assert collision_result.json()["output"] == EXPECTED_OUTPUT
    finally:
        if second_run_id:
            await cancel_run(e2e_client, e2e_headers, second_run_id)
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        for request_id in collision_request_ids:
            cancelled = await e2e_client.post(f"/api/agent/requests/{request_id}/cancel", headers=e2e_headers)
            if cancelled.status_code == 409:
                await cancel_run(e2e_client, e2e_headers, cancelled.json()["detail"]["run_id"])
            else:
                assert cancelled.status_code == 200, cancelled.text
        if session_id:
            await e2e_client.delete(f"/api/chat/thread/{session_id}", headers=e2e_headers)
        for thread_id in collision_thread_ids:
            await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
        for key_id in key_ids:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


async def test_public_session_create_stream_survives_disconnect_and_replay(e2e_client, e2e_headers):
    """创建流贯穿首轮执行，断线重放仍绑定同一持久 Turn。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    await _create_provider(e2e_client, e2e_headers)
    token = str(uuid.uuid4())
    agent_slug = key_id = session_id = run_id = streamed_session_id = streamed_run_id = empty_session_id = None
    corrected_session_id = corrected_run_id = legacy_empty_session_id = None
    try:
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            str(me.json()["uid"]),
            system_prompt_suffix=f"{BLOCK_BEFORE_RESPONSE_MARKER}:{token}",
        )
        key = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Public session stream E2E",
                "access_level": "agents",
                "app_id": "ci-session-stream",
            },
        )
        assert key.status_code == 200, key.text
        key_id = key.json()["api_key"]["id"]
        headers = {
            "Authorization": f"Bearer {key.json()['secret']}",
            "Idempotency-Key": f"session-stream-{uuid.uuid4().hex}",
        }
        body = {
            "agent_id": agent_slug,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": f"只输出 {EXPECTED_OUTPUT}"}]}],
        }
        bad_key = f"session-invalid-{uuid.uuid4().hex}"
        bad_headers = {**headers, "Idempotency-Key": bad_key}
        invalid_config = await e2e_client.post(
            "/api/v1/agents/sessions", headers=bad_headers,
            json={**body, "tool_approval_mode": "invalid-mode"},
        )
        assert invalid_config.status_code == 422, invalid_config.text
        invalid_input = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers=bad_headers,
            json={
                "agent_id": agent_slug,
                "input": [{"role": "user", "content": [
                    {"type": "input_image", "image_url": "https://example.invalid/image.png"}
                ]}],
            },
        )
        assert invalid_input.status_code == 422, invalid_input.text
        creation_id = hash_id(
            "pubsess_",
            json.dumps(
                [str(me.json()["uid"]), "ci-session-stream", "new-session", bad_key],
                separators=(",", ":"),
            ),
            length=64,
        )
        pg = await asyncpg.connect(postgres_dsn())
        try:
            assert await pg.fetchval(
                "SELECT count(*) FROM conversations WHERE creation_request_id = $1", creation_id
            ) == 0
        finally:
            await pg.close()
        corrected = await e2e_client.post("/api/v1/agents/sessions", headers=bad_headers, json=body)
        assert corrected.status_code == 200, corrected.text
        corrected_session_id = corrected.json()["id"]
        corrected_run_id = corrected.json()["run_id"]
        invalid = await e2e_client.post("/api/v1/agents/sessions", headers=headers, json={**body, "stream": "yes"})
        assert invalid.status_code == 422, invalid.text

        async with e2e_client.stream(
            "POST", "/api/v1/agents/sessions", headers=headers, json={**body, "stream": True}
        ) as stream:
            assert stream.status_code == 200, stream.text
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers["X-App-Id"] == "ci-session-stream"
            lines = stream.aiter_lines()
            assert await anext(lines) == "event: session_created"
            created = json.loads((await anext(lines)).removeprefix("data: "))
            session_id = created["id"]
            turn_id = created["turn_id"]
            assert created["status"] == "in_progress"
            assert created["events_url"] == f"/api/v1/agents/sessions/{session_id}/events?turn_id={turn_id}"
            await _wait_for_blocking_replay(token)
            active = await e2e_client.get(created["result_url"], headers=headers)
            assert active.status_code == 200, active.text
            assert active.json()["status"] == "in_progress"
            run_id = active.json()["run_id"]
            assert run_id

        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            released = await replay.get("/release-blocking", params={"token": token})
            assert released.status_code == 200, released.text

        result = None
        for _ in range(150):
            response = await e2e_client.get(created["result_url"], headers=headers)
            assert response.status_code == 200, response.text
            result = response.json()
            if result["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        assert result is not None and result["status"] == "completed", result
        assert result["output"] == EXPECTED_OUTPUT
        assert result["run_id"] == run_id

        session = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}", headers=headers)
        assert session.status_code == 200, session.text
        assert session.json()["status"] == "idle"
        assert session.json()["turn_id"] == turn_id

        async with e2e_client.stream(
            "POST", "/api/v1/agents/sessions", headers=headers, json={**body, "stream": True}
        ) as replay:
            assert replay.status_code == 200, replay.text
            replay_body = (await replay.aread()).decode()
        assert "event: session_created" in replay_body
        assert "event: end" in replay_body
        assert json.loads(replay_body.split("data: ", 1)[1].split("\n", 1)[0])["turn_id"] == turn_id

        native = await e2e_client.post("/api/v1/agents/threads", headers=headers, json=body)
        assert native.status_code == 200, native.text
        assert native.json()["request_id"] == turn_id
        assert native.json()["run_id"] == run_id
        rejected = await e2e_client.post("/api/v1/agents/threads", headers=headers, json={**body, "stream": True})
        assert rejected.status_code == 422, rejected.text

        conn = await asyncpg.connect(postgres_dsn())
        try:
            assert await conn.fetchval("SELECT COUNT(*) FROM agent_run_requests WHERE request_id = $1", turn_id) == 1
            dispatched_run_id = await conn.fetchval(
                "SELECT dispatched_run_id FROM agent_run_requests WHERE request_id = $1", turn_id
            )
            assert dispatched_run_id == run_id
        finally:
            await conn.close()

        full_headers = {**headers, "Idempotency-Key": f"session-full-stream-{uuid.uuid4().hex}"}
        async with e2e_client.stream(
            "POST", "/api/v1/agents/sessions", headers=full_headers, json={**body, "stream": True}
        ) as full_stream:
            assert full_stream.status_code == 200, full_stream.text
            full_body = (await full_stream.aread()).decode()
        assert full_body.startswith("event: session_created\n")
        assert "event: end" in full_body
        full_created = json.loads(full_body.split("data: ", 1)[1].split("\n", 1)[0])
        streamed_session_id = full_created["id"]
        assert streamed_session_id != session_id
        full_result = await e2e_client.get(full_created["result_url"], headers=full_headers)
        assert full_result.status_code == 200, full_result.text
        assert full_result.json()["status"] == "completed"
        assert full_result.json()["output"] == EXPECTED_OUTPUT
        streamed_run_id = full_result.json()["run_id"]

        empty_key = f"product-empty-{uuid.uuid4().hex}"
        empty_headers = {**e2e_headers, "Idempotency-Key": empty_key}
        empty = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers=empty_headers,
            json={"agent_id": agent_slug, "title": "产品空对话"},
        )
        assert empty.status_code == 200, empty.text
        empty_session_id = empty.json()["id"]
        assert empty.json()["status"] == "idle"
        assert empty.json()["turn_id"] is None
        same_empty = await e2e_client.post(
            "/api/v1/agents/sessions", headers=empty_headers,
            json={"agent_id": agent_slug, "title": "产品空对话"},
        )
        assert same_empty.status_code == 200, same_empty.text
        assert same_empty.json()["id"] == empty_session_id
        for conflicting_body in (
            {"agent_id": agent_slug, "title": "另一标题"},
            {"agent_id": agent_slug, "title": "产品空对话", "model_spec": MODEL_SPEC},
            {"agent_id": agent_slug, "title": "产品空对话", "tool_approval_mode": "always_trust"},
            {**body, "title": "产品空对话"},
        ):
            conflict = await e2e_client.post(
                "/api/v1/agents/sessions", headers=empty_headers, json=conflicting_body
            )
            assert conflict.status_code == 409, conflict.text
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{empty_session_id}/events",
            headers={**e2e_headers, "Idempotency-Key": f"product-message-{uuid.uuid4().hex}"},
            json={"events": [{
                "type": "agent.session.input.message",
                "mode": "follow_up",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": EXPECTED_OUTPUT}]}],
            }]},
        )
        assert accepted.status_code == 202, accepted.text
        product_turn_id = accepted.json()["turn_id"]
        assert product_turn_id
        for _ in range(150):
            product_turn = await e2e_client.get(
                f"/api/v1/agents/sessions/{empty_session_id}/turns/{product_turn_id}", headers=e2e_headers
            )
            assert product_turn.status_code == 200, product_turn.text
            if product_turn.json()["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        assert product_turn.json()["status"] == "completed", product_turn.text
        assert product_turn.json()["output"] == EXPECTED_OUTPUT
        items = await e2e_client.get(
            f"/api/v1/agents/sessions/{empty_session_id}/turns/{product_turn_id}/items", headers=e2e_headers
        )
        assert items.status_code == 200, items.text
        assert {item["role"] for item in items.json()["data"]} == {"user", "assistant"}
        pg = await asyncpg.connect(postgres_dsn())
        try:
            conversation_id = await pg.fetchval(
                "SELECT id FROM conversations WHERE thread_id = $1", empty_session_id
            )
            await pg.execute(
                "INSERT INTO messages (conversation_id, role, content, message_type, run_id, delivery_status) "
                "VALUES ($1, 'assistant', 'INTERNAL_MODEL_AUDIT', 'model_audit', $2, 'complete')",
                conversation_id,
                product_turn.json()["run_id"],
            )
        finally:
            await pg.close()
        after_audit = await e2e_client.get(
            f"/api/v1/agents/sessions/{empty_session_id}/turns/{product_turn_id}/items", headers=e2e_headers
        )
        assert after_audit.status_code == 200, after_audit.text
        assert len(after_audit.json()["data"]) == len(items.json()["data"])
        assert "INTERNAL_MODEL_AUDIT" not in after_audit.text
        legacy_key = f"legacy-empty-{uuid.uuid4().hex}"
        legacy_creation_id = hash_id(
            "pubsess_",
            json.dumps([str(me.json()["uid"]), None, "new-session", legacy_key], separators=(",", ":")),
            length=64,
        )
        legacy_created = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={
                "request_id": legacy_creation_id,
                "agent_id": agent_slug,
                "title": "旧空会话",
                "metadata": {"source": "public_api", "channel": "web"},
            },
        )
        assert legacy_created.status_code == 200, legacy_created.text
        legacy_empty_session_id = legacy_created.json()["id"]
        legacy_headers = {**e2e_headers, "Idempotency-Key": legacy_key}
        legacy_replay = await e2e_client.post(
            "/api/v1/agents/sessions", headers=legacy_headers,
            json={"agent_id": agent_slug, "title": "旧空会话"},
        )
        assert legacy_replay.status_code == 200, legacy_replay.text
        assert legacy_replay.json()["id"] == legacy_empty_session_id
        legacy_conflict = await e2e_client.post(
            "/api/v1/agents/sessions", headers=legacy_headers,
            json={"agent_id": agent_slug, "title": "更改后的标题"},
        )
        assert legacy_conflict.status_code == 409, legacy_conflict.text
    finally:
        if corrected_run_id:
            await cancel_run(e2e_client, e2e_headers, corrected_run_id)
        if corrected_session_id:
            await e2e_client.delete(f"/api/chat/thread/{corrected_session_id}", headers=e2e_headers)
        if legacy_empty_session_id:
            await e2e_client.delete(f"/api/chat/thread/{legacy_empty_session_id}", headers=e2e_headers)
        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            await replay.get("/release-blocking", params={"token": token})
        if streamed_run_id:
            await cancel_run(e2e_client, e2e_headers, streamed_run_id)
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if streamed_session_id:
            await e2e_client.delete(f"/api/chat/thread/{streamed_session_id}", headers=e2e_headers)
        if session_id:
            await e2e_client.delete(f"/api/chat/thread/{session_id}", headers=e2e_headers)
        if empty_session_id:
            await e2e_client.delete(f"/api/chat/thread/{empty_session_id}", headers=e2e_headers)
        if key_id:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


async def test_public_session_status_keeps_earlier_queued_request_visible(e2e_client, e2e_headers):
    """最新 Request 已取消时，较早排队请求仍使 Session 处于进行中。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    await _create_provider(e2e_client, e2e_headers)
    token = str(uuid.uuid4())
    agent_slug = key_id = session_id = run_id = queued_request_id = None
    try:
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            str(me.json()["uid"]),
            system_prompt_suffix=f"{BLOCK_BEFORE_RESPONSE_MARKER}:{token}",
        )
        key = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Public session queued status E2E",
                "access_level": "agents",
                "app_id": "ci-session-queued-status",
            },
        )
        assert key.status_code == 200, key.text
        key_id = key.json()["api_key"]["id"]
        headers = {"Authorization": f"Bearer {key.json()['secret']}"}
        first = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": f"first-{uuid.uuid4().hex}"},
            json={
                "agent_id": agent_slug,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "等待模型响应"}]}],
            },
        )
        assert first.status_code == 200, first.text
        session_id = first.json()["id"]
        for _ in range(50):
            current = await e2e_client.get(first.json()["result_url"], headers=headers)
            assert current.status_code == 200, current.text
            run_id = current.json()["run_id"]
            if run_id:
                break
            await asyncio.sleep(0.1)
        assert run_id
        await _wait_for_blocking_replay(token)

        requests = []
        for label in ("second", "third"):
            submitted = await e2e_client.post(
                f"/api/v1/agents/threads/{session_id}/requests",
                headers={**headers, "Idempotency-Key": f"{label}-{uuid.uuid4().hex}"},
                json={"input": [{"role": "user", "content": [{"type": "input_text", "text": label}]}]},
            )
            assert submitted.status_code == 202, submitted.text
            assert submitted.json()["status"] == "queued"
            requests.append(submitted.json()["request_id"])
        queued_request_id, latest_request_id = requests
        cancelled = await e2e_client.post(
            f"/api/agent/requests/{latest_request_id}/cancel", headers=e2e_headers
        )
        assert cancelled.status_code == 200, cancelled.text
        cancel_headers = {**headers, "Idempotency-Key": f"cancel-{uuid.uuid4().hex}"}
        cancel_payload = {"events": [{"type": "agent.session.input.cancel"}]}
        accepted_cancel = await e2e_client.post(
            f"/api/v1/agents/sessions/{session_id}/events",
            headers=cancel_headers,
            json=cancel_payload,
        )
        assert accepted_cancel.status_code == 202, accepted_cancel.text
        assert accepted_cancel.json()["turn_id"] == first.json()["turn_id"]
        assert accepted_cancel.json()["run_id"] == run_id
        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            released = await replay.get("/release-blocking", params={"token": token})
            assert released.status_code == 200, released.text
        run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "cancelled", run

        conn = await asyncpg.connect(postgres_dsn())
        try:
            rows = await conn.fetch(
                "SELECT request_id, status FROM agent_run_requests WHERE request_id = ANY($1::text[])",
                requests,
            )
            assert {row["request_id"]: row["status"] for row in rows} == {
                queued_request_id: "queued",
                latest_request_id: "cancelled",
            }
        finally:
            await conn.close()
        session = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}", headers=headers)
        assert session.status_code == 200, session.text
        assert session.json()["turn_id"] == queued_request_id
        assert session.json()["status"] == "in_progress"
        replayed_cancel = await e2e_client.post(
            f"/api/v1/agents/sessions/{session_id}/events",
            headers=cancel_headers,
            json=cancel_payload,
        )
        assert replayed_cancel.status_code == 202, replayed_cancel.text
        assert replayed_cancel.json() == accepted_cancel.json()
    finally:
        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            await replay.get("/release-blocking", params={"token": token})
        if queued_request_id:
            await e2e_client.post(f"/api/agent/requests/{queued_request_id}/cancel", headers=e2e_headers)
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if session_id:
            await e2e_client.delete(f"/api/chat/thread/{session_id}", headers=e2e_headers)
        if key_id:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


async def test_public_end_user_private_agent_run_keeps_own_uid(e2e_client, e2e_headers):
    """私有 Agent 由 Key 用户授权，真实 worker 仍把结果绑定终端用户。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    owner_id, owner_uid = me.json()["id"], str(me.json()["uid"])
    await _create_provider(e2e_client, e2e_headers)
    app_id = f"ci-end-user-{uuid.uuid4().hex[:12]}"
    end_user_id = f"visitor-{uuid.uuid4().hex}"
    other_end_user_id = f"other-{uuid.uuid4().hex}"
    agent_slug = None
    key_id = None
    thread_id = None
    conn = await asyncpg.connect(postgres_dsn())
    try:
        agent_slug = await _create_agent(e2e_client, e2e_headers, owner_uid)
        key = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Public end user E2E",
                "access_level": "agents",
                "app_id": app_id,
            },
        )
        assert key.status_code == 200, key.text
        key_id = key.json()["api_key"]["id"]
        public_headers = {
            "Authorization": f"Bearer {key.json()['secret']}",
            "X-End-User-Id": end_user_id,
            "Idempotency-Key": f"end-user-first-{uuid.uuid4().hex}",
        }
        visible = await e2e_client.get(f"/api/v1/agents/{agent_slug}", headers=public_headers)
        assert visible.status_code == 200, visible.text
        body = {
            "agent_id": agent_slug,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": f"只输出 {EXPECTED_OUTPUT}"}]}],
        }
        submitted = await e2e_client.post("/api/v1/agents/threads", headers=public_headers, json=body)
        assert submitted.status_code == 200, submitted.text
        receipt = submitted.json()
        thread_id, request_id = receipt["thread_id"], receipt["request_id"]

        for other_headers in (
            {"Authorization": public_headers["Authorization"]},
            {**public_headers, "X-End-User-Id": other_end_user_id},
        ):
            hidden = await e2e_client.get(f"/api/v1/agents/threads/{thread_id}", headers=other_headers)
            assert hidden.status_code == 404, hidden.text
            hidden_result = await e2e_client.get(receipt["result_url"], headers=other_headers)
            assert hidden_result.status_code == 404, hidden_result.text
            hidden_stream = await e2e_client.get(receipt["events_url"], headers=other_headers)
            assert hidden_stream.status_code == 404, hidden_stream.text

        replay = await e2e_client.post("/api/v1/agents/sessions", headers=public_headers, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == thread_id
        assert replay.json()["turn_id"] == request_id

        for _ in range(300):
            result = await e2e_client.get(receipt["result_url"], headers=public_headers)
            assert result.status_code == 200, result.text
            if result.json()["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Public end user Request did not reach a terminal status")
        assert result.json()["status"] == "completed", result.text
        assert result.json()["output"] == EXPECTED_OUTPUT
        assert result.json()["run_id"]

        persisted = await conn.fetchrow(
            """
            SELECT u.uid, u.user_kind, u.role, c.uid AS conversation_uid,
                   p.uid AS project_uid, p.workdir_path,
                   req.uid AS request_uid, run.uid AS run_uid, run.app_id AS run_app_id
            FROM users u
            JOIN conversations c ON c.uid = u.uid
            JOIN projects p ON p.id = c.project_id
            JOIN agent_run_requests req ON req.conversation_thread_id = c.thread_id
            JOIN agent_runs run ON run.id = req.dispatched_run_id
            WHERE c.thread_id = $1 AND req.request_id = $2
            """,
            thread_id,
            request_id,
        )
        assert persisted is not None
        assert persisted["uid"] != owner_uid
        assert persisted["user_kind"] == "end_user" and persisted["role"] == "user"
        assert persisted["uid"] == persisted["conversation_uid"] == persisted["project_uid"]
        assert persisted["uid"] == persisted["request_uid"] == persisted["run_uid"]
        assert persisted["run_app_id"] == app_id
        assert (user_workspace_dir(persisted["uid"]) / persisted["workdir_path"]).is_dir()
        assert not (user_workspace_dir(owner_uid) / persisted["workdir_path"]).exists()

        async with e2e_client.stream("GET", receipt["events_url"], headers=public_headers) as events:
            assert events.status_code == 200, events.text
            assert "event: end" in (await events.aread()).decode()
    finally:
        if thread_id:
            await conn.execute("UPDATE conversations SET status = 'deleted' WHERE thread_id = $1", thread_id)
        users = await conn.fetch(
            """
            UPDATE users SET is_deleted = 1, deleted_at = NOW()
            WHERE owner_user_id = $1 AND app_id = $2 AND end_user_id = ANY($3::varchar[])
            RETURNING uid
            """,
            owner_id,
            app_id,
            [end_user_id, other_end_user_id],
        )
        for user in users:
            shutil.rmtree(user_workspace_dir(user["uid"]), ignore_errors=True)
        await conn.close()
        if key_id:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_lifecycle
@pytest.mark.parametrize("endpoint", ["create", "request", "session"])
async def test_public_sse_releases_validation_transaction(e2e_client, e2e_headers, endpoint):
    """流仍在等待模型时，PostgreSQL 不保留入口的空闲事务。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    await _create_provider(e2e_client, e2e_headers)
    token = str(uuid.uuid4())
    agent_slug = key_id = session_id = run_id = None
    conn = await asyncpg.connect(postgres_dsn())
    try:
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            str(me.json()["uid"]),
            system_prompt_suffix=f"{BLOCK_BEFORE_RESPONSE_MARKER}:{token}",
        )
        key = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "SSE transaction E2E",
                "access_level": "agents",
                "app_id": "ci-sse-transaction",
            },
        )
        assert key.status_code == 200, key.text
        key_id = key.json()["api_key"]["id"]
        headers = {"Authorization": f"Bearer {key.json()['secret']}", "Idempotency-Key": token}
        body = {
            "agent_id": agent_slug,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": f"只输出 {EXPECTED_OUTPUT}"}]}],
        }
        # 非流式创建提供清理标识；create 分支重放同一持久请求，仍执行完整 Session 查询。
        created = await e2e_client.post("/api/v1/agents/sessions", headers=headers, json=body)
        assert created.status_code == 200, created.text
        receipt = created.json()
        session_id, turn_id = receipt["id"], receipt["turn_id"]
        await _wait_for_blocking_replay(token)
        active = await e2e_client.get(receipt["result_url"], headers=headers)
        assert active.status_code == 200, active.text
        run_id = active.json()["run_id"]
        assert run_id and active.json()["status"] == "in_progress"

        started = await conn.fetchval("SELECT clock_timestamp()")
        path = receipt["events_url"]
        kwargs = {}
        if endpoint == "request":
            path = f"/api/v1/agents/threads/{session_id}/requests/{turn_id}/events"
        elif endpoint == "create":
            path = "/api/v1/agents/sessions"
            kwargs["json"] = {**body, "stream": True}
        async with e2e_client.stream(
            "POST" if endpoint == "create" else "GET", path, headers=headers, **kwargs
        ) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            # 只拒绝贯穿观察窗口的事务，排除轮询和心跳在提交前的瞬时 idle。
            cutoff = await conn.fetchval("SELECT clock_timestamp()")
            await asyncio.sleep(0.5)
            idle = await conn.fetch(
                """SELECT pid, state FROM pg_stat_activity
                   WHERE datname = current_database() AND xact_start >= $1 AND xact_start <= $2
                     AND state = 'idle in transaction'
                     AND query ~ '(agent_run_requests|agent_runs)'""",
                started,
                cutoff,
            )
            assert not idle, f"{endpoint} SSE retains validation transactions: {idle}"
            # 同时验证流未结束时普通数据库请求仍能取得当前状态。
            active = await e2e_client.get(receipt["result_url"], headers=headers)
            assert active.status_code == 200, active.text
            assert active.json()["status"] == "in_progress"

        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            released = await replay.get("/release-blocking", params={"token": token})
            assert released.status_code == 200, released.text
        result = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert result["status"] == "completed", result
        output = await e2e_client.get(receipt["result_url"], headers=headers)
        assert output.status_code == 200, output.text
        assert output.json()["output"] == EXPECTED_OUTPUT
        assert output.json()["run_id"] == run_id
    finally:
        await conn.close()
        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            await replay.get("/release-blocking", params={"token": token})
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if session_id:
            await e2e_client.delete(f"/api/chat/thread/{session_id}", headers=e2e_headers)
        if key_id:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.parametrize("native", [False, True])
async def test_public_agents_queued_sse_keeps_public_run_url(e2e_client, e2e_headers, native):
    """排队流交接到唯一 Run 时只提供当前 Public API 可访问的地址。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    await _create_provider(e2e_client, e2e_headers)
    agent_slug = None
    session_id = None
    first_run_id = None
    second_run_id = None
    key_id = None
    stream_task = None
    token = None
    try:
        token = str(uuid.uuid4())
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            str(me.json()["uid"]),
            system_prompt_suffix=f"{BLOCK_BEFORE_RESPONSE_MARKER}:{token}",
        )
        created = await e2e_client.post(
            "/api/user/apikey/",
            headers=e2e_headers,
            json={
                "request_id": str(uuid.uuid4()),
                "name": "Public queued SSE E2E",
                "access_level": "agents",
                "app_id": "ci-public-queue",
            },
        )
        assert created.status_code == 200, created.text
        key_id = created.json()["api_key"]["id"]
        headers = {
            "Authorization": f"Bearer {created.json()['secret']}",
            "Idempotency-Key": f"first-{uuid.uuid4().hex}",
        }
        first = await e2e_client.post(
            "/api/v1/agents/threads" if native else "/api/v1/agents/sessions",
            headers=headers,
            json={
                "agent_id": agent_slug,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "等待模型响应"}]}],
            },
        )
        assert first.status_code == 200, first.text
        session_id = first.json()["thread_id" if native else "id"]
        conn = await asyncpg.connect(postgres_dsn())
        try:
            assert (
                await conn.fetchval("SELECT app_id FROM conversations WHERE thread_id = $1", session_id)
                == "ci-public-queue"
            )
        finally:
            await conn.close()
        first_run_id = first.json()["run_id"]
        for _ in range(50):
            if first_run_id:
                break
            first_result = await e2e_client.get(
                (
                    f"/api/v1/agents/threads/{session_id}/requests/{first.json()['request_id']}"
                    if native
                    else f"/api/v1/agents/sessions/{session_id}/turns/{first.json()['turn_id']}"
                ),
                headers=headers,
            )
            assert first_result.status_code == 200, first_result.text
            first_run_id = first_result.json()["run_id"]
            await asyncio.sleep(0.1)
        assert first_run_id
        await _wait_for_blocking_replay(token)

        second = await e2e_client.post(
            (
                f"/api/v1/agents/threads/{session_id}/requests"
                if native
                else f"/api/v1/agents/sessions/{session_id}/events"
            ),
            headers={**headers, "Idempotency-Key": f"second-{uuid.uuid4().hex}"},
            json=(
                {"input": [{"role": "user", "content": [{"type": "input_text", "text": "继续"}]}]}
                if native
                else {
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "mode": "follow_up",
                            "input": [{"role": "user", "content": [{"type": "input_text", "text": "继续"}]}],
                        }
                    ]
                }
            ),
        )
        assert second.status_code == 202, second.text
        assert second.json()["status"] == "queued"
        opened = asyncio.Event()

        async def collect_events() -> tuple[dict, str]:
            """消费队列交接事件，并在 Run 建立后取消阻塞模型。"""
            nonlocal second_run_id
            observed = []
            run_created = None
            event_name = ""
            async with e2e_client.stream(
                "GET",
                second.json()["events_url"],
                headers=headers,
            ) as events:
                assert events.status_code == 200, events.text
                opened.set()
                async for line in events.aiter_lines():
                    observed.append(line)
                    if line.startswith("event: "):
                        event_name = line[7:]
                    elif line.startswith("data: ") and event_name == "run_created":
                        run_created = json.loads(line[6:])
                        second_run_id = run_created["run_id"]
                        await cancel_run(e2e_client, e2e_headers, second_run_id)
                    elif line == "" and event_name == "end":
                        break
            return run_created, "\n".join(observed)

        stream_task = asyncio.create_task(collect_events())
        await asyncio.wait_for(opened.wait(), timeout=10)
        # 取消首个 Run 会按队列契约暂停后续请求；等待正常完成才能验证自动交接。
        async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
            released = await replay.get("/release-blocking", params={"token": token})
            assert released.status_code == 200, released.text
        run_created, stream_body = await asyncio.wait_for(stream_task, timeout=30)
        assert run_created is not None, stream_body
        assert run_created["stream_url"] == second.json()["events_url"]
        assert "/api/agent/runs/" not in stream_body
        assert "event: end" in stream_body
    finally:
        if token:
            async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as replay:
                await replay.get("/release-blocking", params={"token": token})
        if stream_task and not stream_task.done():
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        for target in (second_run_id, first_run_id):
            if target:
                await cancel_run(e2e_client, e2e_headers, target)
        if session_id:
            await e2e_client.delete(f"/api/chat/thread/{session_id}", headers=e2e_headers)
        if key_id:
            await e2e_client.delete(f"/api/user/apikey/{key_id}", headers=e2e_headers)
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_lifecycle
@pytest.mark.parametrize(("subagent", "first_call"), [(False, True), (True, False)])
async def test_model_retry_exhaustion_preserves_failure_and_parent_recovers(
    e2e_client, e2e_headers, subagent, first_call
):
    """真实 429 耗尽后保留失败原因，父任务可消费失败且线程仍可继续。"""
    uid = str((await e2e_client.get("/api/auth/me", headers=e2e_headers)).json()["uid"])
    await _create_provider(e2e_client, e2e_headers)
    agents, child_threads, run_ids = [], [], []
    thread_id = None
    marker = "DETERMINISTIC_RATE_LIMIT"
    query = f"{EXPECTED_OUTPUT} {marker} SUBAGENT_PATH:/tmp/not-written"
    if first_call:
        query += " RATE_LIMIT_FIRST_CALL"
    try:
        child = None
        if subagent:
            child = await _create_agent(
                e2e_client, e2e_headers, uid, is_subagent=True, system_prompt_suffix="DETERMINISTIC_SUBAGENT_CHILD"
            )
            agents.append(child)
        agent = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            subagents=[child] if child else [],
            system_prompt_suffix=f"DETERMINISTIC_SUBAGENT_PARENT:{child}" if child else "",
        )
        agents.append(agent)
        response = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={
                "agent_id": agent,
                "title": make_test_conversation_title("model-retry-failure"),
                "metadata": make_test_conversation_metadata("model-retry-failure", e2e=True),
            },
        )
        assert response.status_code == 200, response.text
        thread_id = response.json()["id"]
        # 同一线程连续提交两次，第二次证明上一次失败没有遗留清理或队列阻塞。
        for _ in range(2 if not subagent else 1):
            response = await e2e_client.post(
                "/api/agent/runs",
                headers=e2e_headers,
                json={
                    "agent_slug": agent,
                    "thread_id": thread_id,
                    "query": query,
                    "tool_approval_mode": "default",
                    "meta": {"request_id": str(uuid.uuid4())},
                },
            )
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            run_ids.append(run_id)
            final = await wait_for_run(e2e_client, e2e_headers, run_id)
            assert final["status"] == ("completed" if subagent else "failed"), final
            failed_id = run_id
            conn = await asyncpg.connect(postgres_dsn())
            try:
                if subagent:
                    children = await conn.fetch(
                        "SELECT id, conversation_thread_id FROM agent_runs WHERE created_by_run_id = $1", run_id
                    )
                    assert len(children) == 1, children
                    failed_id = children[0]["id"]
                    child_threads.append(children[0]["conversation_thread_id"])
                    tool_content = await conn.fetchval(
                        "SELECT content FROM messages WHERE run_id = $1 AND message_type = 'tool_audit' "
                        "AND operation_id = 'await-call-subagent-start'",
                        run_id,
                    )
                    observed = json.loads(tool_content)
                    assert observed["status"] == "failed", observed
                    assert marker in observed["result"]["error"]["message"], observed
                    parent_result = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
                    assert parent_result.json()["output"] == EXPECTED_OUTPUT, parent_result.text
                failed = await conn.fetchrow(
                    "SELECT status, error_message, output_message_id FROM agent_runs WHERE id = $1", failed_id
                )
                assert failed["status"] == "failed", failed
                assert marker in failed["error_message"], failed
                assert "Model lifecycle" not in failed["error_message"], failed
                assert await conn.fetchval("SELECT COUNT(*) FROM agent_run_attempts WHERE run_id = $1", failed_id) == 1
                # 失败通道允许保存同 Run 的部分输出，但必须携带明确错误元数据。
                output = await conn.fetchrow(
                    "SELECT run_id, content, extra_metadata FROM messages WHERE id = $1",
                    failed["output_message_id"],
                )
                assert output["run_id"] == failed_id, output
                metadata = json.loads(output["extra_metadata"])
                assert metadata["is_error"] is True, metadata
                assert marker in metadata["error_message"], metadata
                assert "Model call failed after" not in output["content"], output
            finally:
                await conn.close()
            result = await e2e_client.get(f"/api/agent/runs/{failed_id}/result", headers=e2e_headers)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "failed", result.text
            assert result.json()["output"] == "", result.text
            assert marker in result.json()["error"]["message"], result.text
            async with e2e_client.stream("GET", f"/api/agent/runs/{failed_id}/events", headers=e2e_headers) as events:
                assert events.status_code == 200
                body = (await events.aread()).decode()
                assert "event: end" in body and '"failed"' in body
            await _wait_for_runtime_cleanup(failed_id)
            await _wait_for_runtime_cleanup(run_id)
    finally:
        for run_id in run_ids:
            await cancel_run(e2e_client, e2e_headers, run_id)
        for target in [*child_threads, thread_id]:
            if target:
                await e2e_client.delete(f"/api/chat/thread/{target}", headers=e2e_headers)
        for slug in reversed(agents):
            await delete_agent(e2e_client, e2e_headers, slug)
        await _delete_provider(e2e_client, e2e_headers)


async def _create_provider(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post(
        "/api/system/model-providers",
        json={
            "provider_id": PROVIDER_ID,
            "display_name": "CI deterministic replay",
            "provider_type": "openai",
            "base_url": "http://api:8765/v1",
            "api_key": "ci-replay-key",
            "capabilities": ["chat"],
            "enabled_models": [
                {
                    "id": "deterministic-chat",
                    "display_name": "Deterministic chat",
                    "type": "chat",
                    "source": "manual",
                }
            ],
            "is_enabled": True,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["provider_id"] == PROVIDER_ID


async def _delete_provider(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.delete(f"/api/system/model-providers/{PROVIDER_ID}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def _wait_for_blocking_replay(token: str) -> None:
    """等待 replay 确认本次模型请求已开始但尚未返回任何消息。"""
    async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as client:
        for _ in range(300):
            response = await client.get("/blocking-started", params={"token": token})
            assert response.status_code == 200, response.text
            if response.json().get("started") is True:
                return
            await asyncio.sleep(0.1)
    pytest.fail("deterministic replay did not observe blocking model request")


async def _wait_for_running_model_audit(run_id: str) -> None:
    """回读 PG，证明取消发生前 Model running 事实已经提交。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        for _ in range(100):
            status = await conn.fetchval(
                """
                SELECT execution_status
                FROM messages
                WHERE run_id = $1 AND message_type = 'model_audit'
                ORDER BY sequence
                LIMIT 1
                """,
                run_id,
            )
            if status == "running":
                return
            await asyncio.sleep(0.1)
    finally:
        await conn.close()
    pytest.fail("running Model audit was not committed before cancellation")


async def _wait_for_runtime_cleanup(run_id: str) -> None:
    """等待终态 Run 释放 runtime ownership 后再创建 resume。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        for _ in range(100):
            cleanup_pending = await conn.fetchval(
                "SELECT runtime_cleanup_pending FROM agent_runs WHERE id = $1",
                run_id,
            )
            if cleanup_pending is False:
                return
            await asyncio.sleep(0.1)
    finally:
        await conn.close()
    pytest.fail("terminal Run did not finish runtime cleanup")


async def _run_deterministic(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_slug: str,
    thread_id: str,
    attachment_file_ids: list[str] | None = None,
) -> dict:
    """提交无外部模型依赖的真实 worker Run 并等待终态。"""
    request_id = f"deterministic-hydrate-{uuid.uuid4()}"
    response = await client.post(
        "/api/agent/runs",
        json={
            "query": f"只输出 {EXPECTED_OUTPUT}",
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "meta": {
                "request_id": request_id,
                "attachment_file_ids": attachment_file_ids or [],
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    run = await wait_for_run(client, headers, str(response.json()["run_id"]))
    assert run["status"] == "completed", run
    return run


async def _create_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    uid: str,
    *,
    system_prompt_suffix: str = "",
    is_subagent: bool = False,
    subagents: list[str] | None = None,
) -> str:
    slug = f"ci-deterministic-{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/api/agent",
        json={
            "name": f"Deterministic E2E {slug[-8:]}",
            "slug": slug,
            "backend_id": "SubAgentBackend" if is_subagent else "ChatbotAgent",
            "is_subagent": is_subagent,
            "description": "无外部密钥的 assembled-path 测试智能体",
            "config_json": {
                "context": {
                    "model": "" if is_subagent else MODEL_SPEC,
                    "system_prompt": f"不要调用工具，只输出 {EXPECTED_OUTPUT}。{system_prompt_suffix}",
                    "tools": [],
                    "knowledges": [],
                    "mcps": [],
                    "skills": ["image-gen"],
                    "preload_skills": ["image-gen"],
                    "subagents": subagents or [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [uid],
                },
                "manage_scope": None,
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent"]["slug"] == slug
    return slug


@pytest.mark.parametrize("mode", ["default", "always_trust"])
@pytest.mark.e2e_boundaries
async def test_subagent_worker_enforces_inherited_write_policy(e2e_client, e2e_headers, mode):
    """真实父子 Run 继承审批模式，回读工具审计与共享 Workdir 文件。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    await _create_provider(e2e_client, e2e_headers)
    agents = []
    thread_id = child_thread_id = run_id = workdir_path = probe_path = None
    try:
        child_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            is_subagent=True,
            system_prompt_suffix="DETERMINISTIC_SUBAGENT_CHILD",
        )
        agents.append(child_slug)
        parent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            subagents=[child_slug],
            system_prompt_suffix=f"DETERMINISTIC_SUBAGENT_PARENT:{child_slug}",
        )
        agents.append(parent_slug)
        response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": parent_slug,
                "title": make_test_conversation_title("subagent-policy"),
                "metadata": make_test_conversation_metadata("subagent-policy", e2e=True),
            },
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text
        thread_id = str(response.json()["id"])
        workdir_path = str(response.json()["workdir_path"])
        file_name = f"subagent-policy-{uuid.uuid4().hex}.txt"
        path = f"/home/gem/user-data/{workdir_path}/{file_name}"
        probe_path = user_workspace_dir(uid) / workdir_path / file_name
        response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "agent_slug": parent_slug,
                "thread_id": thread_id,
                "query": f"{EXPECTED_OUTPUT} SUBAGENT_MODE:{mode} SUBAGENT_PATH:{path}",
                "tool_approval_mode": mode,
                "meta": {"request_id": f"subagent-policy-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text
        run_id = str(response.json()["run_id"])
        parent = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert parent["status"] == "completed", parent

        conn = await asyncpg.connect(postgres_dsn())
        try:
            children = await conn.fetch(
                """
                SELECT run.id, run.status, run.runtime_scope_id, run.input_payload,
                       conversation.thread_id, run.manifest
                FROM agent_runs run JOIN conversations conversation ON conversation.id = run.conversation_id
                WHERE run.created_by_run_id = $1 AND run.run_type = 'subagent'
                """,
                run_id,
            )
            assert len(children) == 1, children
            child = children[0]
            child_thread_id = str(child["thread_id"])
            assert child["status"] == "completed", dict(child)
            await _assert_single_persisted_input(run_id)
            await _assert_single_persisted_input(str(child["id"]))
            assert child["runtime_scope_id"] == thread_id
            payload = json.loads(child["input_payload"])
            assert payload["tool_approval_mode"] == mode
            assert payload["model_spec"] == MODEL_SPEC
            assert json.loads(child["manifest"])["model"]["spec"] == MODEL_SPEC
            audit = await conn.fetchrow(
                """
                SELECT execution_status, content FROM messages
                WHERE run_id = $1 AND message_type = 'tool_audit' AND operation_id = 'call-subagent-write'
                """,
                child["id"],
            )
        finally:
            await conn.close()

        state = await e2e_client.get(
            f"/api/chat/thread/{child_thread_id}/state", params={"include_messages": "true"}, headers=e2e_headers
        )
        assert state.status_code == 200, state.text
        assert state.json()["subagent_run"]["run_id"] == child["id"]
        results = [
            message for message in state.json()["messages"] if message.get("tool_call_id") == "call-subagent-write"
        ]
        assert len(results) == 1, state.json()["messages"]
        assert results[0]["status"] == ("error" if mode == "default" else "success")
        assert probe_path.parent.is_dir(), probe_path
        if mode == "default":
            assert "不可用" in results[0]["content"]
            assert not probe_path.exists(), "被拒绝的子智能体调用不能写入共享 Workdir"
        else:
            assert audit and audit["execution_status"] == "completed", audit
            assert probe_path.read_text(encoding="utf-8") == "subagent write verified"
    finally:
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if probe_path:
            probe_path.unlink(missing_ok=True)
        if thread_id:
            get_sandbox_provider().release(thread_id, uid=uid, workdir_path=workdir_path)
        for cleanup_thread_id in (child_thread_id, thread_id):
            if cleanup_thread_id:
                response = await e2e_client.delete(f"/api/chat/thread/{cleanup_thread_id}", headers=e2e_headers)
                assert response.status_code in {200, 404}, response.text
        for slug in reversed(agents):
            await delete_agent(e2e_client, e2e_headers, slug)
        await _delete_provider(e2e_client, e2e_headers)


async def _assert_single_persisted_input(run_id: str) -> None:
    """回读同请求的全部用户消息，证明 worker 未重复保存输入。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT message.id, message.run_id, message.request_id, run.input_message_id,
                   run.request_id AS expected_request_id, request.input_message_id AS request_input_id
            FROM agent_runs run
            LEFT JOIN agent_run_requests request ON request.request_id = run.request_id
            JOIN messages message ON message.conversation_id = run.conversation_id
                AND message.role = 'user'
                AND (message.request_id = run.request_id
                     OR message.extra_metadata->>'request_id' = run.request_id)
            WHERE run.id = $1
            """,
            run_id,
        )
        assert len(rows) == 1, [dict(row) for row in rows]
        row = rows[0]
        assert row["id"] == row["input_message_id"]
        assert row["run_id"] == run_id
        assert row["request_id"] == row["expected_request_id"]
        if row["request_input_id"] is not None:
            assert row["request_input_id"] == row["id"]
    finally:
        await conn.close()


async def _assert_persisted_causality(run_id: str, request_id: str) -> None:
    await _assert_single_persisted_input(run_id)
    conn = await asyncpg.connect(postgres_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT ar.status, ar.request_id, ar.output_message_id, ar.langfuse_trace_id,
                   message.run_id AS output_run_id,
                   message.request_id AS output_request_id,
                   message.content AS output_content,
                   message.extra_metadata->>'langfuse_trace_id' AS output_trace_id
            FROM agent_runs ar
            LEFT JOIN messages message ON message.id = ar.output_message_id
            WHERE ar.id = $1
            """,
            run_id,
        )
        assert row, f"agent_runs row missing for {run_id}"
        assert row["status"] == "completed"
        assert row["request_id"] == request_id
        assert row["output_message_id"] is not None
        assert row["output_run_id"] == run_id
        assert row["output_request_id"] == request_id
        assert row["output_content"] == EXPECTED_OUTPUT
        assert row["langfuse_trace_id"] == row["output_trace_id"]
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            assert row["langfuse_trace_id"]

        model_audits = await conn.fetch(
            """
            SELECT id, message_type, operation_id, sequence, execution_status,
                   started_at, finished_at, duration_ms, usage
            FROM messages
            WHERE run_id = $1 AND operation_id IS NOT NULL AND role = 'assistant'
            ORDER BY sequence
            """,
            run_id,
        )
        assert len(model_audits) == 2
        assert [item["execution_status"] for item in model_audits] == ["completed", "completed"]
        assert model_audits[0]["message_type"] == "model_audit"
        assert model_audits[1]["id"] == row["output_message_id"]
        assert model_audits[1]["message_type"] == "text"
        assert model_audits[0]["sequence"] < model_audits[1]["sequence"]
        assert all(item["operation_id"] for item in model_audits)
        assert all(item["started_at"] and item["finished_at"] for item in model_audits)
        assert all(item["duration_ms"] is not None and item["duration_ms"] >= 0 for item in model_audits)
        assert all(item["usage"] for item in model_audits)

        tool_audit = await conn.fetchrow(
            """
            SELECT operation_id, sequence, execution_status, started_at, finished_at, duration_ms,
                   content, usage, extra_metadata
            FROM messages
            WHERE run_id = $1 AND message_type = 'tool_audit' AND role = 'tool'
            """,
            run_id,
        )
        assert tool_audit
        assert tool_audit["operation_id"] == EXPECTED_TOOL_CALL_ID
        assert model_audits[0]["sequence"] < tool_audit["sequence"] < model_audits[1]["sequence"]
        assert tool_audit["execution_status"] == "completed"
        assert tool_audit["started_at"] and tool_audit["finished_at"]
        assert tool_audit["duration_ms"] is not None and tool_audit["duration_ms"] >= 0
        assert tool_audit["content"] and EXPECTED_TOOL_RESULT_MARKER in tool_audit["content"]
        assert tool_audit["usage"] is None
        raw_tool_metadata = tool_audit["extra_metadata"]
        tool_metadata = json.loads(raw_tool_metadata) if isinstance(raw_tool_metadata, str) else raw_tool_metadata
        assert tool_metadata["tool_name"] == EXPECTED_PRELOADED_TOOL
        assert tool_metadata["input"] == {"filepaths": []}
        assert tool_metadata["source_model_operation_id"] == model_audits[0]["operation_id"]

        tool_call = await conn.fetchrow(
            """
            SELECT tc.langgraph_tool_call_id, tc.tool_name, tc.status, tc.tool_output,
                   message.operation_id AS source_model_operation_id
            FROM tool_calls tc
            JOIN messages message ON message.id = tc.message_id
            WHERE message.run_id = $1
            """,
            run_id,
        )
        if not tool_call:
            persisted_messages = await conn.fetch(
                """
                SELECT message.id, message.message_type, message.operation_id,
                       message.extra_metadata, count(tool_call.id) AS tool_call_count
                FROM messages message
                LEFT JOIN tool_calls tool_call ON tool_call.message_id = message.id
                WHERE message.run_id = $1
                GROUP BY message.id
                ORDER BY message.sequence NULLS LAST, message.id
                """,
                run_id,
            )
            pytest.fail(f"预加载工具未持久化；Run messages={persisted_messages!r}")
        assert tool_call["langgraph_tool_call_id"] == EXPECTED_TOOL_CALL_ID
        assert tool_call["tool_name"] == EXPECTED_PRELOADED_TOOL
        assert tool_call["status"] == "success"
        assert tool_call["tool_output"]
        assert tool_call["source_model_operation_id"] == model_audits[0]["operation_id"]
    finally:
        await conn.close()


async def _assert_followup_run_does_not_rebind_prior_audits(
    *,
    first_run_id: str,
    second_run_id: str,
    second_request_id: str,
) -> None:
    """同线程后续 Run 不得把前一 Run 的隐藏 Model 行复制为自身输出。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        first_operations = await conn.fetch(
            "SELECT operation_id FROM messages WHERE run_id = $1 AND operation_id IS NOT NULL",
            first_run_id,
        )
        first_operation_ids = [row["operation_id"] for row in first_operations]
        row = await conn.fetchrow(
            """
            SELECT run.request_id, run.output_message_id,
                   count(message.id) FILTER (WHERE message.run_id = run.id AND message.operation_id IS NOT NULL)
                       AS second_audit_count,
                   count(message.id) FILTER (
                       WHERE message.run_id = run.id AND message.operation_id = ANY($2::varchar[])
                   ) AS rebound_count,
                   count(message.id) FILTER (
                       WHERE message.role = 'assistant' AND message.message_type != 'model_audit'
                   ) AS visible_assistant_count
            FROM agent_runs run
            JOIN messages message ON message.conversation_id = run.conversation_id
            WHERE run.id = $1
            GROUP BY run.request_id, run.output_message_id
            """,
            second_run_id,
            first_operation_ids,
        )
        assert row
        assert row["request_id"] == second_request_id
        assert row["output_message_id"] is not None
        assert row["second_audit_count"] == 1
        assert row["rebound_count"] == 0
        assert row["visible_assistant_count"] == 2
    finally:
        await conn.close()


async def _assert_persistent_workdir_binding(run_id: str, thread_id: str) -> None:
    """Run 复用 Conversation 的 UserWorkspace Workdir 与线程运行域。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT project.workdir_path,
                   run.runtime_scope_id
            FROM conversations conversation
            JOIN projects project ON project.id = conversation.project_id AND project.uid = conversation.uid
            JOIN agent_runs run ON run.id = $1
            WHERE conversation.thread_id = $2
            """,
            run_id,
            thread_id,
        )
        assert row, f"workdir binding missing for {thread_id}"
        assert str(row["workdir_path"]).startswith("projects/")
        assert row["runtime_scope_id"] == thread_id
    finally:
        await conn.close()


async def _assert_persisted_execution_facts(run_id: str, agent_slug: str) -> None:
    """真实 worker 链路固化后的 manifest 指纹与 attempt 终止事实。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT manifest, manifest_fingerprint, manifest_recorded_at, started_at
            FROM agent_runs
            WHERE id = $1
            """,
            run_id,
        )
        assert row, f"agent_runs row missing for {run_id}"
        raw_manifest = row["manifest"]
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
        assert manifest is not None, "执行完成的 Run 必须已固化运行清单"
        assert manifest["manifest_version"] == 2
        assert manifest["agent"] == {"slug": agent_slug, "backend_id": "ChatbotAgent"}
        assert manifest["model"] == {"spec": MODEL_SPEC}
        assert len(manifest["resources"]["skills"]) == 1
        assert manifest["resources"]["skills"][0]["slug"] == "image-gen"
        assert manifest["resources"]["skills"][0]["content_hash"]
        assert row["manifest_recorded_at"] is not None
        assert row["manifest_recorded_at"] >= row["started_at"]

        serialized = json.dumps(manifest, ensure_ascii=False)
        # 用户正文、prompt 与 provider 密钥不得进入 manifest 直接字段。
        assert EXPECTED_OUTPUT not in serialized
        assert "不要调用工具" not in serialized
        assert "ci-replay-key" not in serialized
        assert EXPECTED_PRELOADED_SKILL_MARKER not in serialized
        assert len(manifest["config_digest"]) == 64

        expected_fingerprint = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert row["manifest_fingerprint"] == expected_fingerprint

        attempts = await conn.fetch(
            """
            SELECT attempt_no, worker_id, outcome, finished_at
            FROM agent_run_attempts
            WHERE run_id = $1
            ORDER BY attempt_no
            """,
            run_id,
        )
        assert attempts, "completed Run 必须有执行占有事实"
        assert attempts[-1]["outcome"] == "completed"
        assert all(attempt["finished_at"] is not None for attempt in attempts)
        assert [attempt["attempt_no"] for attempt in attempts] == list(range(1, len(attempts) + 1))
    finally:
        await conn.close()


@pytest.mark.e2e_smoke
async def test_deterministic_agent_path_reaches_persisted_result(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    await _create_provider(e2e_client, e2e_headers)
    agent_slug: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_completed = False
    try:
        agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
        projection_root = get_skill_projection_dir() / workspace_uid_dirname(uid)
        shutil.rmtree(projection_root, ignore_errors=True)
        assert not projection_root.exists(), "冷启动用例必须从缺失 uid Skill projection 开始"
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": make_test_conversation_title("model-audit-followup"),
                "metadata": make_test_conversation_metadata("model-audit-followup", e2e=True),
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])

        request_id = f"deterministic-e2e-{uuid.uuid4()}"
        run_response = await e2e_client.post(
            "/api/agent-invocation/agent-call/runs",
            json={
                "agent_slug": agent_slug,
                "messages": [{"role": "user", "content": f"只输出 {EXPECTED_OUTPUT}"}],
                "thread_id": thread_id,
                "request_id": request_id,
                "async_mode": True,
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_payload = run_response.json()
        run_id = str(run_payload["run_id"])
        assert str(run_payload["thread_id"]) == thread_id

        event_counts = await consume_events(e2e_client, e2e_headers, run_id)
        assert event_counts.get("messages", 0) > 0, event_counts
        assert event_counts.get("end", 0) == 1, event_counts

        run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run
        assert run["request_id"] == request_id
        assert projection_root.is_dir(), "worker bootstrap 必须在首次 Sandbox 创建前物化 uid projection"

        result = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result.status_code == 200, result.text
        assert result.json()["output"] == EXPECTED_OUTPUT
        assert result.json()["request_id"] == request_id
        assert result.json()["thread_id"] == thread_id

        request_result = await e2e_client.get(
            "/api/agent/request-result", params={"request_id": request_id}, headers=e2e_headers
        )
        assert request_result.status_code == 200, request_result.text
        assert (request_result.json()["run_id"], request_result.json()["output"]) == (run_id, EXPECTED_OUTPUT)

        await _assert_persisted_causality(run_id, request_id)
        await _assert_persistent_workdir_binding(run_id, thread_id)
        await _assert_persisted_execution_facts(run_id, agent_slug)
        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        history = history_response.json()["history"]
        tool_message = next(message for message in history if message.get("tool_calls"))
        tool_call = tool_message["tool_calls"][0]
        assert tool_message["run_id"] == run_id
        assert tool_call["id"] == EXPECTED_TOOL_CALL_ID
        assert tool_call["name"] == EXPECTED_PRELOADED_TOOL
        assert tool_call["status"] == "success"
        assert EXPECTED_TOOL_RESULT_MARKER in tool_call["tool_call_result"]["content"]
        assert any(message.get("content") == EXPECTED_OUTPUT for message in history)

        audit_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/audits", headers=e2e_headers)
        assert audit_response.status_code == 200, audit_response.text
        audit_timeline = [item for item in audit_response.json()["audits"] if item["run_id"] == run_id]
        assert [item["type"] for item in audit_timeline] == ["ai", "tool", "ai"]
        assert [item["sequence"] for item in audit_timeline] == sorted(item["sequence"] for item in audit_timeline)
        assert audit_timeline[1]["tool_call_id"] == EXPECTED_TOOL_CALL_ID
        assert audit_timeline[1]["tool_input"] == {"filepaths": []}
        assert EXPECTED_TOOL_RESULT_MARKER in audit_timeline[1]["content"]

        first_run_id = run_id
        second_request_id = f"deterministic-followup-{uuid.uuid4()}"
        second_response = await e2e_client.post(
            "/api/agent-invocation/agent-call/runs",
            json={
                "agent_slug": agent_slug,
                "messages": [{"role": "user", "content": f"再次只输出 {EXPECTED_OUTPUT}"}],
                "thread_id": thread_id,
                "request_id": second_request_id,
                "async_mode": True,
            },
            headers=e2e_headers,
        )
        assert second_response.status_code == 200, second_response.text
        run_id = str(second_response.json()["run_id"])
        await consume_events(e2e_client, e2e_headers, run_id)
        followup_run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert followup_run["status"] == "completed", followup_run
        followup_result = await e2e_client.get(
            "/api/agent/request-result", params={"request_id": second_request_id}, headers=e2e_headers
        )
        assert followup_result.status_code == 200, followup_result.text
        assert followup_result.json()["request_id"] == second_request_id
        assert followup_result.json()["run_id"] == run_id
        assert followup_result.json()["output"] == EXPECTED_OUTPUT
        await _assert_followup_run_does_not_rebind_prior_audits(
            first_run_id=first_run_id,
            second_run_id=run_id,
            second_request_id=second_request_id,
        )
        run_completed = True
    finally:
        if run_id and not run_completed:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if thread_id:
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_smoke
async def test_standard_user_run_uses_admin_execution_limit(e2e_client, e2e_headers):
    """普通用户执行管理员配置，以真实步数失败和成功结果证明配置生效。"""
    departments = await e2e_client.get("/api/departments", headers=e2e_headers)
    assert departments.status_code == 200, departments.text
    password = f"Pw!{uuid.uuid4().hex}"
    created = await e2e_client.post(
        "/api/auth/users",
        headers=e2e_headers,
        json={
            "username": f"pytest_limit_{uuid.uuid4().hex[:6]}",
            "password": password,
            "role": "user",
            "department_id": departments.json()[0]["id"],
        },
    )
    assert created.status_code == 200, created.text
    user = created.json()
    agent_slug = None
    threads = []
    run_ids = []
    headers = None
    try:
        login = await e2e_client.post("/api/auth/token", data={"username": user["uid"], "password": password})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        await _create_provider(e2e_client, e2e_headers)
        agent_slug = await _create_agent(e2e_client, e2e_headers, str(user["uid"]))
        for limit, expected_status in [(1, "failed"), (42, "completed")]:
            updated = await e2e_client.put(
                f"/api/agent/{agent_slug}",
                headers=e2e_headers,
                json={"config_json": {"context": {"max_execution_steps": limit}}},
            )
            assert updated.status_code == 200, updated.text
            thread = await e2e_client.post(
                "/api/chat/thread",
                headers=headers,
                json={
                    "agent_id": agent_slug,
                    "title": make_test_conversation_title("config-auth"),
                    "metadata": make_test_conversation_metadata("config-auth", e2e=True),
                },
            )
            assert thread.status_code == 200, thread.text
            thread_id = str(thread.json()["id"])
            threads.append(thread_id)
            response = await e2e_client.post(
                "/api/agent/runs",
                headers=headers,
                json={"agent_slug": agent_slug, "thread_id": thread_id, "query": EXPECTED_OUTPUT},
            )
            assert response.status_code == 200, response.text
            run_id = str(response.json()["run_id"])
            run_ids.append(run_id)
            run = await wait_for_run(e2e_client, headers, run_id)
            assert run["status"] == expected_status, run
            conn = await asyncpg.connect(postgres_dsn())
            try:
                row = await conn.fetchrow(
                    "SELECT status, error_message, manifest FROM agent_runs WHERE id = $1", run_id
                )
                assert row["status"] == expected_status
                manifest = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
                assert manifest["limits"]["max_execution_steps"] == limit
                if limit == 1:
                    assert "Recursion limit of 1 reached" in row["error_message"]
                else:
                    result = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
                    assert result.status_code == 200, result.text
                    assert result.json()["output"] == EXPECTED_OUTPUT
            finally:
                await conn.close()
    finally:
        if headers:
            for run_id in run_ids:
                await cancel_run(e2e_client, headers, run_id)
            for thread_id in threads:
                deleted = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=headers)
                assert deleted.status_code in {200, 404}, deleted.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)
        deleted = await e2e_client.delete(f"/api/auth/users/{user['id']}", headers=e2e_headers)
        assert deleted.status_code in {200, 404}, deleted.text


@pytest.mark.e2e_smoke
async def test_scheduled_task_run_now_reaches_exact_conversation_and_result(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    """Run now 复用真实 worker 链路，并把历史记录绑定到准确 Conversation。"""
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    await _create_provider(e2e_client, e2e_headers)
    agent_slug: str | None = None
    directory_name: str | None = None
    project_id: str | None = None
    job_id: str | None = None
    thread_id: str | None = None
    try:
        agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
        directory_name = f"pytest-scheduled-e2e-{uuid.uuid4().hex[:10]}"
        directory_response = await e2e_client.post(
            "/api/workspace/directory",
            headers=e2e_headers,
            json={"parent_path": "/", "name": directory_name},
        )
        assert directory_response.status_code == 200, directory_response.text

        project_response = await e2e_client.post(
            "/api/projects",
            headers=e2e_headers,
            json={
                "request_id": f"scheduled-e2e-project-{uuid.uuid4()}",
                "name": f"pytest scheduled E2E {uuid.uuid4().hex[:8]}",
                "workdir": {"mode": "linked", "path": directory_name},
            },
        )
        assert project_response.status_code == 200, project_response.text
        project_id = str(project_response.json()["id"])

        create_response = await e2e_client.post(
            "/api/scheduled-tasks",
            headers=e2e_headers,
            json={
                "request_id": f"scheduled-e2e-create-{uuid.uuid4()}",
                "name": make_test_conversation_title("scheduled-agent"),
                "project_id": project_id,
                "agent_slug": agent_slug,
                "prompt": f"只输出 {EXPECTED_OUTPUT}",
                "cron_expression": "0 9 * * *",
                "timezone": "UTC",
            },
        )
        assert create_response.status_code == 200, create_response.text
        job_id = str(create_response.json()["id"])

        run_response = await e2e_client.post(
            f"/api/scheduled-tasks/{job_id}/run-now",
            headers=e2e_headers,
            json={"request_id": f"scheduled-e2e-run-{uuid.uuid4()}"},
        )
        assert run_response.status_code == 200, run_response.text
        execution = run_response.json()
        run_id = str(execution["run_id"])
        thread_id = str(execution["thread_id"])

        run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run
        result = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result.status_code == 200, result.text
        assert result.json()["output"] == EXPECTED_OUTPUT
        assert result.json()["thread_id"] == thread_id

        jobs_response = await e2e_client.get("/api/scheduled-tasks", headers=e2e_headers)
        assert jobs_response.status_code == 200, jobs_response.text
        job = next(item for item in jobs_response.json()["jobs"] if item["id"] == job_id)
        history = next(item for item in job["runs"] if item["run_id"] == run_id)
        assert history["status"] == "completed"
        assert history["thread_id"] == thread_id
        assert history["conversation_available"] is True
    finally:
        if job_id:
            response = await e2e_client.delete(f"/api/scheduled-tasks/{job_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if thread_id:
            response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if project_id:
            response = await e2e_client.delete(f"/api/projects/{project_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
            projects_response = await e2e_client.get("/api/projects", headers=e2e_headers)
            assert projects_response.status_code == 200, projects_response.text
            assert project_id not in {item["id"] for item in projects_response.json()}
        if directory_name:
            response = await e2e_client.delete(
                "/api/workspace/file",
                headers=e2e_headers,
                params={"path": f"/{directory_name}"},
            )
            assert response.status_code in {200, 404}, response.text
            tree_response = await e2e_client.get(
                "/api/workspace/tree",
                headers=e2e_headers,
                params={"path": "/", "include_unbound_project_dirs": True},
            )
            assert tree_response.status_code == 200, tree_response.text
            assert directory_name not in {item["name"] for item in tree_response.json()["entries"]}
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_lifecycle
async def test_resume_with_offloaded_tool_result_publishes_stream_owned_audit(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    """审批恢复后的大结果 State 不得覆盖已关闭的原始 Tool 审计。"""
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    await _create_provider(e2e_client, e2e_headers)
    agent_slug: str | None = None
    thread_id: str | None = None
    workdir_path: str | None = None
    active_run_id: str | None = None
    try:
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            system_prompt_suffix=LARGE_TOOL_RESULT_MARKER,
        )
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": make_test_conversation_title("offloaded-tool-resume"),
                "metadata": make_test_conversation_metadata("offloaded-tool-resume", e2e=True),
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])
        workdir_path = str(thread_payload["workdir_path"])

        initial_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": f"只输出 {EXPECTED_OUTPUT}",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": f"deterministic-large-parent-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert initial_response.status_code == 200, initial_response.text
        parent_run_id = str(initial_response.json()["run_id"])
        active_run_id = parent_run_id
        parent_run = await wait_for_run(e2e_client, e2e_headers, parent_run_id)
        assert parent_run["status"] == "interrupted", parent_run
        assert parent_run["error_type"] == "human_approval_required", parent_run
        await _wait_for_runtime_cleanup(parent_run_id)

        # 刷新读取持久化审批时，模型供应商可以不可用；恢复执行前再装配供应商。
        await _delete_provider(e2e_client, e2e_headers)
        state_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/state", headers=e2e_headers)
        assert state_response.status_code == 200, state_response.text
        pending = state_response.json()["interrupt"]
        assert pending["run_id"] == parent_run_id
        assert pending["status"] == "human_approval_required"
        actions = pending["approval"]["action_requests"]
        assert len(actions) == 1
        assert actions[0]["name"] == "execute"
        assert actions[0]["args"]["command"]
        assert "messages" not in state_response.json()
        await _create_provider(e2e_client, e2e_headers)

        session_response = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=e2e_headers)
        assert session_response.status_code == 200, session_response.text
        assert session_response.json()["status"] == "requires_action"
        turn_id = session_response.json()["turn_id"]
        assert turn_id

        resume_request_id = f"deterministic-large-resume-{uuid.uuid4()}"
        resume_event = {
            "events": [{
                "type": "yuxi.session.input.resume",
                "turn_id": turn_id,
                "run_id": parent_run_id,
                "resume": {"decisions": [{"type": "approve"}]},
            }]
        }
        resume_headers = {**e2e_headers, "Idempotency-Key": resume_request_id}
        resume_response = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            json=resume_event,
            headers=resume_headers,
        )
        assert resume_response.status_code == 202, resume_response.text
        assert resume_response.json()["turn_id"] == turn_id
        resume_run_id = str(resume_response.json()["run_id"])
        replayed = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events", json=resume_event, headers=resume_headers
        )
        assert replayed.status_code == 202, replayed.text
        assert replayed.json() == resume_response.json()
        wrong_parent = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            json={"events": [{**resume_event["events"][0], "run_id": resume_run_id}]},
            headers={**e2e_headers, "Idempotency-Key": f"wrong-{uuid.uuid4().hex}"},
        )
        assert wrong_parent.status_code == 409, wrong_parent.text
        active_run_id = resume_run_id
        await consume_events(e2e_client, e2e_headers, resume_run_id)
        resume_run = await wait_for_run(e2e_client, e2e_headers, resume_run_id)
        assert resume_run["status"] == "completed", resume_run
        assert resume_run["output_message_id"] is not None, resume_run
        await _assert_single_persisted_input(parent_run_id)
        await _assert_single_persisted_input(resume_run_id)

        result = await e2e_client.get(f"/api/agent/runs/{resume_run_id}/result", headers=e2e_headers)
        assert result.status_code == 200, result.text
        assert result.json()["output"] == EXPECTED_OUTPUT
        turn_response = await e2e_client.get(
            f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=e2e_headers
        )
        assert turn_response.status_code == 200, turn_response.text
        assert turn_response.json()["run_ids"] == [parent_run_id, resume_run_id]
        assert turn_response.json()["output"] == EXPECTED_OUTPUT
        async with e2e_client.stream(
            "GET",
            f"/api/v1/agents/sessions/{thread_id}/events",
            params={"turn_id": turn_id},
            headers={**e2e_headers, "Last-Event-ID": f"{parent_run_id}:end"},
        ) as resumed_events:
            assert resumed_events.status_code == 200, resumed_events.text
            resumed_body = (await resumed_events.aread()).decode()
        assert resumed_body.startswith("event: run_created\n")
        assert f"id: {resume_run_id}:0-0\n" in resumed_body
        assert f"id: {parent_run_id}:end\n" not in resumed_body

        completed_state = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/state", params={"include_messages": "true"}, headers=e2e_headers
        )
        assert completed_state.status_code == 200, completed_state.text
        assert "interrupt" not in completed_state.json()
        final_message = completed_state.json()["messages"][-1]
        assert final_message["type"] == "ai"
        assert parse_assistant_message_body(final_message["content"])["content"] == EXPECTED_OUTPUT

        conn = await asyncpg.connect(postgres_dsn())
        try:
            parent_payload = json.loads(
                await conn.fetchval(
                    "SELECT input_payload::text FROM agent_runs WHERE id = $1",
                    parent_run_id,
                )
            )
            resumed = await conn.fetchrow(
                "SELECT r.input_payload::text AS payload, m.message_type, m.content, "
                "m.extra_metadata::text AS metadata "
                "FROM agent_runs r JOIN messages m ON m.id = r.input_message_id WHERE r.id = $1",
                resume_run_id,
            )
            assert json.loads(resumed["payload"]) == parent_payload
            assert parent_payload["tool_approval_mode"] == "default"
            assert resumed["message_type"] == "resume"
            assert json.loads(resumed["content"]) == {"decisions": [{"type": "approve"}]}
            assert json.loads(resumed["metadata"])["resume"] == {"decisions": [{"type": "approve"}]}
            assert "此字段不应进入恢复消息" not in resumed["metadata"]
            audit = await conn.fetchrow(
                """
                SELECT execution_status, content, extra_metadata
                FROM messages
                WHERE run_id = $1 AND message_type = 'tool_audit' AND operation_id = $2
                """,
                resume_run_id,
                LARGE_TOOL_CALL_ID,
            )
        finally:
            await conn.close()
        assert audit
        assert audit["execution_status"] == "completed"
        assert len(audit["content"]) > 3 * 1024 * 4
        raw_metadata = audit["extra_metadata"]
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
        assert metadata["tool_name"] == "execute"
        assert metadata["output"]["content"] == audit["content"]

        sandbox = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        offloaded = sandbox.read(f"/home/gem/user-data/{workdir_path}/outputs/large_tool_results/{LARGE_TOOL_CALL_ID}")
        assert offloaded.error is None, offloaded
        assert offloaded.file_data and audit["content"].startswith(offloaded.file_data["content"])
        assert offloaded.next_offset is not None
        active_run_id = None
    finally:
        if active_run_id:
            await cancel_run(e2e_client, e2e_headers, active_run_id)
        if thread_id:
            try:
                get_sandbox_provider().release(thread_id, uid=uid, workdir_path=workdir_path)
            except Exception:
                pass
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_smoke
async def test_deterministic_tool_error_is_persisted_by_tool_message(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    """真实 worker 将 ToolNode 受控错误保存为 failed ToolMessage 与兼容 ToolCall。"""
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    await _create_provider(e2e_client, e2e_headers)
    agent_slug: str | None = None
    thread_id: str | None = None
    try:
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            system_prompt_suffix=TOOL_ERROR_MARKER,
        )
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": make_test_conversation_title("tool-audit-error"),
                "metadata": make_test_conversation_metadata("tool-audit-error", e2e=True),
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])

        run = await _run_deterministic(
            e2e_client,
            e2e_headers,
            agent_slug=agent_slug,
            thread_id=thread_id,
        )
        conn = await asyncpg.connect(postgres_dsn())
        try:
            row = await conn.fetchrow(
                """
                SELECT audit.execution_status, audit.content, audit.duration_ms,
                       tool_call.status AS tool_call_status, tool_call.error_message
                FROM messages audit
                LEFT JOIN tool_calls tool_call
                  ON tool_call.id = (audit.extra_metadata->>'compatibility_tool_call_id')::integer
                WHERE audit.run_id = $1 AND audit.message_type = 'tool_audit'
                """,
                run["id"],
            )
        finally:
            await conn.close()

        assert row
        assert row["execution_status"] == "failed"
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0
        assert row["tool_call_status"] == "error"
        assert row["error_message"]
    finally:
        if thread_id:
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_lifecycle
async def test_cancelled_run_keeps_trace_and_closes_running_model_audit(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    """模型请求开始后取消时，保留 Run trace 并关闭无最终输出的 Model 审计。"""
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    await _create_provider(e2e_client, e2e_headers)
    agent_slug: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    terminal = False
    try:
        blocking_token = str(uuid.uuid4())
        agent_slug = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            system_prompt_suffix=f"{BLOCK_BEFORE_RESPONSE_MARKER}:{blocking_token}",
        )
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": make_test_conversation_title("cancelled-trace"),
                "metadata": make_test_conversation_metadata("cancelled-trace", e2e=True),
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])

        request_id = f"deterministic-cancel-{uuid.uuid4()}"
        run_response = await e2e_client.post(
            "/api/agent-invocation/agent-call/runs",
            json={
                "agent_slug": agent_slug,
                "messages": [{"role": "user", "content": f"只输出 {EXPECTED_OUTPUT}"}],
                "request_id": request_id,
                "thread_id": thread_id,
                "async_mode": True,
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        assert str(run_response.json()["thread_id"]) == thread_id

        await _wait_for_blocking_replay(blocking_token)
        await _wait_for_running_model_audit(run_id)
        await cancel_run(e2e_client, e2e_headers, run_id)
        run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "cancelled", run
        terminal = True

        conn = await asyncpg.connect(postgres_dsn())
        try:
            row = await conn.fetchrow(
                """
                SELECT ar.langfuse_trace_id, ar.output_message_id, ar.first_model_request_at,
                       count(message.id) FILTER (
                           WHERE message.role = 'assistant' AND message.message_type != 'model_audit'
                       ) AS visible_assistant_count,
                       count(message.id) FILTER (
                           WHERE message.role = 'assistant' AND message.message_type = 'model_audit'
                       ) AS audit_count,
                       min(message.execution_status) FILTER (
                           WHERE message.message_type = 'model_audit'
                       ) AS audit_status,
                       count(message.id) FILTER (
                           WHERE message.role = 'assistant'
                             AND (
                                 message.run_id IS DISTINCT FROM ar.id
                                 OR message.request_id IS DISTINCT FROM ar.request_id
                             )
                       ) AS misbound_assistant_count
                FROM agent_runs ar
                LEFT JOIN messages message ON message.conversation_id = ar.conversation_id
                WHERE ar.id = $1
                GROUP BY ar.langfuse_trace_id, ar.output_message_id, ar.first_model_request_at
                """,
                run_id,
            )
        finally:
            await conn.close()

        assert row
        assert row["langfuse_trace_id"]
        assert row["output_message_id"] is None
        assert row["first_model_request_at"] is not None
        assert row["visible_assistant_count"] == 0
        assert row["audit_count"] == 1
        assert row["audit_status"] == "interrupted"
        assert row["misbound_assistant_count"] == 0

        result = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result.status_code == 200, result.text
        assert result.json()["output"] == ""
        assert result.json()["timing"]["first_model_request_latency_ms"] is not None
        assert result.json()["langfuse_trace_id"] == row["langfuse_trace_id"]
    finally:
        if run_id and not terminal:
            await cancel_run(e2e_client, e2e_headers, run_id)
        if thread_id:
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_boundaries
async def test_attachment_is_written_to_user_workspace_workdir_and_survives_runtime_recreation(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> None:
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])
    await _create_provider(e2e_client, e2e_headers)

    agent_slug: str | None = None
    thread_id: str | None = None
    workdir_path: str | None = None
    try:
        agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": make_test_conversation_title("attachment-workdir"),
                "metadata": make_test_conversation_metadata("attachment-workdir", e2e=True),
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])
        workdir_path = str(thread_payload["workdir_path"])

        expected_content = f"sandbox hydrate {uuid.uuid4()}\n"
        file_name = f"hydrate-{uuid.uuid4().hex[:8]}.txt"
        upload_response = await e2e_client.post(
            "/api/chat/attachments/tmp",
            files={"file": (file_name, expected_content.encode(), "text/plain")},
            headers=e2e_headers,
        )
        assert upload_response.status_code == 200, upload_response.text
        uploaded = upload_response.json()
        confirm_response = await e2e_client.post(
            f"/api/chat/thread/{thread_id}/attachments/confirm",
            json={
                "attachments": [
                    {
                        "file_type": uploaded.get("file_type"),
                        "object_name": uploaded["object_name"],
                    }
                ]
            },
            headers=e2e_headers,
        )
        assert confirm_response.status_code == 200, confirm_response.text
        attachment = confirm_response.json()["attachments"][0]
        attachment_path = str(attachment["original_path"])
        assert attachment_path.startswith(f"/home/gem/user-data/{workdir_path}/uploads/"), attachment

        sandbox = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        uploaded_read = sandbox.read(attachment_path)
        assert uploaded_read.error is None, uploaded_read
        assert uploaded_read.file_data == {"content": expected_content.rstrip(), "encoding": "utf-8"}
        overwritten_content = f"agent overwrite {uuid.uuid4()}"
        overwrite_result = sandbox.edit(
            attachment_path,
            expected_content.rstrip(),
            overwritten_content,
        )
        assert overwrite_result.error is None, overwrite_result
        live_artifact = await e2e_client.get(attachment["original_artifact_url"], headers=e2e_headers)
        assert live_artifact.status_code == 200, live_artifact.text
        assert live_artifact.text.strip() == overwritten_content

        await _run_deterministic(
            e2e_client,
            e2e_headers,
            agent_slug=agent_slug,
            thread_id=thread_id,
            attachment_file_ids=[str(attachment["file_id"])],
        )

        read_result = sandbox.read(attachment_path)
        assert read_result.error is None, read_result
        assert read_result.file_data == {"content": overwritten_content, "encoding": "utf-8"}

        get_sandbox_provider().release(thread_id, uid=uid, workdir_path=workdir_path)
        sandbox = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        recreated_read = sandbox.read(attachment_path)
        assert recreated_read.error is None, recreated_read
        assert recreated_read.file_data == {"content": overwritten_content, "encoding": "utf-8"}

        delete_response = await e2e_client.delete(
            f"/api/chat/thread/{thread_id}/attachments/{attachment['file_id']}",
            headers=e2e_headers,
        )
        assert delete_response.status_code == 200, delete_response.text
        missing_result = sandbox.read(attachment_path)
        assert missing_result.file_data is None
        assert missing_result.error
        missing_error = missing_result.error.lower()
        assert attachment_path.lower() in missing_error
        canonical_not_found = f"file '{attachment_path.lower()}' not found"
        assert any(marker in missing_error for marker in ("does not exist", canonical_not_found, "filenotfounderror"))
    finally:
        if thread_id:
            try:
                get_sandbox_provider().release(thread_id, uid=uid, workdir_path=workdir_path)
            except Exception:
                pass
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
        if agent_slug:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        await _delete_provider(e2e_client, e2e_headers)


@pytest.mark.e2e_boundaries
async def test_subagent_end_is_observable_while_parent_awaits_slow_child(e2e_client, e2e_headers):
    """父 graph 等待慢任务时，独立子 SSE 和数据库已能证明快任务完成。"""
    uid = str((await e2e_client.get("/api/auth/me", headers=e2e_headers)).json()["uid"])
    await _create_provider(e2e_client, e2e_headers)
    agents, child_threads = [], []
    thread_id = run_id = None
    gate = str(uuid.uuid4())
    try:
        child = await _create_agent(
            e2e_client, e2e_headers, uid, is_subagent=True, system_prompt_suffix="DETERMINISTIC_SUBAGENT_CHILD"
        )
        agents.append(child)
        parent = await _create_agent(
            e2e_client,
            e2e_headers,
            uid,
            subagents=[child],
            system_prompt_suffix=f"DETERMINISTIC_SUBAGENT_PARENT:{child}",
        )
        agents.append(parent)
        response = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={
                "agent_id": parent,
                "title": make_test_conversation_title("subagent-observation"),
                "metadata": make_test_conversation_metadata("subagent-observation", e2e=True),
            },
        )
        assert response.status_code == 200, response.text
        thread_id = response.json()["id"]
        response = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "agent_slug": parent,
                "thread_id": thread_id,
                "query": f"{EXPECTED_OUTPUT} SUBAGENT_OBSERVATION_GATE:{gate} SUBAGENT_PATH:/tmp/not-written",
                "tool_approval_mode": "default",
                "meta": {"request_id": str(uuid.uuid4())},
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        conn = await asyncpg.connect(postgres_dsn())
        try:
            async with asyncio.timeout(45):
                while True:
                    children = await conn.fetch(
                        "SELECT id, status, conversation_thread_id, input_payload, output_message_id "
                        "FROM agent_runs WHERE created_by_run_id = $1 AND run_type = 'subagent'",
                        run_id,
                    )
                    by_call = {json.loads(row["input_payload"])["runtime"]["tool_call_id"]: row for row in children}
                    awaiting = await conn.fetchval(
                        "SELECT execution_status FROM messages WHERE run_id = $1 AND message_type = 'tool_audit' "
                        "AND operation_id = 'await-call-subagent-slow'",
                        run_id,
                    )
                    if (
                        len(by_call) == 2
                        and by_call["call-subagent-start"]["status"] == "completed"
                        and by_call["call-subagent-slow"]["status"] == "running"
                        and awaiting == "running"
                    ):
                        break
                    await asyncio.sleep(0.2)
            child_threads = [row["conversation_thread_id"] for row in children]
            fast, slow = by_call["call-subagent-start"], by_call["call-subagent-slow"]
            assert fast["output_message_id"] is not None
            assert await conn.fetchval("SELECT status FROM agent_runs WHERE id = $1", run_id) == "running"
            state = await e2e_client.get(f"/api/chat/thread/{thread_id}/state", headers=e2e_headers)
            assert state.status_code == 200, state.text
            states = {row["run_id"]: row["status"] for row in state.json()["agent_state"]["subagent_runs"]}
            assert states == {fast["id"]: "completed", slow["id"]: "running"}
            async with e2e_client.stream("GET", f"/api/agent/runs/{fast['id']}/events", headers=e2e_headers) as events:
                assert events.status_code == 200
                body = (await events.aread()).decode()
                assert "event: end" in body and '"completed"' in body
            assert await conn.fetchval("SELECT status FROM agent_runs WHERE id = $1", run_id) == "running"
        finally:
            await conn.close()
        async with httpx.AsyncClient() as replay:
            await replay.get("http://localhost:8765/release-subagent", params={"token": gate})
        final = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert final["status"] == "completed", final
        for row in children:
            result = await e2e_client.get(f"/api/agent/runs/{row['id']}/result", headers=e2e_headers)
            assert result.json()["status"] == "completed", result.text
            assert result.json()["output"] == EXPECTED_OUTPUT
    finally:
        async with httpx.AsyncClient() as replay:
            await replay.get("http://localhost:8765/release-subagent", params={"token": gate})
        if run_id:
            await cancel_run(e2e_client, e2e_headers, run_id)
        for target in [*child_threads, thread_id]:
            if target:
                await e2e_client.delete(f"/api/chat/thread/{target}", headers=e2e_headers)
        for slug in reversed(agents):
            await delete_agent(e2e_client, e2e_headers, slug)
        await _delete_provider(e2e_client, e2e_headers)
