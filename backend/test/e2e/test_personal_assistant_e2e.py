"""个人助理经过真实 API、worker、消息持久化和文件系统的验收。"""

import asyncio
import json
import uuid

import asyncpg
import pytest
import pytest_asyncio

from test.e2e.e2e_helpers import public_message, register_replay_provider, wait_for_consumed_input, wait_for_terminal_turn
from test.support.openai_replay_server import serve_replay_handler
from test.support.personal_assistant_replay_server import PersonalReplayHandler
from test.support.postgres import postgres_dsn
from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def personal_runtime(e2e_client, e2e_headers):
    """在新用户下创建唯一助理，所有执行仍使用真实 worker。"""
    client = e2e_client
    suffix = uuid.uuid4().hex[:8]
    provider = "personal-" + suffix
    previous = (await client.get("/api/system/config", headers=e2e_headers)).json()
    original_models = {key: previous[key] for key in ("personal_assistant_model", "default_model")}
    with serve_replay_handler(PersonalReplayHandler) as replay:
        await register_replay_provider(client, e2e_headers, provider_id=provider, base_url=replay)
        model = provider + ":deterministic-chat"
        options = await client.post(
            "/api/system/config/update", headers=e2e_headers, json={"personal_assistant_model": model, "default_model": model}
        )
        assert options.status_code == 200, options.text
        departments = (await client.get("/api/departments", headers=e2e_headers)).json()
        created = await client.post(
            "/api/auth/users",
            headers=e2e_headers,
            json={"username": "pytest_pa_" + suffix, "password": "TestPersonal123!", "department_id": departments[0]["id"]},
        )
        assert created.status_code == 200, created.text
        user = created.json()
        token = await client.post("/api/auth/token", data={"username": user["uid"], "password": "TestPersonal123!"})
        assert token.status_code == 200, token.text
        headers = {"Authorization": "Bearer " + token.json()["access_token"]}
        first = await client.post(
            "/api/agents/personal", headers=headers, json={"name": "测试助理", "avatar": "/icons/personal-assistant/orbit.svg"}
        )
        assert first.status_code == 200, first.text
        result = first.json()
        updated = await client.patch(
            "/api/agents/personal", headers=headers, json={"config_json": {"context": {"system_prompt": "PERSONAL_PRIVATE_PREFERENCE"}}}
        )
        assert updated.status_code == 200, updated.text
        db = await asyncpg.connect(postgres_dsn())
        try:
            yield {
                "client": client,
                "headers": headers,
                "admin": e2e_headers,
                "user": user,
                "assistant": result["assistant"],
                "db": db,
                "model": model,
            }
        finally:
            await db.close()
            response = await client.delete(f"/api/auth/users/{user['id']}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
            restored = await client.post("/api/system/config/update", headers=e2e_headers, json=original_models)
            assert restored.status_code == 200, restored.text
            response = await client.delete("/api/system/model-providers/" + provider, headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text


async def submit(runtime, text):
    """提交文字并回读其实际消费到的 Input/Turn。"""
    thread = runtime["assistant"]["thread_id"]
    response = await runtime["client"].post(
        f"/api/v1/agents/sessions/{thread}/events",
        headers={**runtime["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={"events": [{"type": "agent.session.input.message", "input": [public_message(text)], "yuxi": {"mode": "steer"}}]},
    )
    assert response.status_code == 202, response.text
    accepted = await wait_for_consumed_input(runtime["client"], runtime["headers"], response.json())
    terminal = await wait_for_terminal_turn(runtime["client"], runtime["headers"], thread, accepted["turn_id"], timeout=60)
    assert terminal["status"] == "completed", terminal
    return accepted


async def test_personal_channel_is_persisted_and_silent_content_remains_trace_only(personal_runtime):
    """静默执行仍有真实终态，但只有成功工具输出提供用户消息。"""
    r = personal_runtime
    await submit(r, "PERSONAL_SILENT 检查状态，不需要发消息")
    await submit(r, "PERSONAL_SEND 请回答")
    thread = r["assistant"]["thread_id"]
    items = await r["client"].get(f"/api/v1/agents/sessions/{thread}/items", headers=r["headers"], params={"limit": 100, "order": "asc"})
    assert items.status_code == 200, items.text
    raw = items.json()["data"]
    assert any("PERSONAL_INTERNAL_SILENT_CONTENT" in str(item.get("content")) for item in raw)
    outputs = [item for item in raw if item["type"] == "function_call_output"]
    sent = [item for item in outputs if item["call_id"] == "user-send" and item["status"] == "completed"]
    assert len(sent) == 1
    assert "send_to_user" in str([item for item in raw if item["type"] == "function_call"])
    assert "这是通过 send_to_user 发出的消息。" in str(sent[0]["output"])
    assert await r["db"].fetchval("SELECT count(*) FROM agent_inputs WHERE thread_id=$1 AND kind<>'steer'", thread) == 0
    assert await r["db"].fetchval("SELECT count(*) FROM agent_turns WHERE thread_id=$1 AND status='completed'", thread) == 2
    response = await r["client"].post(
        f"/api/v1/agents/sessions/{thread}/events",
        headers={**r["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={
            "events": [{"type": "agent.session.input.message", "input": [public_message("PERSONAL_BLOCK")], "yuxi": {"mode": "follow_up"}}]
        },
    )
    assert response.status_code == 202, response.text
    accepted = await wait_for_consumed_input(r["client"], r["headers"], response.json())
    assert await r["db"].fetchval("SELECT kind FROM agent_inputs WHERE id=$1", accepted["input_id"]) == "follow_up"
    cancelled = await r["client"].post(
        f"/api/v1/agents/sessions/{thread}/events",
        headers={**r["headers"], "Idempotency-Key": str(uuid.uuid4())},
        json={"events": [{"type": "agent.session.input.cancel", "yuxi": {"turn_id": accepted["turn_id"]}}]},
    )
    assert cancelled.status_code == 202, cancelled.text
    terminal = await wait_for_terminal_turn(r["client"], r["headers"], thread, accepted["turn_id"], timeout=60)
    assert terminal["status"] == "cancelled", terminal
    for event_type, stopped in (("yuxi.session.tree.stop", True), ("yuxi.session.tree.continue", False)):
        response = await r["client"].post(
            f"/api/v1/agents/sessions/{thread}/events",
            headers={**r["headers"], "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [{"type": event_type}]},
        )
        assert response.status_code == 202, response.text
        assert await r["db"].fetchval("SELECT stopped FROM session_cooperation_runtimes WHERE tree_root_thread_id=$1", thread) is stopped


@pytest.mark.parametrize("failure", [False, True])
async def test_personal_delegates_root_without_project_or_configuration_inheritance(personal_runtime, failure):
    """独立任务使用普通 Backend、无项目选择、独立目录，并回读成功文件或失败终态。"""
    r = personal_runtime
    accepted = await submit(r, "PERSONAL_DELEGATE " + ("FAIL_TASK" if failure else "请写任务记录"))
    task = None
    for _ in range(150):
        tasks = await r["client"].get("/api/agents/personal/sessions", headers=r["headers"])
        assert tasks.status_code == 200, tasks.text
        rows = tasks.json()["sessions"]
        if rows and rows[0]["turn_status"] in {"completed", "failed"}:
            task = rows[0]
            break
        await asyncio.sleep(0.2)
    assert task is not None, tasks.text
    assert task["turn_status"] == ("failed" if failure else "completed"), task
    row = await r["db"].fetchrow(
        "SELECT s.*,p.selection_status,p.directory_mode,p.workdir_path,a.backend_id,a.config_json AS agent_config "
        "FROM sessions s JOIN projects p ON p.id=s.project_id JOIN agents a ON a.slug=s.agent_id "
        "WHERE s.thread_id=$1",
        task["session_id"],
    )
    assert row["parent_thread_id"] is None and row["tree_root_thread_id"] == task["session_id"]
    assert row["backend_id"] == "ChatbotAgent"
    assert row["selection_status"] == "implicit" and row["directory_mode"] == "managed"
    assert row["project_id"] != r["assistant"]["project_id"] and row["workdir_path"] != "agents/assistant"
    snapshot = json.loads(row["config_snapshot"])
    agent_context = json.loads(row["agent_config"])["context"]
    assert snapshot["system_prompt"] == agent_context.get("system_prompt", "You are a helpful assistant.")
    assert "send_to_user" not in str(snapshot) and "PERSONAL_PRIVATE_PREFERENCE" not in str(snapshot)
    if not failure:
        assert (
            user_workspace_dir(r["user"]["uid"]) / row["workdir_path"] / "personal-task-proof.txt"
        ).read_text() == "PERSONAL_EXECUTOR_FILE_OK"
    # Task completion is queryable but does not create any assistant wake-up input.
    assert await r["db"].fetchval("SELECT count(*) FROM agent_inputs WHERE thread_id=$1", r["assistant"]["thread_id"]) == 1
    assert accepted["run_id"] == row["created_by_run_id"]
    other = await r["client"].get("/api/agents/personal/sessions", headers=r["admin"])
    assert other.status_code == 404 or task["session_id"] not in str(other.json())
