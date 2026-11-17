"""真实 API、worker、checkpoint 和共享沙盒的 Session 协作链。"""

import asyncio
import json
import os
import sys
import uuid
from contextlib import aclosing

import asyncpg
import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from test.e2e.e2e_helpers import (
    archive_public_thread,
    delete_agent,
    iter_public_thread_events,
    output_text,
    postgres_dsn,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.e2e.e2e_helpers import (
    create_lifecycle_agent as _agent,
)
from test.e2e.e2e_helpers import (
    public_message as _message,
)
from test.e2e.e2e_helpers import (
    register_replay_provider as _provider,
)
from test.live_api_cleanup import (
    cleanup_test_chat_resources,
    delete_test_session_resources,
    make_test_session_title,
    validate_test_runs_terminal,
)
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.agents.runtime.sandbox import UserSandboxBackend, get_sandbox_provider
from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(240)]
OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"
MODEL = "cooperation-replay:deterministic-chat"
TARGET_MODEL = "cooperation-target-replay:deterministic-chat"
SUMMARY_PROMPT = "COOPERATION_SUMMARY_FIXTURE\n{messages}"


@pytest.mark.parametrize(
    "parallel_question,select_agent,cancel_wait",
    [(False, False, False), (True, False, False), (False, True, False), (True, False, True), (False, False, True)],
)
async def test_sessions_use_public_state_and_shared_sandbox(e2e_client, e2e_headers, parallel_question, select_agent, cancel_wait):
    """根工具创建普通会话，结果通过持久等待返回，文件在同一 Project 中。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    uid = me.json()["uid"]
    sandbox_backend = os.getenv("SANDBOX_BACKEND", "cube")
    assert sandbox_backend in {"cube", "container"}
    await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id="cooperation-replay")
    slug = await _agent(
        e2e_client,
        e2e_headers,
        uid,
        model_spec=MODEL,
        tools=["ask_user_question"] if parallel_question else [],
        system_prompt_suffix="COOP_PARENT_CONFIG",
        summary_prompt=SUMMARY_PROMPT,
    )
    target_slug = None
    if select_agent:
        await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id="cooperation-target-replay")
        target_slug = await _agent(
            e2e_client,
            e2e_headers,
            uid,
            model_spec=TARGET_MODEL,
            tools=["ask_user_question"],
            system_prompt_suffix="COOP_TARGET_CONFIG",
            summary_prompt=SUMMARY_PROMPT,
        )
        updated = await e2e_client.put(f"/api/agent/{target_slug}", headers=e2e_headers, json={"description": "COOP_TARGET_ROLE"})
        assert updated.status_code == 200, updated.text
    thread_id = turn_id = workdir = None
    conn = await asyncpg.connect(postgres_dsn())
    try:
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "agent_id": slug,
                "title": make_test_session_title("session-cooperation"),
                "agent": {"model": MODEL},
                "tool_approval_mode": "always_trust",
            },
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        root = await conn.fetchrow(
            "SELECT s.project_id, p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=$1",
            thread_id,
        )
        workdir = root["workdir_path"]
        path = f"/home/user/workspace/{workdir}/cooperation.txt"
        probe_name = f"cooperation-shared-{uuid.uuid4().hex}"
        runtime_probe = f"/tmp/{probe_name}" if sandbox_backend == "cube" else f"/home/user/workspace/{workdir}/{probe_name}"
        question_marker = " PARALLEL_QUESTION" if parallel_question else ""
        selection_marker = f" SELECT_AGENT:{target_slug}" if select_agent else ""
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [
                            _message(
                                f"{OUTPUT} PRIVATE_ROOT_HISTORY COOPERATION_ROOT:{path} "
                                f"COOP_RUNTIME:{runtime_probe}{question_marker}{selection_marker}"
                            )
                        ],
                        "yuxi": {"mode": "follow_up", "tool_approval_mode": "always_trust"},
                    }
                ]
            },
        )
        assert accepted.status_code == 202, accepted.text
        turn_id = (await wait_for_consumed_input(e2e_client, e2e_headers, accepted.json()))["turn_id"]
        # 包含父子执行与周期协作恢复，恢复相位可额外等待一分钟。
        async with asyncio.timeout(180):
            while True:
                response = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=e2e_headers)
                assert response.status_code == 200, response.text
                turn = response.json()
                if (
                    (parallel_question or cancel_wait)
                    and turn["status"] == ("requires_action" if parallel_question else "in_progress")
                    and (turn["yuxi"]["waitpoint"] or {}).get("kind") == ("answer" if parallel_question else "cooperation")
                ):
                    await _assert_thread_activity(
                        e2e_client,
                        e2e_headers,
                        thread_id,
                        "requires_action" if parallel_question else "in_progress",
                    )
                    if cancel_wait:
                        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                        async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
                            before = await saver.aget_tuple(config)
                            prior_messages = list(before.checkpoint["channel_values"]["messages"])
                            for _, channel, values in before.pending_writes or []:
                                if channel == "messages":
                                    prior_messages.extend(values if isinstance(values, list) else [values])
                            completed = [message for message in prior_messages if isinstance(message, ToolMessage)]
                            assert any(message.tool_call_id == "create-worker" for message in completed)
                            cancelled = await e2e_client.post(
                                f"/api/v1/agents/sessions/{thread_id}/events",
                                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                                json={"events": [{"type": "agent.session.input.cancel", "yuxi": {"turn_id": turn_id}}]},
                            )
                            assert cancelled.status_code == 202, cancelled.text
                            snapshot = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=e2e_headers)
                            assert snapshot.json()["status"] == "cancelled", snapshot.text
                            after = await saver.aget_tuple(config)
                            messages = after.checkpoint["channel_values"]["messages"]
                            assert all(message in messages for message in completed)
                            assert any(
                                isinstance(message, ToolMessage)
                                and message.tool_call_id == ("parallel-question" if parallel_question else "wait-worker")
                                and message.content == "[已取消]"
                                for message in messages
                            )
                            assert not any(channel == "__interrupt__" for _, channel, _ in after.pending_writes or [])
                        assert await conn.fetchval("SELECT result_run_id FROM agent_turns WHERE id=$1", turn_id) is None
                        return
                    resumed = await e2e_client.post(
                        f"/api/v1/agents/sessions/{thread_id}/events",
                        headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                        json={
                            "events": [
                                {
                                    "type": "yuxi.session.input.resume",
                                    "turn_id": turn_id,
                                    "waitpoint_id": turn["yuxi"]["waitpoint"]["id"],
                                    "response": {
                                        "type": "answer",
                                        "answers": [{"question_id": "confirm", "answer": "继续"}],
                                    },
                                }
                            ]
                        },
                    )
                    assert resumed.status_code == 202, resumed.text
                if turn["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.2)
        assert turn["status"] == "completed", turn
        assert output_text(turn["yuxi"]["output"]) == OUTPUT
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE parent_thread_id=$1", thread_id) == 1
        child = await conn.fetchrow("SELECT * FROM sessions WHERE parent_thread_id=$1", thread_id)
        assert child and child["status"] == "active"
        assert child["tree_root_thread_id"] == thread_id and child["project_id"] == root["project_id"]
        run = await conn.fetchrow("SELECT * FROM agent_runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 1", child["thread_id"])
        assert run["run_type"] == "chat" and run["runtime_scope_id"] == thread_id and run["status"] == "completed"
        snapshot = json.loads(child["config_snapshot"])
        payload = json.loads(run["input_payload"])
        assert json.loads(run["manifest"])["model"]["spec"] == (TARGET_MODEL if select_agent else MODEL)
        assert child["agent_id"] == run["agent_slug"] == (target_slug if select_agent else slug)
        assert payload["context_snapshot"]["model"] == (TARGET_MODEL if select_agent else MODEL)
        assert payload["context_snapshot"]["tool_approval_mode"] == "always_trust"
        assert "model_spec" not in payload and "tool_approval_mode" not in payload
        if select_agent:
            assert "COOP_TARGET_CONFIG" in snapshot["system_prompt"]
            assert "COOP_PARENT_CONFIG" not in snapshot["system_prompt"]
            assert snapshot["tools"] == ["present_artifacts", "ask_user_question"]
        else:
            assert "COOP_PARENT_CONFIG" in snapshot["system_prompt"]
        state = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/state", headers=e2e_headers)
        assert state.status_code == 200, state.text
        member = next(s for s in state.json()["agent_state"]["cooperation"]["sessions"] if s["session_id"] == child["thread_id"])
        assert "output" not in member and member["result_run_id"] == run["id"]
        assert (user_workspace_dir(uid) / root["workdir_path"] / "cooperation.txt").read_text() == "cooperation verified"
        child_turn = await e2e_client.get(f"/api/v1/agents/sessions/{child['thread_id']}/turns/{run['turn_id']}", headers=e2e_headers)
        assert child_turn.status_code == 200 and output_text(child_turn.json()["yuxi"]["output"]) == OUTPUT
        assert json.loads(child["config_snapshot"])["system_prompt"].count("用户工作区 agents/AGENTS.md") == 0
        provider = get_sandbox_provider()
        backend = UserSandboxBackend(uid=uid, workdir_path=root["workdir_path"])
        missing_path = path + ".missing.txt"
        for read_result in [await asyncio.to_thread(backend.read, missing_path), await backend.aread(missing_path)]:
            assert read_result.error == f"File '{missing_path}' not found"
            assert "ResponseFileReadResult" not in read_result.error and "validation errors" not in read_result.error
        assert (await asyncio.to_thread(backend.write, missing_path, "write then read verified")).error is None
        for read_result in [await asyncio.to_thread(backend.read, missing_path), await backend.aread(missing_path)]:
            assert read_result.error is None and read_result.file_data["content"] == "write then read verified"
        temporary = f"/tmp/cooperation-{uuid.uuid4().hex}"
        if sandbox_backend == "cube":
            connection = await asyncio.to_thread(provider.get, uid)
            assert connection is not None
            assert (await asyncio.to_thread(backend.execute, f"printf transient > {temporary}")).exit_code == 0
        # 两种后端都保留 Project 文件；持久 VM 额外保留同一 /tmp 与执行身份。
        for target in (thread_id, child["thread_id"]):
            compressed = await e2e_client.post(f"/api/v1/agents/sessions/{target}/compress", headers=e2e_headers)
            assert compressed.status_code == 200, compressed.text
            preserved = await backend.aread(path)
            assert preserved.error is None and preserved.file_data["content"] == "cooperation verified"
            if sandbox_backend == "cube":
                preserved = await asyncio.to_thread(backend.execute, f"cat {temporary}")
                assert preserved.exit_code == 0 and preserved.output == "transient"
                assert (await asyncio.to_thread(provider.get, uid)).sandbox_id == connection.sandbox_id
        if sandbox_backend == "cube":
            preserved = await asyncio.to_thread(backend.execute, f"cat {path} && cat {temporary}")
            assert preserved.exit_code == 0 and preserved.output == "cooperation verifiedtransient"
            assert (await asyncio.to_thread(provider.get, uid)).sandbox_id == connection.sandbox_id
            assert (await asyncio.to_thread(backend.execute, f"rm -- {temporary}")).exit_code == 0
        else:
            async with asyncio.timeout(30):
                while True:
                    status = await e2e_client.get("/api/user/sandbox", headers=e2e_headers)
                    assert status.status_code == 200, status.text
                    state = status.json()
                    assert state["backend"] == "container", state
                    if not state["active_runs"] and not state["busy"] and state["state"] == "not_created":
                        break
                    await asyncio.sleep(0.1)

    finally:
        await conn.close()
        if thread_id:
            members = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/state", headers=e2e_headers)
            assert members.status_code == 200, members.text
            for member in members.json()["agent_state"]["cooperation"]["sessions"]:
                if member["session_id"] != thread_id:
                    await archive_public_thread(e2e_client, e2e_headers, member["session_id"], turn_id=member["turn_id"])
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        await delete_agent(e2e_client, e2e_headers, slug)
        if target_slug is not None:
            await delete_agent(e2e_client, e2e_headers, target_slug)


@pytest.mark.self_managed_resources
async def test_parallel_wait_inputs_keep_both_interrupts_and_resume_exact_results(e2e_client, e2e_headers, cooperation_parallelism_two):
    """真实 worker 保存两个并行等待，两个子任务释放 gate 后按 ID 恢复同一 Turn。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = me.json()["uid"]
    provider_id = f"cooperation-parallel-{uuid.uuid4().hex[:8]}"
    model = f"{provider_id}:deterministic-chat"
    slug = root_id = root_turn = None
    gates = []
    conn = await asyncpg.connect(postgres_dsn())

    def release_gate(path):
        """原子发布跨进程解除信号。"""
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("released")
        temporary.replace(path)

    try:
        await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id=provider_id)
        slug = await _agent(e2e_client, e2e_headers, uid, model_spec=model, summary_prompt=SUMMARY_PROMPT)
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug, "title": make_test_session_title("cooperation-parallel-wait"), "tool_approval_mode": "always_trust"},
        )
        assert created.status_code == 201, created.text
        root_id = created.json()["id"]
        workdir = await conn.fetchval(
            "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=$1", root_id
        )
        gate_name = f"parallel-wait-{uuid.uuid4().hex}"
        gates = [user_workspace_dir(uid) / workdir / f"{gate_name}-{index}" for index in range(2)]
        gate_path = f"/home/user/workspace/{workdir}/{gate_name}"
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{root_id}/events",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [_message(f"COOP_PARALLEL_WAIT_ROOT PRIVATE_ROOT_HISTORY WAIT_GATE:{gate_path}")],
                        "yuxi": {"mode": "follow_up", "tool_approval_mode": "always_trust"},
                    }
                ]
            },
        )
        assert accepted.status_code == 202, accepted.text
        root_turn = (await wait_for_consumed_input(e2e_client, e2e_headers, accepted.json()))["turn_id"]
        async with asyncio.timeout(60):
            while True:
                waiting = await conn.fetchrow("SELECT status,current_run_id,waitpoint FROM agent_turns WHERE id=$1", root_turn)
                assert waiting["status"] not in {"completed", "failed", "cancelled"}, dict(waiting)
                if waiting["status"] == "waiting":
                    waitpoint = json.loads(waiting["waitpoint"])
                    assert waitpoint["kind"] == "cooperation" and len(waitpoint["waits"]) == 2, waitpoint
                    if all(path.with_name(path.name + "-started").exists() for path in gates):
                        break
                await asyncio.sleep(0.1)
        entries = waitpoint["waits"]
        assert len({entry["interrupt_id"] for entry in entries}) == 2
        children = await conn.fetch(
            "SELECT s.thread_id,s.cooperation_name,i.id AS input_id,i.turn_id FROM sessions s "
            "JOIN agent_inputs i ON i.thread_id=s.thread_id WHERE s.parent_thread_id=$1 ORDER BY s.cooperation_name",
            root_id,
        )
        assert [child["cooperation_name"] for child in children] == ["parallel0", "parallel1"]
        assert {tuple(entry["target_inputs"]) for entry in entries} == {(child["input_id"],) for child in children}
        assert await conn.fetchval("SELECT status FROM agent_runs WHERE id=$1", waiting["current_run_id"]) == "interrupted"
        state = await e2e_client.get(f"/api/v1/agents/sessions/{root_id}/state", headers=e2e_headers)
        assert state.status_code == 200, state.text
        assert {entry["interrupt_id"]: entry for entry in state.json()["interrupt"]["waits"]} == {
            entry["interrupt_id"]: entry for entry in entries
        }
        async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": root_id, "checkpoint_ns": ""}})
            pending_ids = {item.id for _, channel, items in checkpoint.pending_writes or [] if channel == "__interrupt__" for item in items}
            assert pending_ids == {entry["interrupt_id"] for entry in entries}

        release_gate(gates[0])
        first = await wait_for_terminal_turn(e2e_client, e2e_headers, children[0]["thread_id"], children[0]["turn_id"], timeout=60)
        assert first["status"] == "completed" and output_text(first["yuxi"]["output"]) == "PARALLEL_CHILD_RESULT_0", first
        second_entry = next(entry for entry in entries if entry["target_inputs"] == [children[1]["input_id"]])
        async with asyncio.timeout(90):
            while True:
                partial = await conn.fetchrow("SELECT status,current_run_id,waitpoint FROM agent_turns WHERE id=$1", root_turn)
                assert partial["status"] not in {"completed", "failed", "cancelled"}, dict(partial)
                if partial["status"] == "waiting" and partial["current_run_id"] != waiting["current_run_id"]:
                    remaining = json.loads(partial["waitpoint"])["waits"]
                    assert remaining == [second_entry], remaining
                    break
                await asyncio.sleep(0.1)
        assert not gates[1].exists()
        release_gate(gates[1])
        final = await wait_for_terminal_turn(e2e_client, e2e_headers, root_id, root_turn, timeout=120)
        assert final["status"] == "completed" and output_text(final["yuxi"]["output"]) == OUTPUT, final
        runs = await conn.fetch(
            "SELECT id,status,run_type,input_message_id FROM agent_runs WHERE turn_id=$1 ORDER BY created_at", root_turn
        )
        assert [(run["run_type"], run["status"]) for run in runs] == [
            ("chat", "interrupted"),
            ("resume", "interrupted"),
            ("resume", "completed"),
        ]
        persisted = await conn.fetchrow("SELECT waitpoint,result_run_id FROM agent_turns WHERE id=$1", root_turn)
        assert json.loads(persisted["waitpoint"] or "null") is None
        assert persisted["result_run_id"] == runs[-1]["id"]
        responses = {}
        for run in runs[1:]:
            metadata = json.loads(await conn.fetchval("SELECT extra_metadata FROM messages WHERE id=$1", run["input_message_id"]))
            assert len(metadata["resume"]) == 1 and not responses.keys() & metadata["resume"].keys()
            responses.update(metadata["resume"])
        assert set(responses) == {entry["interrupt_id"] for entry in entries}
        for index, child in enumerate(children):
            entry = next(entry for entry in entries if entry["target_inputs"] == [child["input_id"]])
            resumed = responses[entry["interrupt_id"]]
            assert resumed["wait_timed_out"] is False
            assert len(resumed["results"]) == 1
            result = resumed["results"][0]
            assert result["input_id"] == child["input_id"] and result["terminal"]
            assert result["output"] == f"PARALLEL_CHILD_RESULT_{index}"
        async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": root_id, "checkpoint_ns": ""}})
            assert not any(channel == "__interrupt__" for _, channel, _ in checkpoint.pending_writes or [])
            completed = {
                message.tool_call_id for message in checkpoint.checkpoint["channel_values"]["messages"] if isinstance(message, ToolMessage)
            }
            assert {"parallel-wait-0", "parallel-wait-1"} <= completed
        streamed = []
        async with asyncio.timeout(15), aclosing(iter_public_thread_events(e2e_client, e2e_headers, root_id)) as events:
            async for event in events:
                streamed.append(event)
                if event["type"] == "agent.session.turn.completed" and event["turn_id"] == root_turn:
                    break
        assert streamed[-1]["yuxi"]["run_id"] == runs[-1]["id"]
        assert {
            event["yuxi"]["run_id"]
            for event in streamed
            if event["type"] == "yuxi.session.run.settled" and event["status"] == "interrupted"
        } == {run["id"] for run in runs[:-1]}
        await validate_test_runs_terminal({root_id, *(child["thread_id"] for child in children)})
    finally:
        body_error = sys.exception()
        for path in gates:
            release_gate(path)
        try:
            if root_id:
                members = await conn.fetch(
                    "SELECT s.thread_id,(SELECT t.id FROM agent_turns t WHERE t.thread_id=s.thread_id "
                    "ORDER BY t.created_at DESC LIMIT 1) AS turn_id "
                    "FROM sessions s WHERE s.tree_root_thread_id=$1 ORDER BY length(s.cooperation_path) DESC",
                    root_id,
                )
                for member in members:
                    await archive_public_thread(e2e_client, e2e_headers, member["thread_id"], turn_id=member["turn_id"])
                await validate_test_runs_terminal({member["thread_id"] for member in members})
            if slug:
                await delete_agent(e2e_client, e2e_headers, slug)
            removed = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
        except Exception as cleanup_error:
            if body_error is not None:
                body_error.add_note(f"测试清理另有失败: {cleanup_error}")
                raise body_error from cleanup_error
            raise
        finally:
            await conn.close()
            for path in gates:
                path.unlink(missing_ok=True)
                path.with_name(path.name + "-started").unlink(missing_ok=True)


