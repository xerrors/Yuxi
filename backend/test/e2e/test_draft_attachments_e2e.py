"""真实 worker 验证消息附件归属、共享文件和图片 wire 内容。"""

import os
import asyncio
import base64
import json
import uuid
from io import BytesIO

import asyncpg
import httpx
import pytest
from PIL import Image

from test.e2e.e2e_helpers import (
    DETERMINISTIC_OUTPUT as OUTPUT,
    archive_public_thread,
    cleanup_uploaded_chat_attachment,
    create_lifecycle_agent as _agent,
    delete_agent,
    output_text,
    postgres_dsn,
    public_message as _message,
    register_replay_provider as _provider,
    wait_for_consumed_input,
    wait_for_terminal_turn as _turn,
)
from test.live_api_cleanup import make_test_session_title

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(240)]


async def test_draft_initial_send_queue_cancel_and_model_attachment_isolation(e2e_client, e2e_headers):
    """独立模型 oracle 拒绝未来附件或 JPEG 包装，最终文件与结果从持久边界回读。"""
    await _provider(e2e_client, e2e_headers)
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    slug = await _agent(e2e_client, e2e_headers, uid)
    files = []
    session_id = turn_id = queued_id = second_session = None
    gate = str(uuid.uuid4())
    conn = await asyncpg.connect(postgres_dsn())
    async with httpx.AsyncClient(base_url=os.environ["OPENAI_REPLAY_URL"].removesuffix("/v1"), timeout=5) as replay:
        try:

            async def upload(name, content):
                """上传未关联会话的原文件。"""
                response = await e2e_client.post("/api/v1/agents/files", headers=e2e_headers, files={"file": (name, content, "text/plain")})
                assert response.status_code == 201, response.text
                file = response.json()
                files.append(file)
                return file

            first = await upload("first.txt", b"first input bytes")
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE agent_id=$1", slug) == 0
            png = BytesIO()
            Image.new("RGB", (16, 12), "red").save(png, format="PNG")
            image_url = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
            initial_text = f"{OUTPUT} DETERMINISTIC_PNG_INPUT DETERMINISTIC_ATTACHMENT_ANCHOR:none"
            anchor_text = f"{OUTPUT} DETERMINISTIC_ATTACHMENT_ANCHOR:first.txt DETERMINISTIC_BLOCK_BEFORE_RESPONSE:{gate}"
            initial = _message(initial_text)
            initial["content"].append({"type": "input_image", "image_url": image_url})
            body = {
                "agent_id": slug,
                "title": make_test_session_title("draft-send"),
                "input": [initial, _message(anchor_text)],
                "yuxi": {"attachment_file_ids": [first["id"]]},
            }
            headers = {**e2e_headers, "Idempotency-Key": str(uuid.uuid4())}
            created = await e2e_client.post("/api/v1/agents/sessions", headers=headers, json=body)
            assert created.status_code == 201, created.text
            session = created.json()
            session_id = session["id"]
            receipt = session["yuxi"]["receipt"]
            consumed = await wait_for_consumed_input(e2e_client, e2e_headers, receipt)
            turn_id = consumed["turn_id"]
            original_messages = await conn.fetchval("SELECT messages FROM agent_inputs WHERE id=$1", receipt["input_id"])
            raw = json.loads(original_messages)
            assert [message["content"][0]["text"] for message in raw] == [initial_text, anchor_text]
            assert raw[0]["content"][1] == {"type": "image_url", "image_url": {"url": image_url}}
            assert "<attachment_context>" not in original_messages
            repeated = await e2e_client.post("/api/v1/agents/sessions", headers=headers, json=body)
            assert repeated.status_code == 201, repeated.text
            assert repeated.json()["yuxi"]["receipt"] == receipt
            changed = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers=headers,
                json={**body, "yuxi": {"attachment_file_ids": []}},
            )
            assert changed.status_code == 409, changed.text
            async with asyncio.timeout(30):
                while not (await replay.get("/blocking-started", params={"token": gate})).json()["started"]:
                    await asyncio.sleep(0.1)

            future = await upload("future.txt", b"future queued bytes")
            queued = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [_message(OUTPUT)],
                            "yuxi": {"mode": "follow_up", "attachment_file_ids": [future["id"]]},
                        }
                    ]
                },
            )
            assert queued.status_code == 202, queued.text
            queued_id = queued.json()["input_id"]
            assert queued.json()["turn_id"] is None
            steer = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [
                                _message(
                                    f"{OUTPUT} DETERMINISTIC_ATTACHMENT_ANCHOR:none "
                                    "DETERMINISTIC_ATTACHMENT_ABSENT:first.txt DETERMINISTIC_ATTACHMENT_ABSENT:future.txt"
                                )
                            ],
                            "yuxi": {"mode": "steer"},
                        }
                    ]
                },
            )
            assert steer.status_code == 202, steer.text
            cancelled = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"events": [{"type": "yuxi.session.input.cancel_input", "input_id": queued_id}]},
            )
            assert cancelled.status_code == 202, cancelled.text
            await replay.get("/release-blocking", params={"token": gate})
            steered = await wait_for_consumed_input(e2e_client, e2e_headers, steer.json())
            assert steered["turn_id"] == turn_id
            result = await _turn(e2e_client, e2e_headers, session_id, turn_id)
            assert result["status"] == "completed", result
            assert output_text(result["yuxi"]["output"]) == OUTPUT
            refs_response = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}/attachments", headers=e2e_headers)
            assert refs_response.status_code == 200, refs_response.text
            refs = {record["file_id"]: record for record in refs_response.json()["attachments"]}
            assert set(refs) == {first["id"], future["id"]}
            for file, expected in ((first, b"first input bytes"), (future, b"future queued bytes")):
                content = await e2e_client.get(refs[file["id"]]["artifact_url"], headers=e2e_headers)
                assert content.status_code == 200 and content.content == expected
            assert await conn.fetchval("SELECT status FROM agent_inputs WHERE id=$1", queued_id) == "cancelled"
            assert await conn.fetchval("SELECT consumed_run_id FROM agent_inputs WHERE id=$1", queued_id) is None
            assert await conn.fetchval("SELECT count(*) FROM messages WHERE source_input_id=$1", queued_id) == 0
            assert (
                await conn.fetchval("SELECT count(*) FROM agent_input_receipts WHERE idempotency_key=$1", headers["Idempotency-Key"]) == 1
            )
            payload = json.loads(await conn.fetchval("SELECT input_payload FROM agent_inputs WHERE id=$1", receipt["input_id"]))
            assert not {"attachments_ready", "attachment_drafts", "attachment_error"} & payload.keys()
            rows = await conn.fetch(
                "SELECT status,object_name,parsed_source,input_id,message_id FROM agent_attachments WHERE id=ANY($1::varchar[])",
                [first["id"], future["id"]],
            )
            assert len(rows) == 2 and all(row["status"] == "ready" for row in rows)
            assert all(row["object_name"] is None and row["parsed_source"] is None for row in rows)
            assert {row["input_id"] for row in rows} == {receipt["input_id"], queued_id}
            assert all((row["message_id"] is None) == (row["input_id"] == queued_id) for row in rows)
            anchor = await conn.fetchrow(
                "SELECT m.source_input_id,m.input_position FROM agent_attachments a JOIN messages m ON m.id=a.message_id WHERE a.id=$1",
                first["id"],
            )
            assert (anchor["source_input_id"], anchor["input_position"]) == (receipt["input_id"], 1)
            assert await conn.fetchval("SELECT messages FROM agent_inputs WHERE id=$1", receipt["input_id"]) == original_messages
            originals = await conn.fetch(
                "SELECT content,input_position FROM messages WHERE source_input_id=$1 ORDER BY input_position", receipt["input_id"]
            )
            assert [(row["content"], row["input_position"]) for row in originals] == [(initial_text, 0), (anchor_text, 1)]
            assert await conn.fetchval("SELECT count(*) FROM messages WHERE source_input_id=$1", steered["input_id"]) == 1
            items = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}/items", headers=e2e_headers, params={"order": "asc"})
            assert items.status_code == 200, items.text
            user_item = next(
                item for item in items.json()["data"] if any(part.get("type") == "input_image" for part in item.get("content", []))
            )
            assert user_item["content"][1]["image_url"] == image_url

            project_id = await conn.fetchval("SELECT project_id FROM sessions WHERE thread_id=$1", session_id)
            sibling = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"agent_id": slug, "project_id": project_id, "title": make_test_session_title("shared-files")},
            )
            assert sibling.status_code == 201, sibling.text
            second_session = sibling.json()["id"]
            shared = await e2e_client.get(refs[first["id"]]["artifact_url"].replace(session_id, second_session), headers=e2e_headers)
            assert shared.status_code == 200 and shared.content == b"first input bytes"
        finally:
            await replay.get("/release-blocking", params={"token": gate})
            if session_id:
                if queued_id:
                    await e2e_client.post(
                        f"/api/v1/agents/sessions/{session_id}/events",
                        headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                        json={"events": [{"type": "yuxi.session.input.cancel_input", "input_id": queued_id}]},
                    )
                await archive_public_thread(e2e_client, e2e_headers, session_id, turn_id=turn_id)
            if second_session:
                await archive_public_thread(e2e_client, e2e_headers, second_session)
            await conn.close()
            await delete_agent(e2e_client, e2e_headers, slug)
            for file in files:
                await cleanup_uploaded_chat_attachment(uid, file["id"])
