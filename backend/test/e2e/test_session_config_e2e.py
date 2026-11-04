"""真实 HTTP、worker 与数据库验证会话配置生效边界。"""

import os
import asyncio
import json
import uuid

import asyncpg
import httpx
import pytest

from test.e2e.e2e_helpers import archive_public_thread, delete_agent, output_text, postgres_dsn, wait_for_consumed_input
from test.e2e.e2e_helpers import (
    DETERMINISTIC_MODEL as MODEL,
    DETERMINISTIC_OUTPUT as OUTPUT,
    create_lifecycle_agent as _agent,
    public_message as _message,
    register_replay_provider as _provider,
    wait_for_terminal_turn as _turn,
)
from test.live_api_cleanup import delete_test_session_resources, make_test_session_title, validate_test_runs_terminal

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(240)]


async def test_session_snapshot_and_input_freeze_survive_definition_and_session_updates(e2e_client, e2e_headers):
    """已接收输入、当前 steer 和后续输入各自保持正确配置与结果归属。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    await _provider(e2e_client, e2e_headers)
    await _provider(e2e_client, e2e_headers, provider_id="ci-config-replay")
    updated_model = "ci-config-replay:deterministic-chat"
    slug = await _agent(e2e_client, e2e_headers, str(me.json()["uid"]), system_prompt_suffix="FROZEN_SESSION_PROMPT")
    gate = str(uuid.uuid4())
    conn = await asyncpg.connect(postgres_dsn())
    try:
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug, "title": make_test_session_title("config-snapshot")},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        assert created.json()["agent"]["model"] == MODEL
        assert created.json()["yuxi"]["tool_approval_mode"] == "default"
        initial_config = json.loads(await conn.fetchval("SELECT config_snapshot FROM sessions WHERE thread_id=$1", session_id))
        assert initial_config["model"] == MODEL
        assert "FROZEN_SESSION_PROMPT" in initial_config["system_prompt"]
        assert initial_config["tools"] == []
        assert not {"uid", "thread_id", "run_id", "worker_id", "workdir_path"} & initial_config.keys()

        async def submit(text, options=None, key=None):
            """发送新协议事件，返回持久接收回执。"""
            response = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                headers={**e2e_headers, "Idempotency-Key": key or str(uuid.uuid4())},
                json={"events": [{"type": "agent.session.input.message", "input": [_message(text)], "yuxi": options or {}}]},
            )
            assert response.status_code == 202, response.text
            return response.json()

        first = await submit(f"{OUTPUT} DETERMINISTIC_FROZEN_CONFIG DETERMINISTIC_BLOCK_BEFORE_RESPONSE:{gate}")
        async with httpx.AsyncClient(base_url=os.environ["OPENAI_REPLAY_URL"].removesuffix("/v1"), timeout=5) as replay:
            async with asyncio.timeout(30):
                while not (await replay.get("/blocking-started", params={"token": gate})).json()["started"]:
                    await asyncio.sleep(0.1)
            before = await submit(OUTPUT + " BEFORE_UPDATE", {"mode": "follow_up"})
            changed_definition = await e2e_client.put(
                f"/api/agent/{slug}",
                headers=e2e_headers,
                json={
                    "config_json": {
                        "context": {
                            "model": "missing-config-provider:chat",
                            "system_prompt": "CHANGED_DEFINITION_PROMPT",
                            "max_execution_steps": 1,
                            "skills": [],
                            "preload_skills": [],
                        }
                    }
                },
            )
            assert changed_definition.status_code == 200, changed_definition.text
            changed = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}",
                headers=e2e_headers,
                json={"agent": {"model": updated_model}, "yuxi": {"tool_approval_mode": "always_trust"}},
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["agent"]["model"] == updated_model
            assert changed.json()["yuxi"]["tool_approval_mode"] == "always_trust"
            invalid_update = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}", headers=e2e_headers, json={"agent": {"model": "   "}}
            )
            assert invalid_update.status_code == 422, invalid_update.text
            key = str(uuid.uuid4())
            after = await submit(OUTPUT + " AFTER_UPDATE", {"mode": "follow_up"}, key)
            assert await submit(OUTPUT + " AFTER_UPDATE", {"mode": "follow_up"}, key) == after
            override = await submit(OUTPUT + " SINGLE_OVERRIDE", {"mode": "follow_up", "model": MODEL, "tool_approval_mode": "default"})
            steer = await submit(OUTPUT + " ACTIVE_STEER")
            payloads = {}
            for label, receipt in (("before", before), ("after", after), ("override", override), ("steer", steer)):
                payloads[label] = json.loads(await conn.fetchval("SELECT input_payload FROM agent_inputs WHERE id=$1", receipt["input_id"]))
            assert (
                payloads["before"]["context_snapshot"]["tool_approval_mode"]
                == payloads["steer"]["context_snapshot"]["tool_approval_mode"]
                == "default"
            )
            assert payloads["after"]["context_snapshot"]["tool_approval_mode"] == "always_trust"
            assert payloads["before"]["context_snapshot"]["model"] == payloads["steer"]["context_snapshot"]["model"] == MODEL
            assert payloads["after"]["context_snapshot"]["model"] == updated_model
            assert payloads["override"]["context_snapshot"]["model"] == MODEL
            assert payloads["override"]["context_snapshot"]["tool_approval_mode"] == "default"
            for payload in payloads.values():
                assert "model_spec" not in payload and "tool_approval_mode" not in payload
                expected = {
                    **initial_config,
                    "model": payload["context_snapshot"]["model"],
                    "tool_approval_mode": payload["context_snapshot"]["tool_approval_mode"],
                }
                assert payload["context_snapshot"] == expected
            session = await e2e_client.get(f"/api/v1/agents/sessions/{session_id}", headers=e2e_headers)
            assert session.json()["agent"]["model"] == updated_model
            assert session.json()["yuxi"]["tool_approval_mode"] == "always_trust"
            assert await conn.fetchval("SELECT count(*) FROM agent_input_receipts WHERE idempotency_key=$1", key) == 1

            rejected = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"agent_id": slug},
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"]["code"] == "chat_model_not_found"
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE agent_id=$1", slug) == 1
            await replay.get("/release-blocking", params={"token": gate})

        first_input = await wait_for_consumed_input(e2e_client, e2e_headers, first)
        first_turn = await _turn(e2e_client, e2e_headers, session_id, first_input["turn_id"])
        assert first_turn["status"] == "completed", first_turn
        for receipt in (before, after, override):
            async with asyncio.timeout(30):
                while True:
                    response = await e2e_client.get(
                        f"/api/v1/agents/sessions/{session_id}/inputs/{receipt['input_id']}", headers=e2e_headers
                    )
                    assert response.status_code == 200, response.text
                    consumed = response.json()
                    if consumed["status"] == "consumed":
                        break
                    await asyncio.sleep(0.2)
            turn = await _turn(e2e_client, e2e_headers, session_id, consumed["turn_id"])
            assert turn["status"] == "completed", turn
            assert turn["yuxi"]["result_run_id"] == consumed["run_id"]
        runs = await conn.fetch("SELECT id, input_payload, manifest FROM agent_runs WHERE thread_id=$1", session_id)
        assert len(runs) >= 5
        for run in runs:
            payload = json.loads(run["input_payload"])
            manifest = json.loads(run["manifest"])
            assert manifest["model"]["spec"] == payload["context_snapshot"]["model"]
            assert manifest["limits"]["max_execution_steps"] == initial_config["max_execution_steps"]
            assert payload["context_snapshot"]["system_prompt"] == initial_config["system_prompt"]
    finally:
        await conn.close()
        async with httpx.AsyncClient(base_url=os.environ["OPENAI_REPLAY_URL"].removesuffix("/v1"), timeout=5) as replay:
            await replay.get("/release-blocking", params={"token": gate})
        await delete_agent(e2e_client, e2e_headers, slug)
        for provider_id in ("ci-replay", "ci-config-replay"):
            response = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text


@pytest.mark.self_managed_resources
async def test_session_approval_selection_reaches_worker_without_legacy_agent_default(e2e_client, e2e_headers, openai_replay_url):
    """历史智能体审批值不影响新会话，会话审批选择进入真实执行与最终结果。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    provider_id = f"pytest-approval-{uuid.uuid4().hex[:10]}"
    slug = session_id = turn_id = None
    provider_created = False
    conn = await asyncpg.connect(postgres_dsn())
    try:
        provider_created = await _provider(e2e_client, e2e_headers, base_url=openai_replay_url, provider_id=provider_id)
        assert provider_created
        slug = await _agent(
            e2e_client, e2e_headers, str(me.json()["uid"]), model_spec=f"{provider_id}:deterministic-chat", bound_skill=True
        )
        legacy_mode = await conn.fetchval(
            """
            UPDATE agents
            SET config_json = jsonb_set(config_json::jsonb, '{context,tool_approval_mode}', to_jsonb($2::text))
            WHERE slug = $1
            RETURNING config_json #>> '{context,tool_approval_mode}'
            """,
            slug,
            "always_trust",
        )
        assert legacy_mode == "always_trust"
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug, "title": make_test_session_title("approval-selection")},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        assert created.json()["yuxi"]["tool_approval_mode"] == "default"

        for mode in ("default", "always_trust"):
            if mode == "always_trust":
                changed = await e2e_client.post(
                    f"/api/v1/agents/sessions/{session_id}", headers=e2e_headers, json={"yuxi": {"tool_approval_mode": mode}}
                )
                assert changed.status_code == 200, changed.text
                assert changed.json()["yuxi"]["tool_approval_mode"] == mode
            session_config = json.loads(await conn.fetchval("SELECT config_snapshot FROM sessions WHERE thread_id=$1", session_id))
            assert session_config["tool_approval_mode"] == mode
            submitted = await e2e_client.post(
                f"/api/v1/agents/sessions/{session_id}/events",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"events": [{"type": "agent.session.input.message", "input": [_message(OUTPUT)]}]},
            )
            assert submitted.status_code == 202, submitted.text
            consumed = await wait_for_consumed_input(e2e_client, e2e_headers, submitted.json())
            turn_id = consumed["turn_id"]
            turn = await _turn(e2e_client, e2e_headers, session_id, turn_id)
            assert turn["status"] == "completed", turn
            assert turn["yuxi"]["result_run_id"] == consumed["run_id"]
            assert output_text(turn["yuxi"]["output"]) == OUTPUT
            persisted = await conn.fetchrow(
                """
                SELECT run.input_payload, run.manifest, run.status, run.thread_id, run.turn_id,
                       run.output_message_id, turn.result_run_id,
                       output.run_id AS output_run_id, output.turn_id AS output_turn_id, output.content
                FROM agent_runs run
                JOIN agent_turns turn ON turn.id = run.turn_id
                JOIN messages output ON output.id = run.output_message_id
                WHERE run.id = $1
                """,
                consumed["run_id"],
            )
            assert persisted and persisted["status"] == "completed"
            assert json.loads(persisted["input_payload"])["context_snapshot"]["tool_approval_mode"] == mode
            assert json.loads(persisted["manifest"])["tool_approval_mode"] == mode
            assert (persisted["thread_id"], persisted["turn_id"]) == (session_id, turn_id)
            assert (persisted["result_run_id"], persisted["output_run_id"], persisted["output_turn_id"]) == (
                consumed["run_id"],
                consumed["run_id"],
                turn_id,
            )
            assert persisted["output_message_id"] == turn["yuxi"]["output"][0]["yuxi"]["message_id"]
            assert persisted["content"] == OUTPUT
    finally:
        try:
            if session_id:
                await archive_public_thread(e2e_client, e2e_headers, session_id, turn_id=turn_id)
                await validate_test_runs_terminal({session_id})
                project = await conn.fetchrow(
                    "SELECT p.id, p.uid, p.workdir_path, p.directory_mode, p.selection_status "
                    "FROM sessions s JOIN projects p ON p.id = s.project_id AND p.uid = s.uid "
                    "WHERE s.thread_id = $1 AND s.agent_id = $2",
                    session_id,
                    slug,
                )
                assert project and project["directory_mode"] == "managed" and project["selection_status"] == "implicit"
                await delete_test_session_resources(
                    {(project["uid"], project["workdir_path"]): {project["id"]}}, {session_id}, {project["id"]}
                )
        finally:
            await conn.close()
        if slug:
            await delete_agent(e2e_client, e2e_headers, slug)
        if provider_created:
            deleted = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert deleted.status_code in {200, 404}, deleted.text