@pytest.mark.parametrize("stop_tree", [False, True])
async def test_full_tree_capacity_control_and_sibling_tools(e2e_client, e2e_headers, stop_tree, actors):
    """真实 worker 满槽等待推进；兄弟工具与持久整树控制不串轮次。"""
    from yuxi.modules.agents.services.cooperation import reconcile_stopped_trees

    await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id="cooperation-replay")
    # list_sessions 按账户列出历史记录；独立身份把本例列表固定在当前六名成员内。
    e2e_headers = actors["identities"]["admin0"]["headers"]
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    slug = await _agent(
        e2e_client,
        e2e_headers,
        me.json()["uid"],
        model_spec=MODEL,
        summary_prompt=SUMMARY_PROMPT,
        summary_tool_result_token_limit=4096,
        bound_skill=True,
    )
    conn = await asyncpg.connect(postgres_dsn())
    root_id = root_turn = gate_file = workdir = None

    async def event(thread_id, payload):
        """通过真实公开入口提交独立输入或控制。"""
        result = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [payload]},
        )
        assert result.status_code == 202, result.text
        return result.json()

    try:
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "agent_id": slug,
                "title": make_test_session_title("cooperation-capacity"),
                "agent": {"model": MODEL},
                "tool_approval_mode": "always_trust",
            },
        )
        assert created.status_code == 201, created.text
        root_id = created.json()["id"]
        workdir = await conn.fetchval(
            "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=$1", root_id
        )
        gate_file = user_workspace_dir(me.json()["uid"]) / workdir / "capacity-release"
        gate_path = f"/home/user/workspace/{workdir}/capacity-release"
        accepted = await event(
            root_id,
            {
                "type": "agent.session.input.message",
                "input": [_message(f"COOP_CAPACITY_ROOT PRIVATE_ROOT_HISTORY CAP_GATE:{gate_path}")],
                "yuxi": {"mode": "follow_up", "tool_approval_mode": "always_trust"},
            },
        )
        root_turn = (await wait_for_consumed_input(e2e_client, e2e_headers, accepted))["turn_id"]
        async with asyncio.timeout(90):
            while True:
                observed = await conn.fetchrow(
                    "SELECT (SELECT count(*) FROM agent_runs WHERE runtime_scope_id=$1 "
                    "AND status IN ('running','cancel_requested') AND worker_id IS NOT NULL) AS active, "
                    "(SELECT status='waiting' FROM agent_turns WHERE id=$2) AS waiting, "
                    "(SELECT count(*) FROM agent_runs WHERE runtime_scope_id=$1 AND thread_id<>$1 "
                    "AND status='running' AND worker_id IS NOT NULL) AS children",
                    root_id,
                    root_turn,
                )
                assert observed["active"] <= 4
                if observed["children"] == 4 and observed["waiting"]:
                    break
                await asyncio.sleep(0.05)
        await _assert_thread_activity(e2e_client, e2e_headers, root_id, "in_progress")
        members = await conn.fetch(
            "SELECT thread_id,cooperation_path FROM sessions WHERE tree_root_thread_id=$1 ORDER BY cooperation_path",
            root_id,
        )
        assert len(members) == 6
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE uid=$1", me.json()["uid"]) == 6
        ready_file = gate_file.with_name(gate_file.name + "-ready")
        if not stop_tree:
            ready_file.write_text("released")
        queued = None
        if stop_tree:
            queued = await event(
                root_id,
                {
                    "type": "agent.session.input.message",
                    "input": [_message("COOP_CAPACITY_FOLLOWUP")],
                    "yuxi": {"mode": "follow_up", "tool_approval_mode": "always_trust"},
                },
            )
            assert queued["turn_id"] is None
            await event(root_id, {"type": "yuxi.session.tree.stop"})
            # 独立测试进程重读持久停止意图，与真实 worker 同时重复执行恢复。
            load_models()
            pg_manager.initialize()
            try:
                await reconcile_stopped_trees()
                await reconcile_stopped_trees()
            finally:
                await pg_manager.close()
            gate_file.write_text("released")
            ready_file.write_text("released")
        async with asyncio.timeout(90):
            while True:
                active = await conn.fetchval(
                    "SELECT count(*) FROM agent_runs WHERE runtime_scope_id=$1 "
                    "AND status IN ('running','cancel_requested') AND worker_id IS NOT NULL",
                    root_id,
                )
                assert active <= 4
                turns = await conn.fetch(
                    "SELECT id,status,thread_id FROM agent_turns WHERE thread_id=ANY($1::varchar[])",
                    [m["thread_id"] for m in members],
                )
                if turns and all(t["status"] in {"completed", "failed", "cancelled"} for t in turns):
                    break
                await asyncio.sleep(0.1)
        assert not any(t["status"] == "failed" for t in turns), turns
        if stop_tree:
            assert all(t["status"] == "cancelled" for t in turns)
            assert await conn.fetchval("SELECT stopped FROM session_cooperation_runtimes WHERE tree_root_thread_id=$1", root_id)
            assert await conn.fetchval(
                "SELECT bool_and(s.queue_paused = EXISTS("
                "SELECT 1 FROM agent_inputs i WHERE i.thread_id=s.thread_id AND i.status='pending')) "
                "FROM sessions s WHERE s.tree_root_thread_id=$1",
                root_id,
            )
            assert await conn.fetchval("SELECT status FROM agent_inputs WHERE id=$1", queued["input_id"]) == "pending"
            await _assert_thread_activity(e2e_client, e2e_headers, root_id, "cancelled")
            await event(root_id, {"type": "yuxi.session.tree.continue"})
            async with asyncio.timeout(60):
                while True:
                    resumed = await conn.fetchrow("SELECT turn_id,status FROM agent_inputs WHERE id=$1", queued["input_id"])
                    if (
                        resumed["turn_id"]
                        and await conn.fetchval("SELECT status FROM agent_turns WHERE id=$1", resumed["turn_id"]) == "completed"
                    ):
                        break
                    await asyncio.sleep(0.1)
            assert resumed["turn_id"] != root_turn
            assert await conn.fetchval("SELECT status FROM agent_turns WHERE id=$1", root_turn) == "cancelled"
        else:
            victim = next(m for m in members if m["cooperation_path"] == "/root/worker4")
            recipient = next(m for m in members if m["cooperation_path"] == "/root/worker1")
            assert next(t for t in turns if t["thread_id"] == victim["thread_id"])["status"] == "cancelled"
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM session_cooperation_events WHERE recipient_thread_id=$1 "
                    "AND kind='message' AND content='SIBLING_INFORMATION'",
                    recipient["thread_id"],
                )
                == 1
            )
            assert await conn.fetchval("SELECT count(*) FROM agent_inputs WHERE thread_id=$1", recipient["thread_id"]) == 2
            assert all(t["status"] == "completed" for t in turns if t["thread_id"] != victim["thread_id"])
    finally:
        body_error = sys.exception()
        if gate_file is not None:
            gate_file.write_text("released")
            gate_file.with_name(gate_file.name + "-ready").write_text("released")
        try:
            if root_id:
                state = await e2e_client.get(f"/api/v1/agents/sessions/{root_id}/state", headers=e2e_headers)
                assert state.status_code == 200, state.text
                for member in reversed(state.json()["agent_state"]["cooperation"]["sessions"]):
                    queue = await e2e_client.get(f"/api/v1/agents/sessions/{member['session_id']}/queue", headers=e2e_headers)
                    assert queue.status_code == 200, queue.text
                    for pending in queue.json()["inputs"]:
                        await event(
                            member["session_id"],
                            {"type": "yuxi.session.input.cancel_input", "input_id": pending["input_id"]},
                        )
                    try:
                        await archive_public_thread(e2e_client, e2e_headers, member["session_id"], turn_id=member["turn_id"])
                    except TimeoutError as error:
                        diagnostic = await asyncpg.connect(postgres_dsn())
                        try:
                            blockers = await diagnostic.fetch(
                                "SELECT id,status,worker_id,runtime_cleanup_pending FROM agent_runs "
                                "WHERE thread_id=$1 AND (status NOT IN "
                                "('completed','failed','cancelled','interrupted') "
                                "OR runtime_cleanup_pending)",
                                member["session_id"],
                            )
                            pending = await diagnostic.fetch(
                                "SELECT id,status,turn_id FROM agent_inputs WHERE thread_id=$1 AND status='pending'",
                                member["session_id"],
                            )
                        finally:
                            await diagnostic.close()
                        error.add_note(f"Archive blockers: session={member['session_id']}, runs={blockers}, pending={pending}")
                        raise
            await delete_agent(e2e_client, e2e_headers, slug)
            await cleanup_test_chat_resources(e2e_client, e2e_headers, owner_uid=str(me.json()["uid"]))
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE uid=$1", me.json()["uid"]) == 0
            if workdir:
                assert not (user_workspace_dir(me.json()["uid"]) / workdir).exists()
        except Exception as cleanup_error:
            if body_error is not None:
                body_error.add_note(f"测试清理另有失败: {cleanup_error}")
                raise body_error from cleanup_error
            raise
        finally:
            await conn.close()


