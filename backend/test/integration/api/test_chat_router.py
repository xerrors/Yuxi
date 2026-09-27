"""
Integration tests for chat router endpoints.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import PurePosixPath

import asyncpg
import pytest
from PIL import Image

from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _postgres_dsn() -> str:
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "+asyncpg", ""
    )


async def _upload_project_file(
    test_client,
    headers,
    thread_id: str,
    name: str,
    content: bytes,
    *,
    parent_path: str = "/",
    artifact_path: bool = False,
) -> str:
    response = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": parent_path},
        files={"files": (name, content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    entry = response.json()["entries"][0]
    if not artifact_path:
        return entry["path"]
    marker = f"/api/chat/thread/{thread_id}/artifacts/"
    assert entry["artifact_url"].startswith(marker)
    return f"/{entry['artifact_url'][len(marker) :]}"


async def test_chat_endpoints_require_authentication(test_client):
    assert (await test_client.get("/api/chat/threads")).status_code == 401
    assert (await test_client.get(f"/api/chat/thread/{uuid.uuid4()}/audits")).status_code == 401
    assert (await test_client.get("/api/agent")).status_code == 401


async def test_thread_message_audits_return_persisted_facts_without_leaking_into_history(
    test_client,
    standard_user,
    admin_headers,
):
    standard_headers = standard_user["headers"]
    standard_thread_id = await _create_thread_for_user(test_client, standard_headers)

    message_audit_forbidden = await test_client.get(
        f"/api/chat/thread/{standard_thread_id}/audits",
        headers=standard_headers,
    )
    assert message_audit_forbidden.status_code == 403, message_audit_forbidden.text

    message_audit_cross_user = await test_client.get(
        f"/api/chat/thread/{standard_thread_id}/audits",
        headers=admin_headers,
    )
    assert message_audit_cross_user.status_code == 404, message_audit_cross_user.text

    thread_id = await _create_thread_for_user(test_client, admin_headers)
    run_id = f"run-{uuid.uuid4()}"
    failed_run_id = f"run-{uuid.uuid4()}"
    request_id = f"request-{uuid.uuid4()}"
    failed_request_id = f"request-{uuid.uuid4()}"
    started_at = datetime(2026, 8, 30, 1, 0, 0)

    conn = await asyncpg.connect(_postgres_dsn())
    try:
        conversation = await conn.fetchrow(
            "SELECT id, uid, agent_id FROM conversations WHERE thread_id = $1",
            thread_id,
        )
        assert conversation
        await conn.execute(
            """
            INSERT INTO agent_runs
                (id, conversation_thread_id, runtime_scope_id, agent_slug, uid, status,
                 request_id, source, channel, conversation_id, run_type, input_payload, token_usage,
                 origin_metadata, created_at, started_at, finished_at)
            VALUES ($1, $2, $2, $3, $4, 'completed', $5, 'chat', 'web', $6, 'chat', '{}'::jsonb,
                    '{}'::jsonb, '{}'::jsonb, $7, $8, $9)
            """,
            run_id,
            thread_id,
            conversation["agent_id"],
            conversation["uid"],
            request_id,
            conversation["id"],
            started_at,
            started_at,
            started_at + timedelta(seconds=3),
        )
        await conn.execute(
            """
            INSERT INTO agent_runs
                (id, conversation_thread_id, runtime_scope_id, agent_slug, uid, status,
                 request_id, source, channel, conversation_id, run_type, input_payload, token_usage,
                 origin_metadata, error_type, created_at, started_at, finished_at)
            VALUES ($1, $2, $2, $3, $4, 'failed', $5, 'chat', 'web', $6, 'chat', '{}'::jsonb,
                    '{}'::jsonb, '{}'::jsonb, 'invalid_input', $7, $7, $8)
            """,
            failed_run_id,
            thread_id,
            conversation["agent_id"],
            conversation["uid"],
            failed_request_id,
            conversation["id"],
            started_at + timedelta(seconds=4),
            started_at + timedelta(seconds=5),
        )
        await conn.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, delivery_status, extra_metadata, run_id,
                 request_id, created_at)
            VALUES ($1, 'user', '会在审计前失败', 'failed', '{}'::jsonb, $2, $3, $4)
            """,
            conversation["id"],
            failed_run_id,
            failed_request_id,
            started_at + timedelta(seconds=4),
        )
        await conn.executemany(
            """
            INSERT INTO messages
                (conversation_id, role, content, message_type, delivery_status, extra_metadata, run_id,
                 request_id, operation_id, started_at, finished_at, duration_ms, sequence,
                 execution_status, usage)
            VALUES ($1, 'assistant', $2, 'model_audit', 'complete', $3::jsonb, $4, $5, $6, $7, $8,
                    $9, $10, 'completed', $11::jsonb)
            """,
            [
                (
                    conversation["id"],
                    "第二次模型输出",
                    json.dumps(
                        {
                            "finished_sequence": 9,
                            "content": [{"type": "text", "text": "第二次模型输出"}],
                            "private_internal_field": "must-not-leak",
                        },
                        ensure_ascii=False,
                    ),
                    run_id,
                    request_id,
                    "operation-2",
                    started_at + timedelta(seconds=2),
                    started_at + timedelta(seconds=3),
                    1000,
                    7,
                    json.dumps({"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}),
                ),
                (
                    conversation["id"],
                    "第一次模型输出",
                    json.dumps(
                        {
                            "finished_sequence": 5,
                            "state_reconciled": True,
                            "private_internal_field": "must-not-leak",
                        }
                    ),
                    run_id,
                    request_id,
                    "operation-1",
                    started_at,
                    started_at + timedelta(seconds=1),
                    1000,
                    3,
                    json.dumps({"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}),
                ),
            ],
        )
        model_messages = await conn.fetch(
            "SELECT id, operation_id FROM messages WHERE run_id = $1 AND role = 'assistant'",
            run_id,
        )
        message_ids = {row["operation_id"]: row["id"] for row in model_messages}
        await conn.executemany(
            """
            INSERT INTO tool_calls
                (message_id, langgraph_tool_call_id, tool_name, tool_input, tool_output, status)
            VALUES ($1, $2, 'search', '{}'::jsonb, $3, 'success')
            """,
            [
                (message_ids["operation-1"], "compat-call-proven", "safe result"),
                (message_ids["operation-2"], "compat-call-unproven", "must stay hidden"),
            ],
        )
        await conn.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, message_type, delivery_status, extra_metadata, run_id,
                 request_id, operation_id, started_at, finished_at, duration_ms, sequence,
                 execution_status, usage)
            VALUES ($1, 'tool', '查询结果', 'tool_audit', 'complete', $2::jsonb, $3, $4, 'call-1',
                    $5, $6, 400, 6, 'completed', NULL)
            """,
            conversation["id"],
            json.dumps(
                {
                    "tool_call_id": "call-1",
                    "tool_name": "search",
                    "input": {"q": "Yuxi"},
                    "output": {"type": "tool", "content": "查询结果", "status": "success"},
                    "source_model_operation_id": "operation-1",
                    "finished_sequence": 7,
                    "private_internal_field": "must-not-leak",
                },
                ensure_ascii=False,
            ),
            run_id,
            request_id,
            started_at + timedelta(seconds=1),
            started_at + timedelta(milliseconds=1400),
        )
        await conn.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, message_type, delivery_status, extra_metadata, run_id,
                 request_id, operation_id, sequence, execution_status)
            SELECT $1, 'assistant', 'bounded-' || sequence_value, 'model_audit', 'complete', '{}'::jsonb,
                   $2, $3, 'bounded-' || sequence_value, sequence_value, 'completed'
            FROM generate_series(10, 507) AS generated(sequence_value)
            """,
            conversation["id"],
            run_id,
            request_id,
        )
    finally:
        await conn.close()

    timeline_response = await test_client.get(f"/api/chat/thread/{thread_id}/audits", headers=admin_headers)
    assert timeline_response.status_code == 200, timeline_response.text
    timeline_payload = timeline_response.json()
    timeline = timeline_payload["audits"]
    assert timeline_payload["truncated"] is True
    assert timeline_payload["runs_truncated"] is False
    assert timeline_payload["runs"] == [
        {
            "run_id": run_id,
            "status": "completed",
            "timing": {
                "created_at": "2026-08-30T01:00:00Z",
                "started_at": "2026-08-30T01:00:00Z",
                "prepared_at": None,
                "first_model_request_at": None,
                "first_output_at": None,
                "finished_at": "2026-08-30T01:00:03Z",
                "dispatch_latency_ms": 0,
                "preparation_latency_ms": None,
                "first_model_request_latency_ms": None,
                "model_first_output_latency_ms": None,
                "first_output_latency_ms": None,
                "total_latency_ms": 3000,
            },
        },
        {
            "run_id": failed_run_id,
            "status": "failed",
            "timing": {
                "created_at": "2026-08-30T01:00:04Z",
                "started_at": "2026-08-30T01:00:04Z",
                "prepared_at": None,
                "first_model_request_at": None,
                "first_output_at": None,
                "finished_at": "2026-08-30T01:00:05Z",
                "dispatch_latency_ms": 0,
                "preparation_latency_ms": None,
                "first_model_request_latency_ms": None,
                "model_first_output_latency_ms": None,
                "first_output_latency_ms": None,
                "total_latency_ms": 1000,
            },
        },
    ]
    assert len(timeline) == 500
    assert [audit["operation_id"] for audit in timeline[:2]] == ["call-1", "operation-2"]
    assert [audit["type"] for audit in timeline[:2]] == ["tool", "ai"]
    assert timeline[0]["tool_name"] == "search"
    assert timeline[0]["tool_input"] == {"q": "Yuxi"}
    assert timeline[0]["content"] == "查询结果"
    assert timeline[0]["duration_ms"] == 400
    assert timeline[1]["sequence"] == 7
    assert timeline[1]["duration_ms"] == 1000
    assert timeline[1]["started_at"] == "2026-08-30T01:00:02Z"
    assert timeline[1]["finished_at"] == "2026-08-30T01:00:03Z"
    assert timeline[1]["usage"]["total_tokens"] == 10
    assert timeline[1]["content_blocks"] == [{"type": "text", "text": "第二次模型输出"}]
    assert timeline[-1]["operation_id"] == "bounded-507"
    assert timeline[-1]["sequence"] == 507
    assert "private_internal_field" not in timeline_response.text

    retired_response = await test_client.get(
        f"/api/chat/thread/{thread_id}/model-audits",
        headers=admin_headers,
    )
    assert retired_response.status_code == 404, retired_response.text

    history = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=admin_headers)
    assert history.status_code == 200, history.text
    history_items = history.json()["history"]
    assert len(history_items) == 2
    failed_input = next(item for item in history_items if item["run_id"] == failed_run_id)
    assert failed_input["type"] == "human"
    assert failed_input["delivery_status"] == "failed"
    proven_output = next(item for item in history_items if item["run_id"] == run_id)
    assert proven_output["content"] == "第一次模型输出"
    assert proven_output["extra_metadata"] == {}
    assert proven_output["tool_calls"][0]["id"] == "compat-call-proven"
    assert "must-not-leak" not in history.text
    assert "must stay hidden" not in history.text


async def test_image_upload_composites_transparent_png_pixels_on_white(test_client, admin_headers):
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    image.putpixel((0, 0), (50, 87, 244, 0))
    image.putpixel((1, 0), (50, 87, 244, 255))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

    response = await test_client.post(
        "/api/chat/image/upload",
        headers=admin_headers,
        files={"file": ("transparent.png", image_bytes, "image/png")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["mime_type"] == "image/png"

    processed_data = base64.b64decode(payload["image_content"])
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        rgb_image = processed_image.convert("RGB")

    assert rgb_image.getpixel((0, 0)) == (255, 255, 255)
    assert rgb_image.getpixel((1, 0)) == (50, 87, 244)


async def test_legacy_direct_thread_attachment_upload_is_removed(test_client, admin_headers):
    response = await test_client.post(
        f"/api/chat/thread/{uuid.uuid4()}/attachments",
        headers=admin_headers,
        files={"file": ("legacy.txt", b"legacy", "text/plain")},
    )

    assert response.status_code == 405


async def test_development_thread_file_browse_routes_are_removed(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)
    path = await _upload_project_file(test_client, admin_headers, thread_id, "removed-route.txt", b"content")

    list_response = await test_client.get(
        f"/api/chat/thread/{thread_id}/files",
        params={"path": "/"},
        headers=admin_headers,
    )
    content_response = await test_client.get(
        f"/api/chat/thread/{thread_id}/files/content",
        params={"path": path},
        headers=admin_headers,
    )

    assert list_response.status_code == 404
    assert content_response.status_code == 404


async def test_thread_artifact_uses_image_signature_for_content_type(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

    upload_response = await test_client.post(
        "/api/chat/attachments/tmp",
        headers=admin_headers,
        files={"file": ("mislabeled.jpg", image_bytes, "image/jpeg")},
    )

    assert upload_response.status_code == 200, upload_response.text
    uploaded = upload_response.json()
    confirm_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/attachments/confirm",
        headers=admin_headers,
        json={
            "attachments": [
                {
                    "file_type": uploaded.get("file_type"),
                    "object_name": uploaded["object_name"],
                }
            ]
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    attachment = confirm_response.json()["attachments"][0]
    listed = await test_client.get(f"/api/chat/thread/{thread_id}/attachments", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert any(item["file_id"] == attachment["file_id"] for item in listed.json()["attachments"])

    artifact_response = await test_client.get(attachment["original_artifact_url"], headers=admin_headers)

    assert artifact_response.status_code == 200, artifact_response.text
    assert artifact_response.headers["content-type"].startswith("image/png")
    assert artifact_response.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_thread_artifact_preview_http_preserves_raw_download(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    content = b"# artifact preview\n"
    artifact_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        f"preview-{uuid.uuid4().hex[:8]}.md",
        content,
        artifact_path=True,
    )
    artifact_url = f"/api/chat/thread/{thread_id}/artifacts/{artifact_path.lstrip('/')}"

    preview_response = await test_client.get(
        artifact_url,
        params={"preview": "true"},
        headers=headers,
    )
    assert preview_response.status_code == 200, preview_response.text
    assert preview_response.headers["content-type"].startswith("application/json")
    assert preview_response.json() == {
        "content": content.decode(),
        "preview_type": "markdown",
        "supported": True,
        "message": None,
        "truncated": False,
        "limit": 250_000,
    }

    raw_response = await test_client.get(artifact_url, headers=headers)
    assert raw_response.status_code == 200, raw_response.text
    assert raw_response.content == content
    assert raw_response.headers["content-type"].startswith("text/markdown")


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    assert agents, "Chat router integration requires at least one visible Agent."

    agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    assert agent_id, f"Agent payload missing identifier: {agents[0]}"

    create_resp = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": make_test_conversation_title("chat-router"),
            "metadata": make_test_conversation_metadata("chat-router"),
        },
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    payload = create_resp.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, f"Create thread response missing thread identifier: {payload}"
    return thread_id


async def test_thread_history_envelope_has_all_runs_and_keeps_viewed_explicit(
    test_client, admin_headers, standard_user
):
    """History 独立返回完整运行列表，读取不标记已读且不跨用户泄露。"""
    thread_id = await _create_thread_for_user(test_client, admin_headers)
    conn = await asyncpg.connect(_postgres_dsn())
    prefix = uuid.uuid4().hex
    started_at = datetime(2026, 9, 5, 0, 0, 0)
    try:
        empty = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=admin_headers)
        assert empty.status_code == 200, empty.text
        assert empty.json()["history"] == []
        assert empty.json()["runs"] == []
        assert empty.json()["thread"]["id"] == thread_id
        assert empty.json()["thread"]["thread_status"] == "done"

        conversation = await conn.fetchrow("SELECT * FROM conversations WHERE thread_id = $1", thread_id)
        marker = conversation["last_viewed_run_id"]
        # 超过审计窗口，验证普通历史不会静默截掉较早或零消息的 Run。
        await conn.executemany(
            """
            INSERT INTO agent_runs
                (id, conversation_thread_id, runtime_scope_id, agent_slug, uid, status,
                 request_id, conversation_id, run_type, input_payload, created_at, finished_at)
            VALUES ($1, $2, $2, $3, $4, 'cancelled', $5, $6, 'chat',
                    '{"private_input":"must-not-leak"}'::jsonb, $7, $8)
            """,
            [
                (
                    f"{prefix}-{index:03}",
                    thread_id,
                    conversation["agent_id"],
                    conversation["uid"],
                    f"request-{prefix}-{index}",
                    conversation["id"],
                    started_at + timedelta(seconds=index * 2),
                    started_at + timedelta(seconds=index * 2 + 1),
                )
                for index in range(501)
            ],
        )
        await conn.execute(
            """
            INSERT INTO messages
                (conversation_id, role, content, delivery_status, extra_metadata, run_id, created_at)
            VALUES ($1, 'assistant', '历史回答', 'complete', '{}'::jsonb, $2, $3),
                   ($1, 'assistant', '没有 Run 的旧回答', 'complete', '{}'::jsonb, NULL, $3)
            """,
            conversation["id"],
            f"{prefix}-000",
            started_at,
        )
        response = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=admin_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"thread", "runs", "history"}
        assert payload["thread"]["project_id"] == empty.json()["thread"]["project_id"]
        assert payload["thread"]["workdir_path"] == empty.json()["thread"]["workdir_path"]
        assert payload["thread"]["thread_status"] == "ready"
        assert [run["run_id"] for run in payload["runs"]] == [f"{prefix}-{index:03}" for index in range(501)]
        assert all(run["status"] == "cancelled" for run in payload["runs"])
        assert payload["runs"][0]["timing"]["total_latency_ms"] == 1000
        assert payload["runs"][-1]["request_id"] == f"request-{prefix}-500"
        assert all(run["run_type"] == "chat" for run in payload["runs"])
        assert len(payload["history"]) == 2
        assert any(message["run_id"] is None for message in payload["history"])
        assert all(
            {"run_timing", "run_started_at", "run_finished_at"}.isdisjoint(message) for message in payload["history"]
        )
        assert "must-not-leak" not in response.text
        assert (
            await conn.fetchval("SELECT last_viewed_run_id FROM conversations WHERE thread_id = $1", thread_id)
            == marker
        )

        denied = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=standard_user["headers"])
        assert denied.status_code == 404
        assert prefix not in denied.text
        viewed = await test_client.post(f"/api/chat/thread/{thread_id}/viewed", headers=admin_headers)
        assert viewed.status_code == 200, viewed.text
        assert viewed.json()["thread_status"] == "done"
        assert (
            await conn.fetchval("SELECT last_viewed_run_id FROM conversations WHERE thread_id = $1", thread_id)
            == f"{prefix}-500"
        )
        reread = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=admin_headers)
        assert reread.json()["thread"]["thread_status"] == "done"
        await test_client.delete(f"/api/chat/thread/{thread_id}", headers=admin_headers)
        deleted = await test_client.get(f"/api/chat/thread/{thread_id}/history", headers=admin_headers)
        assert deleted.status_code == 404
    finally:
        await conn.close()
        await test_client.delete(f"/api/chat/thread/{thread_id}", headers=admin_headers)


async def test_thread_tool_approval_mode_is_saved_in_conversation_metadata(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)

    update_response = await test_client.put(
        f"/api/chat/thread/{thread_id}",
        headers=admin_headers,
        json={"tool_approval_mode": "always_trust"},
    )

    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["metadata"]["tool_approval_mode"] == "always_trust"

    list_response = await test_client.get("/api/chat/threads", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    thread = next(item for item in list_response.json() if item["id"] == thread_id)
    assert thread["metadata"]["tool_approval_mode"] == "always_trust"


async def test_thread_tool_approval_mode_rejects_unknown_value(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)

    response = await test_client.put(
        f"/api/chat/thread/{thread_id}",
        headers=admin_headers,
        json={"tool_approval_mode": "unknown"},
    )

    assert response.status_code == 422, response.text


async def test_thread_list_exposes_thread_status(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)

    list_response = await test_client.get("/api/chat/threads", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    thread = next(item for item in list_response.json() if item["id"] == thread_id)
    assert thread["thread_status"] in {"done", "ready", "loading"}


async def test_mark_thread_viewed_returns_thread_status(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)

    response = await test_client.post(f"/api/chat/thread/{thread_id}/viewed", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["thread_status"] in {"done", "ready", "loading"}


async def test_mark_thread_viewed_requires_ownership(test_client, standard_user, admin_headers):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.post(f"/api/chat/thread/{thread_id}/viewed", headers=admin_headers)
    assert response.status_code == 404, response.text


async def test_admin_can_read_default_agent(test_client, admin_headers):
    response = await test_client.get("/api/agent/default", headers=admin_headers)
    assert response.status_code == 200, response.text
    agent = response.json()["agent"]
    assert agent["is_default"] is True
    assert agent["agent_id"]


async def test_agent_detail_filters_configurable_items_by_role(
    test_client,
    admin_headers,
    standard_user,
):
    agents_response = await test_client.get("/api/agent", headers=standard_user["headers"])
    assert agents_response.status_code == 200, agents_response.text
    agents = agents_response.json().get("agents", [])
    if not agents:
        pytest.skip("No agents are registered in the system.")

    agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    if not agent_id:
        pytest.skip("Agent payload missing slug field.")

    user_agent_response = await test_client.get(f"/api/agent/{agent_id}", headers=standard_user["headers"])
    assert user_agent_response.status_code == 200, user_agent_response.text
    user_items = user_agent_response.json()["agent"].get("configurable_items", {})
    assert "summary_threshold" not in user_items
    assert "summary_keep_messages" not in user_items
    assert "summary_prompt" not in user_items
    assert "summary_tool_result_token_limit" not in user_items
    assert "max_execution_steps" not in user_items

    admin_agent_response = await test_client.get(f"/api/agent/{agent_id}", headers=admin_headers)
    assert admin_agent_response.status_code == 200, admin_agent_response.text
    admin_items = admin_agent_response.json()["agent"].get("configurable_items", {})
    assert "summary_threshold" in admin_items
    assert "summary_keep_messages" in admin_items
    assert "summary_prompt" in admin_items
    assert "summary_tool_result_token_limit" in admin_items
    assert "max_execution_steps" in admin_items


async def test_setting_default_agent_requires_admin(test_client, admin_headers, standard_user):
    agents_response = await test_client.get("/api/agent", headers=admin_headers)
    assert agents_response.status_code == 200, agents_response.text
    agents = agents_response.json().get("agents", [])

    if not agents:
        pytest.skip("No agents are registered in the system.")

    candidate_agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    if not candidate_agent_id:
        pytest.skip("Agent payload missing slug field.")

    forbidden_response = await test_client.post(
        f"/api/agent/{candidate_agent_id}/set_default",
        headers=standard_user["headers"],
    )
    assert forbidden_response.status_code == 403

    update_response = await test_client.post(
        f"/api/agent/{candidate_agent_id}/set_default",
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    agent = update_response.json()["agent"]
    assert agent["agent_id"] == candidate_agent_id
    assert agent["is_default"] is True


async def test_save_thread_artifact_to_workspace_copies_output_file(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.md"
    source_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        filename,
        b"# artifact\n",
        artifact_path=True,
    )

    response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": source_path, "destination_path": "/saved_artifacts"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["name"] == filename
    assert payload["source_path"] == source_path
    assert payload["saved_path"] == f"/home/gem/user-data/saved_artifacts/{filename}"

    download_response = await test_client.get(payload["saved_artifact_url"], headers=headers)
    assert download_response.status_code == 200, download_response.text
    assert download_response.text == "# artifact\n"


async def test_save_thread_artifact_to_selected_workspace_directory(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.md"
    source_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        filename,
        b"# selected destination\n",
        artifact_path=True,
    )
    destination_name = f"exports-{uuid.uuid4().hex[:8]}"
    directory = await test_client.post(
        "/api/workspace/directory",
        json={"parent_path": "/", "name": destination_name},
        headers=headers,
    )
    assert directory.status_code == 200, directory.text

    response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": source_path, "destination_path": f"/{destination_name}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["saved_path"] == f"/home/gem/user-data/{destination_name}/{filename}"

    download_response = await test_client.get(payload["saved_artifact_url"], headers=headers)
    assert download_response.status_code == 200, download_response.text
    assert download_response.text == "# selected destination\n"


async def test_save_thread_artifact_to_workspace_auto_renames_conflicts(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.txt"
    renamed_filename = filename.replace(".txt", " (1).txt")

    source_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        filename,
        b"first\n",
        artifact_path=True,
    )

    directory = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={"thread_id": thread_id, "parent_path": "/", "name": "second-source"},
        headers=headers,
    )
    assert directory.status_code == 200, directory.text
    second_source_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        filename,
        b"second\n",
        parent_path=directory.json()["entry"]["path"],
        artifact_path=True,
    )
    save_url = f"/api/chat/thread/{thread_id}/artifacts/save"
    first_response, second_response = await asyncio.gather(
        test_client.post(save_url, json={"path": source_path}, headers=headers),
        test_client.post(save_url, json={"path": second_source_path}, headers=headers),
    )
    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text

    first_payload = first_response.json()
    second_payload = second_response.json()
    assert {first_payload["saved_path"], second_payload["saved_path"]} == {
        f"/home/gem/user-data/saved_artifacts/{filename}",
        f"/home/gem/user-data/saved_artifacts/{renamed_filename}",
    }

    first_download = await test_client.get(first_payload["saved_artifact_url"], headers=headers)
    second_download = await test_client.get(second_payload["saved_artifact_url"], headers=headers)
    assert {first_download.content, second_download.content} == {b"first\n", b"second\n"}


async def test_save_thread_artifact_to_workspace_rejects_invalid_paths(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    invalid_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": "/home/gem/user-data/not-allowed/demo.txt"},
        headers=headers,
    )
    assert invalid_response.status_code == 404, invalid_response.text

    directory = await test_client.post(
        "/api/viewer/filesystem/directory",
        json={"thread_id": thread_id, "parent_path": "/", "name": "nested-dir"},
        headers=headers,
    )
    assert directory.status_code == 200, directory.text
    child_path = await _upload_project_file(
        test_client,
        headers,
        thread_id,
        "child.txt",
        b"child",
        parent_path=directory.json()["entry"]["path"],
        artifact_path=True,
    )
    directory_path = str(PurePosixPath(child_path).parent)
    directory_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": directory_path},
        headers=headers,
    )
    assert directory_response.status_code == 400, directory_response.text