async def _assert_thread_activity(client, headers, thread_id, expected):
    """真实 HTTP 查看不改变 Turn 等待，列表与快照保持同一活动投影。"""
    detail = await client.get(f"/api/v1/agents/sessions/{thread_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    viewed = await client.post(f"/api/v1/agents/sessions/{thread_id}/viewed", headers=headers)
    assert viewed.status_code == 200, viewed.text
    for snapshot in (detail.json(), viewed.json()):
        assert snapshot["status"] == expected, snapshot
    listed = await client.get("/api/v1/agents/sessions", headers=headers, params={"limit": 100})
    assert listed.status_code == 200, listed.text
    thread = next(item for item in listed.json()["data"] if item["id"] == thread_id)
    assert thread["status"] == expected, thread


@pytest.fixture
async def cooperation_parallelism_two(e2e_client, e2e_headers):
    """为两个阻塞子任务提供明确名额，并恢复管理员原配置。"""
    current = await e2e_client.get("/api/system/config", headers=e2e_headers)
    assert current.status_code == 200, current.text
    previous = current.json()["max_cooperation_concurrency"]
    try:
        changed = await e2e_client.post("/api/system/config/update", headers=e2e_headers, json={"max_cooperation_concurrency": 2})
        assert changed.status_code == 200, changed.text
        yield
    finally:
        restored = await e2e_client.post("/api/system/config/update", headers=e2e_headers, json={"max_cooperation_concurrency": previous})
        assert restored.status_code == 200, restored.text


@pytest.fixture
async def cooperation_depth_two(e2e_client, e2e_headers):
    """显式设置测试深度并恢复原值，不依赖管理员当前配置。"""
    current = await e2e_client.get("/api/system/config", headers=e2e_headers)
    assert current.status_code == 200, current.text
    previous = current.json()["max_cooperation_depth"]
    try:
        changed = await e2e_client.post("/api/system/config/update", headers=e2e_headers, json={"max_cooperation_depth": 2})
        assert changed.status_code == 200, changed.text
        yield
    finally:
        restored = await e2e_client.post("/api/system/config/update", headers=e2e_headers, json={"max_cooperation_depth": previous})
        assert restored.status_code == 200, restored.text


async def test_worker_rejects_third_cooperation_depth_and_commits_only_allowed_members(e2e_client, e2e_headers, cooperation_depth_two):
    """真实 API、worker 与工具执行拒绝第三层，回读会话和输入确认无副作用。"""
    me = (await e2e_client.get("/api/auth/me", headers=e2e_headers)).json()
    await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id="cooperation-replay")
    slug = await _agent(e2e_client, e2e_headers, me["uid"], model_spec=MODEL, tools=[])
    thread_id = turn_id = None
    conn = await asyncpg.connect(postgres_dsn())
    try:
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug, "title": make_test_session_title("cooperation-depth"), "tool_approval_mode": "always_trust"},
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [{"type": "agent.session.input.message", "input": [_message(f"{OUTPUT} COOP_DEPTH:0")]}]},
        )
        assert accepted.status_code == 202, accepted.text
        turn_id = (await wait_for_consumed_input(e2e_client, e2e_headers, accepted.json()))["turn_id"]
        async with asyncio.timeout(180):
            while True:
                turn = (await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=e2e_headers)).json()
                if turn["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.5)
        assert turn["status"] == "completed", turn
        assert output_text(turn["yuxi"]["output"]) == OUTPUT
        rows = await conn.fetch("SELECT thread_id, cooperation_path FROM sessions WHERE tree_root_thread_id=$1", thread_id)
        assert {row["cooperation_path"] for row in rows} == {"/root", "/root/depth-1", "/root/depth-1/depth-2"}
        count = await conn.fetchval("SELECT count(*) FROM agent_inputs WHERE thread_id=ANY($1::text[])", [row["thread_id"] for row in rows])
        assert count == 3
        statuses = await conn.fetch("SELECT status FROM agent_turns WHERE thread_id=ANY($1::text[])", [row["thread_id"] for row in rows])
        assert {row["status"] for row in statuses} == {"completed"}
        denied = await conn.fetchval(
            "SELECT count(*) FROM messages m "
            "JOIN sessions s ON s.id=m.session_record_id WHERE s.tree_root_thread_id=$1 "
            "AND m.message_type='tool_audit' AND m.extra_metadata->>'tool_name'='create_session' "
            "AND m.execution_status='failed' "
            "AND coalesce(m.extra_metadata->>'error_message',m.content,'') LIKE '%协作深度 3%'",
            thread_id,
        )
        assert denied == 1
    finally:
        await conn.close()
        if thread_id:
            state = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/state", headers=e2e_headers)
            for member in state.json()["agent_state"]["cooperation"]["sessions"]:
                if member["session_id"] != thread_id:
                    await archive_public_thread(e2e_client, e2e_headers, member["session_id"], turn_id=member["turn_id"])
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        await delete_agent(e2e_client, e2e_headers, slug)


@pytest.mark.self_managed_resources
@pytest.mark.parametrize("different_project", [False, True])
async def test_independent_root_cooperation_uses_real_worker_and_persisted_origins(e2e_client, e2e_headers, different_project, actors):
    """通过模型工具创建独立根，再跨树投递与等待，来源从 HTTP 和数据库回读。"""
    await _provider(e2e_client, e2e_headers, base_url=os.environ["COOPERATION_REPLAY_URL"], provider_id="cooperation-replay")
    e2e_headers = actors["identities"]["admin0"]["headers"]
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    uid = me.json()["uid"]
    slug = await _agent(e2e_client, e2e_headers, uid, model_spec=MODEL, summary_tool_result_token_limit=4096, bound_skill=True)
    root_id = target_id = turn_id = None
    conn = await asyncpg.connect(postgres_dsn())
    try:
        response = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "agent_id": slug,
                "title": make_test_session_title("user-scope-cooperation"),
                "agent": {"model": MODEL},
                "tool_approval_mode": "always_trust",
            },
        )
        assert response.status_code == 201, response.text
        root_id = response.json()["id"]
        marker = "USER_SCOPE_ROOT PRIVATE_ROOT_HISTORY"
        if different_project:
            other = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={
                    "agent_id": slug,
                    "title": make_test_session_title("cross-project-target"),
                    "agent": {"model": MODEL},
                    "tool_approval_mode": "always_trust",
                },
            )
            assert other.status_code == 201, other.text
            target_id = other.json()["id"]
            marker += f" USER_SCOPE_EXISTING:{target_id}"
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{root_id}/events",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"events": [{"type": "agent.session.input.message", "input": [_message(marker)], "yuxi": {"mode": "follow_up"}}]},
        )
        assert accepted.status_code == 202, accepted.text
        turn_id = (await wait_for_consumed_input(e2e_client, e2e_headers, accepted.json()))["turn_id"]
        terminal = await wait_for_terminal_turn(e2e_client, e2e_headers, root_id, turn_id, timeout=180)
        assert terminal["status"] == "completed", terminal
        assert output_text(terminal["yuxi"]["output"]) == OUTPUT
        if different_project:
            target = await conn.fetchrow("SELECT * FROM sessions WHERE thread_id=$1", target_id)
        else:
            target = await conn.fetchrow(
                "SELECT * FROM sessions WHERE created_by_run_id IN (SELECT id FROM agent_runs WHERE thread_id=$1)", root_id
            )
        assert target is not None and target["parent_thread_id"] is None
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE uid=$1", uid) == 2
        target_id = target["thread_id"]
        assert target["tree_root_thread_id"] == target_id and target_id != root_id
        root_project = await conn.fetchval("SELECT project_id FROM sessions WHERE thread_id=$1", root_id)
        assert (target["project_id"] != root_project) == different_project
        if different_project:
            source_path = await conn.fetchval("SELECT workdir_path FROM projects WHERE id=$1", root_project)
            target_path = await conn.fetchval("SELECT workdir_path FROM projects WHERE id=$1", target["project_id"])
            assert (user_workspace_dir(uid) / target_path / "cooperation-target-only.txt").read_text() == "TARGET_PROJECT_VERIFIED"
            assert not (user_workspace_dir(uid) / source_path / "cooperation-target-only.txt").exists()
        inputs = await conn.fetch("SELECT id, origin_metadata FROM agent_inputs WHERE thread_id=$1 ORDER BY received_seq", target_id)
        assert len(inputs) == 2
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM session_cooperation_events WHERE recipient_thread_id=$1 AND content='CROSS_TREE_MESSAGE'", target_id
            )
            == 1
        )
        origins = await e2e_client.get(
            f"/api/agent/threads/{target_id}/input-origins", headers=e2e_headers, params=[("input_ids", item["id"]) for item in inputs]
        )
        assert origins.status_code == 200, origins.text
        origin_items = origins.json()["origins"]
        assert set(origin_items) == {item["id"] for item in inputs}
        assert all(origin["target"]["session_id"] == root_id for origin in origin_items.values())
        items = await e2e_client.get(f"/api/v1/agents/sessions/{target_id}/items", headers=e2e_headers)
        assert items.status_code == 200, items.text
        texts = [part["text"] for item in items.json()["data"] if item.get("role") == "user" for part in item["content"]]
        assert texts == ["USER_SCOPE_CHILD FOLLOWUP", "USER_SCOPE_CHILD USER_SCOPE_WRITE" if different_project else "USER_SCOPE_CHILD"]
        assert all("PRIVATE_ROOT_HISTORY" not in text for text in texts)
    finally:
        try:
            # 先停止派发方，避免清理查询之后继续创建独立根。
            if root_id:
                source_turn = turn_id or await conn.fetchval(
                    "SELECT id FROM agent_turns WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 1", root_id
                )
                await archive_public_thread(e2e_client, e2e_headers, root_id, turn_id=source_turn)
            resources = await conn.fetch(
                "SELECT s.thread_id, s.project_id, s.uid, p.workdir_path, "
                "(SELECT id FROM agent_turns WHERE thread_id=s.thread_id ORDER BY created_at DESC LIMIT 1) AS turn_id "
                "FROM sessions s JOIN projects p ON p.id=s.project_id "
                "WHERE s.thread_id=ANY($1::text[]) OR s.created_by_run_id IN (SELECT id FROM agent_runs WHERE thread_id=$2)",
                [item for item in (root_id, target_id) if item],
                root_id,
            )
            thread_ids = {row["thread_id"] for row in resources}
            workdirs = {}
            for row in resources:
                if row["thread_id"] != root_id:
                    await archive_public_thread(e2e_client, e2e_headers, row["thread_id"], turn_id=row["turn_id"])
                if row["workdir_path"]:
                    workdirs.setdefault((row["uid"], row["workdir_path"]), set()).add(row["project_id"])
            await validate_test_runs_terminal(thread_ids)
            await delete_test_session_resources(workdirs, thread_ids, {row["project_id"] for row in resources})
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE thread_id=ANY($1::text[])", sorted(thread_ids)) == 0
            for owner_uid, path in workdirs:
                assert not (user_workspace_dir(owner_uid) / path).exists()
        finally:
            await conn.close()
            await delete_agent(e2e_client, e2e_headers, slug)
