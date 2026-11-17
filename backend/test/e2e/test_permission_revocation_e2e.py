"""真实模型、worker 与文件边界证明新工具执行会重新授权。"""

import asyncio
import json
import os
import uuid
from datetime import datetime

import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from test.e2e.e2e_helpers import public_message as _message
from test.e2e.e2e_helpers import register_replay_provider as _provider
from test.e2e.e2e_helpers import wait_for_consumed_input
from test.support.permission_convergence import SHARED, create_agent
from yuxi.modules.workspace.paths import user_workspace_dir
from yuxi.modules.workspace.runtime_paths import SHARED_SKILLS_PATH, WORKSPACE_PATH

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(120)]


@pytest.mark.self_managed_resources
@pytest.mark.parametrize("revocation", ["agent", "account", "skill", "mcp"])
async def test_tool_boundary_rechecks_current_caller_and_retains_history(actors, replay, remote_tool, revocation):
    """撤销 Agent/账号终结执行，撤销 Skill 禁止后续读取，撤销 MCP 拒绝执行。"""
    client, db = actors["client"], actors["db"]
    admin, user = (actors["identities"][key] for key in ("admin0", "user0"))
    suffix = uuid.uuid4().hex[:8]
    provider_id = f"ci-permission-{suffix}"
    model = f"{provider_id}:deterministic-chat"
    await _provider(client, actors["root"], base_url=replay["url"], provider_id=provider_id)
    skill = f"permission-skill-{suffix}"
    skill_installed = False
    filename = f"permission-proof-{suffix}.txt"
    file = user_workspace_dir(user["uid"]) / filename
    mcp_slug = None
    if revocation == "skill":
        package = f"---\nname: {skill}\ndescription: permission test\n---\n# Permission test\n"
        prepared = await client.post(
            "/api/skills/import/prepare",
            headers=admin["headers"],
            files={"file": ("SKILL.md", package.encode(), "text/markdown")},
        )
        assert prepared.status_code == 200, prepared.text
        draft = prepared.json()["data"]["draft_id"]
        confirmed = await client.post(
            f"/api/skills/install-drafts/{draft}/confirm",
            headers=admin["headers"],
            json={"slugs": [skill], "share_config": SHARED},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["data"][0]["success"], confirmed.text
        skill_installed = True
        replay["action"].update(name="read_file", arguments={"file_path": f"{SHARED_SKILLS_PATH}/{skill}/SKILL.md"})
    elif revocation == "mcp":
        mcp_slug = f"permission-mcp-{suffix}"
        created = await client.post(
            "/api/system/mcp-servers",
            headers=actors["root"],
            json={
                "slug": mcp_slug,
                "name": "Permission MCP",
                "transport": "streamable_http",
                "url": remote_tool["url"],
            },
        )
        assert created.status_code == 200, created.text
        replay["action"].update(name="effect_probe", arguments={})
    else:
        replay["action"].update(
            name="write_file",
            arguments={"file_path": f"{WORKSPACE_PATH}/{filename}", "content": "FORBIDDEN_SIDE_EFFECT"},
        )
    agent = await create_agent(
        actors,
        admin,
        visibility="shared",
        share_config=SHARED,
        config_json={
            "context": {
                "model": model,
                "tools": [],
                "skills": [skill] if skill_installed else [],
                "preload_skills": [skill] if skill_installed else [],
                "mcps": [mcp_slug] if mcp_slug else [],
                "knowledges": [],
            }
        },
    )
    try:
        created = await client.post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": suffix},
            json={
                "agent_id": agent["slug"],
                "agent": {"model": model},
                "input": [_message("Check permission")],
                "title": f"Permission {suffix}",
            },
        )
        assert created.status_code == 201, created.text
        accepted = await wait_for_consumed_input(client, user["headers"], created.json()["yuxi"]["receipt"])
        for _ in range(200):
            if replay["entered"].is_set():
                break
            run = await db.fetchrow("SELECT status,error_message FROM agent_runs WHERE id=$1", accepted["run_id"])
            assert run["status"] != "failed", dict(run)
            await asyncio.sleep(0.1)
        else:
            pytest.fail("真实模型未到达撤权前的工具准备阶段")
        workdir = await db.fetchval(
            "SELECT p.workdir_path FROM projects p JOIN sessions t ON t.project_id=p.id WHERE t.thread_id=$1",
            accepted["session_id"],
        )
        file = user_workspace_dir(user["uid"]) / workdir / filename
        if revocation in {"agent", "account"}:
            replay["action"]["arguments"]["file_path"] = f"{WORKSPACE_PATH}/{workdir}/{filename}"
        if revocation == "agent":
            response = await client.put(
                f"/api/agent/{agent['slug']}",
                headers=admin["headers"],
                json={"share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
            )
        elif revocation == "account":
            response = await client.delete(f"/api/auth/users/{user['id']}", headers=actors["root"])
        elif revocation == "mcp":
            response = await client.put(f"/api/system/mcp-servers/{mcp_slug}/tools/effect_probe/toggle", headers=actors["root"])
        else:
            response = await client.put(
                f"/api/system/skills/{skill}/share-config",
                headers=admin["headers"],
                json={"share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
            )
        assert response.status_code == 200, response.text
        replay["release"].set()
        for _ in range(200):
            run = await db.fetchrow("SELECT status,error_message FROM agent_runs WHERE id=$1", accepted["run_id"])
            if run["status"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("撤权后 Run 没有形成终态")
        assert run["status"] == ("completed" if revocation in {"skill", "mcp"} else "failed"), dict(run)
        assert not file.exists(), "失权后执行了文件副作用"
        assert not remote_tool["effect"].exists(), "停用后执行了真实 MCP 工具"
        assert await db.fetchval("SELECT count(*) FROM sessions WHERE thread_id=$1", accepted["session_id"]) == 1
        if revocation in {"skill", "mcp"}:
            async with AsyncPostgresSaver.from_conn_string(os.environ["POSTGRES_URL"].replace("+asyncpg", "")) as saver:
                checkpoint = await saver.aget_tuple({"configurable": {"thread_id": accepted["session_id"]}})
            outputs = [
                message
                for message in checkpoint.checkpoint["channel_values"]["messages"]
                if isinstance(message, ToolMessage) and message.tool_call_id == "call-permission"
            ]
            assert len(outputs) == 1
            if revocation == "skill":
                # Skill 已不控制工具授权；此处独立证明真实文件边界拒绝后续读取。
                assert "# Permission test" not in str(outputs[0].content)
                assert "Error" in str(outputs[0].content) or outputs[0].status == "error"
            else:
                assert "能力受限" in outputs[0].content and outputs[0].status == "error"
                assert any(
                    "能力受限" in str(message.get("content"))
                    for request in replay["requests"]
                    for message in request["messages"]
                    if message["role"] == "tool"
                )
        else:
            assert "权限已撤销" in run["error_message"] or "账号已失效" in run["error_message"]
    finally:
        replay["release"].set()
        if mcp_slug:
            response = await client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=actors["root"])
            assert response.status_code in {200, 404}, response.text
        if skill_installed:
            deleted = await client.delete(f"/api/system/skills/{skill}", headers=actors["root"])
            assert deleted.status_code in {200, 404}, deleted.text
        response = await client.delete(f"/api/system/model-providers/{provider_id}", headers=actors["root"])
        assert response.status_code in {200, 404}, response.text


async def prepared_agent(actors, replay, *, tools=None):
    """注册独占模型并创建可供普通用户调用的共享定义。"""
    provider = f"ci-permission-{uuid.uuid4().hex[:8]}"
    model = f"{provider}:deterministic-chat"
    await _provider(actors["client"], actors["root"], base_url=replay["url"], provider_id=provider)
    admin = actors["identities"]["admin0"]
    agent = await create_agent(
        actors,
        admin,
        visibility="shared",
        share_config=SHARED,
        config_json={
            "context": {
                "model": model,
                "tools": tools or [],
                "skills": [],
                "preload_skills": [],
                "mcps": [],
                "knowledges": [],
            }
        },
    )
    return agent, provider, model


async def revoke_agent(actors, agent):
    """通过资源 Owner 的真实接口撤销使用范围。"""
    response = await actors["client"].put(
        f"/api/agent/{agent['slug']}",
        headers=actors["identities"]["admin0"]["headers"],
        json={"share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
    )
    assert response.status_code == 200, response.text


async def test_queued_input_after_revocation_fails_without_another_model_call(actors, replay):
    """已经接收的 FIFO 输入启动时重查权限，两轮都终结且历史保留。"""
    client, db = actors["client"], actors["db"]
    user = actors["identities"]["user0"]
    replay["action"].update(name="write_file", arguments={"file_path": f"{WORKSPACE_PATH}/forbidden.txt", "content": "forbidden"})
    agent, provider, model = await prepared_agent(actors, replay)
    try:
        initial = await client.post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={"agent_id": agent["slug"], "agent": {"model": model}, "input": [_message("First")]},
        )
        assert initial.status_code == 201, initial.text
        accepted = await wait_for_consumed_input(client, user["headers"], initial.json()["yuxi"]["receipt"])
        for _ in range(200):
            if replay["entered"].is_set():
                break
            await asyncio.sleep(0.1)
        assert replay["entered"].is_set()
        queued = await client.post(
            f"/api/v1/agents/sessions/{accepted['session_id']}/events",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "yuxi": {"mode": "follow_up"},
                        "input": [_message("Queued")],
                    }
                ]
            },
        )
        assert queued.status_code == 202, queued.text
        await revoke_agent(actors, agent)
        replay["release"].set()
        for _ in range(200):
            if await db.fetchval("SELECT status FROM agent_runs WHERE id=$1", accepted["run_id"]) == "failed":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("失权的执行未失败")
        assert await db.fetchval("SELECT queue_paused FROM sessions WHERE thread_id=$1", accepted["session_id"])
        assert (
            await db.fetchval(
                "SELECT count(*) FROM agent_inputs WHERE thread_id=$1 AND status='pending'",
                accepted["session_id"],
            )
            == 1
        )
        continued = await client.post(
            f"/api/v1/agents/sessions/{accepted['session_id']}/events",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={"events": [{"type": "yuxi.session.input.continue"}]},
        )
        assert continued.status_code == 202, continued.text
        for _ in range(200):
            rows = await db.fetch("SELECT status,error_message FROM agent_runs WHERE thread_id=$1", accepted["session_id"])
            if len(rows) == 2 and all(row["status"] == "failed" for row in rows):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"失权的排队输入未形成明确失败结果：{[dict(row) for row in rows]}")
        assert len(replay["requests"]) == 1, "排队输入失权后仍调用了模型"
        history = await client.get(f"/api/v1/agents/sessions/{accepted['session_id']}/items?order=asc&limit=100", headers=user["headers"])
        assert history.status_code == 200 and "Queued" in history.text, history.text
    finally:
        replay["release"].set()
        await client.delete(f"/api/system/model-providers/{provider}", headers=actors["root"])


async def test_due_schedule_rechecks_permission_before_creating_thread(actors, replay):
    """真实 worker 到期调度失权任务，保存失败意图且不创建会话或 Run。"""
    client, db = actors["client"], actors["db"]
    user = actors["identities"]["user0"]
    agent, provider, model = await prepared_agent(actors, replay)
    job = None
    try:
        initial = await client.post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={"agent_id": agent["slug"], "agent": {"model": model}},
        )
        assert initial.status_code == 201, initial.text
        thread_id = initial.json()["id"]
        project_id = await db.fetchval("SELECT project_id FROM sessions WHERE thread_id=$1", thread_id)
        response = await client.post(
            "/api/scheduled-tasks",
            headers=user["headers"],
            json={
                "request_id": uuid.uuid4().hex,
                "name": "Permission schedule",
                "project_id": project_id,
                "agent_slug": agent["slug"],
                "prompt": "Never run",
                "cron_expression": "0 0 1 1 *",
                "timezone": "UTC",
                "model_spec": model,
            },
        )
        assert response.status_code == 200, response.text
        job = response.json()["id"]
        await revoke_agent(actors, agent)
        await db.execute("UPDATE scheduled_agent_jobs SET next_run_at=$1 WHERE id=$2", datetime(2020, 1, 1), job)
        for _ in range(300):
            record = await db.fetchrow("SELECT status,error_message,thread_id FROM scheduled_agent_runs WHERE job_id=$1", job)
            if record and record["status"] == "failed":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("真实 worker 未处理失权的到期任务")
        assert "不可访问" in record["error_message"]
        assert await db.fetchval("SELECT count(*) FROM sessions WHERE thread_id=$1", record["thread_id"]) == 0
        assert not replay["requests"]
    finally:
        if job:
            await client.delete(f"/api/scheduled-tasks/{job}", headers=user["headers"])
        await client.delete(f"/api/system/model-providers/{provider}", headers=actors["root"])


@pytest.mark.parametrize("revocation", ["agent", "account"])
async def test_waitpoint_resume_after_revocation_cannot_create_new_run(actors, replay, revocation):
    """真实提问等待点失权后拒绝恢复，原会话和暂停结果仍可读取。"""
    client, db = actors["client"], actors["db"]
    user = actors["identities"]["user0"]
    replay["action"].update(name="ask_user_question", arguments={"questions": [{"question_id": "permission-q", "question": "Continue?"}]})
    replay["release"].set()
    agent, provider, model = await prepared_agent(actors, replay, tools=["ask_user_question"])
    accepted = None
    try:
        response = await client.post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={"agent_id": agent["slug"], "agent": {"model": model}, "input": [_message("Wait")]},
        )
        assert response.status_code == 201, response.text
        accepted = await wait_for_consumed_input(client, user["headers"], response.json()["yuxi"]["receipt"])
        for _ in range(200):
            response = await client.get(
                f"/api/v1/agents/sessions/{accepted['session_id']}/turns/{accepted['turn_id']}", headers=user["headers"]
            )
            assert response.status_code == 200, response.text
            snapshot = response.json()
            if snapshot["status"] == "requires_action":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("真实提问没有形成等待点")
        if revocation == "agent":
            waiting_request = None
            try:
                async with db.transaction():
                    await db.fetchval("SELECT id FROM agents WHERE slug=$1 FOR UPDATE", agent["slug"])
                    waiting_request = asyncio.create_task(
                        client.post(
                            f"/api/v1/agents/sessions/{accepted['session_id']}/events",
                            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
                            json={
                                "events": [
                                    {
                                        "type": "yuxi.session.input.resume",
                                        "turn_id": accepted["turn_id"],
                                        "waitpoint_id": str(uuid.uuid4()),
                                        "response": {
                                            "type": "answer",
                                            "answers": [{"question_id": "permission-q", "answer": "Yes"}],
                                        },
                                    }
                                ]
                            },
                        )
                    )
                    for _ in range(100):
                        await db.execute("SELECT pg_stat_clear_snapshot()")
                        if await db.fetchval(
                            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                            "AND query LIKE '%FROM agents%' AND query LIKE '%FOR KEY SHARE%' "
                            "AND $1 = ANY(pg_blocking_pids(pid))",
                            db.get_server_pid(),
                        ):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        pytest.fail("恢复没有在 Agent 锁上等待")
                    # 删除先锁 Agent 再锁 Thread；恢复等待 Agent 时不得先占 Thread。
                    await db.fetchval("SELECT id FROM sessions WHERE thread_id=$1 FOR UPDATE NOWAIT", accepted["session_id"])
            finally:
                if waiting_request is not None:
                    invalid = await waiting_request
                    assert invalid.status_code == 409, invalid.text
            await revoke_agent(actors, agent)
        else:
            deleted = await client.delete(f"/api/auth/users/{user['id']}", headers=actors["root"])
            assert deleted.status_code == 200, deleted.text
        response = await client.post(
            f"/api/v1/agents/sessions/{accepted['session_id']}/events",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={
                "events": [
                    {
                        "type": "yuxi.session.input.resume",
                        "turn_id": accepted["turn_id"],
                        "waitpoint_id": snapshot["yuxi"]["waitpoint"]["id"],
                        "response": {"type": "answer", "answers": [{"question_id": "permission-q", "answer": "Yes"}]},
                    }
                ]
            },
        )
        assert response.status_code in {401, 403, 404}, response.text
        assert await db.fetchval("SELECT count(*) FROM agent_runs WHERE thread_id=$1", accepted["session_id"]) == 1
        assert await db.fetchval("SELECT status FROM agent_runs WHERE id=$1", accepted["run_id"]) == "interrupted"
        if revocation == "agent":
            assert (
                await client.get(f"/api/v1/agents/sessions/{accepted['session_id']}/items?order=asc&limit=100", headers=user["headers"])
            ).status_code == 200
        await client.delete(f"/api/system/model-providers/{provider}", headers=actors["root"])
        if revocation == "account":
            # 恢复进程失联留下的取消意图：worker 对失效账号同样必须收敛。
            await db.execute("UPDATE sessions SET queue_paused=true WHERE thread_id=$1", accepted["session_id"])
            await db.execute("UPDATE agent_turns SET status='cancelling' WHERE id=$1", accepted["turn_id"])
            for _ in range(400):
                if await db.fetchval("SELECT status FROM agent_turns WHERE id=$1", accepted["turn_id"]) == "cancelled":
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("失效账号的等待取消没有收敛")
    finally:
        if accepted and revocation == "agent":
            from test.e2e.e2e_helpers import archive_public_thread

            await archive_public_thread(client, user["headers"], accepted["session_id"], turn_id=accepted["turn_id"])
        await client.delete(f"/api/system/model-providers/{provider}", headers=actors["root"])


@pytest.mark.parametrize("failure", ["network", "overflow"])
async def test_failed_model_response_cannot_start_retry_or_summary_after_revocation(actors, replay, failure):
    """真实模型失败期间撤权后，不发送重试或摘要请求，Run/Turn 持久失败。"""
    client, db = actors["client"], actors["db"]
    admin, user = (actors["identities"][key] for key in ("admin0", "user0"))
    suffix = uuid.uuid4().hex[:8]
    provider_id = f"ci-retry-revocation-{suffix}"
    model = f"{provider_id}:deterministic-chat"
    await _provider(client, actors["root"], base_url=replay["url"], provider_id=provider_id)
    agent = await create_agent(
        actors,
        admin,
        visibility="shared",
        share_config=SHARED,
        config_json={
            "context": {
                "model": model,
                "tools": [],
                "mcps": [],
                "skills": [],
                "knowledges": [],
                "summary_keep_messages": 1,
            }
        },
    )
    try:
        replay["action"]["plain"] = True
        if failure == "network":
            replay["action"]["failure"] = failure
        response = await client.post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": suffix},
            json={
                "agent_id": agent["slug"],
                "agent": {"model": model},
                "input": [_message("Prime history" if failure == "overflow" else "Check retry")],
                "title": f"Permission retry {suffix}",
            },
        )
        assert response.status_code == 201, response.text
        accepted = await wait_for_consumed_input(client, user["headers"], response.json()["yuxi"]["receipt"])
        if failure == "overflow":
            for _ in range(200):
                status = await db.fetchval("SELECT status FROM agent_runs WHERE id=$1", accepted["run_id"])
                if status == "completed":
                    break
                assert status != "failed"
                await asyncio.sleep(0.1)
            else:
                pytest.fail("历史初始化没有完成")
            replay["requests"].clear()
            replay["action"]["failure"] = failure
            response = await client.post(
                f"/api/v1/agents/sessions/{accepted['session_id']}/events",
                headers={**user["headers"], "Idempotency-Key": suffix + "-overflow"},
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [_message("Trigger overflow")],
                            "yuxi": {"mode": "follow_up", "model": model},
                        }
                    ]
                },
            )
            assert response.status_code == 202, response.text
            accepted = await wait_for_consumed_input(client, user["headers"], response.json())
        for _ in range(200):
            if replay["entered"].is_set():
                break
            status = await db.fetchval("SELECT status FROM agent_runs WHERE id=$1", accepted["run_id"])
            assert status != "failed"
            await asyncio.sleep(0.1)
        else:
            pytest.fail("没有到达受控的模型失败响应")
        response = await client.put(
            f"/api/agent/{agent['slug']}",
            headers=admin["headers"],
            json={"share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
        )
        assert response.status_code == 200, response.text
        persisted = json.loads(await db.fetchval("SELECT share_config FROM agents WHERE slug=$1", agent["slug"]))
        assert persisted["read_scope"] is None
        replay["release"].set()
        for _ in range(200):
            run = await db.fetchrow("SELECT status,error_message,turn_id FROM agent_runs WHERE id=$1", accepted["run_id"])
            if run["status"] in {"failed", "completed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("模型失败和撤权后 Run 没有收敛")
        assert run["status"] == "failed", dict(run)
        assert "权限已撤销" in run["error_message"], dict(run)
        assert await db.fetchval("SELECT status FROM agent_turns WHERE id=$1", run["turn_id"]) == "failed"
        assert len(replay["requests"]) == 1, "撤权后仍发送了重试或独立摘要请求"
    finally:
        replay["release"].set()
        response = await client.delete(f"/api/system/model-providers/{provider_id}", headers=actors["root"])
        assert response.status_code in {200, 404}, response.text


async def test_runless_forced_summary_uses_current_thread_agent_permission(actors, replay):
    """主动摘要没有 Run 时仍按真实 Thread 撤权，并且不发送模型请求。"""
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI

    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.infrastructure.postgres.models import load_models
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.agents.runtime.middlewares.authorization import AgentExecutionRevoked
    from yuxi.modules.agents.runtime.middlewares.summary import create_summary_middleware_from_context

    user = actors["identities"]["user0"]
    agent, provider, model = await prepared_agent(actors, replay)
    try:
        response = await actors["client"].post(
            "/api/v1/agents/sessions",
            headers={**user["headers"], "Idempotency-Key": uuid.uuid4().hex},
            json={"agent_id": agent["slug"], "agent": {"model": model}},
        )
        assert response.status_code == 201, response.text
        thread_id = response.json()["id"]
        assert await actors["db"].fetchval("SELECT uid FROM sessions WHERE thread_id=$1", thread_id) == user["uid"]
        assert await actors["db"].fetchval("SELECT count(*) FROM agent_runs WHERE thread_id=$1", thread_id) == 0
        context = ChatBotContext(uid=user["uid"], thread_id=thread_id, model=model)
        compressor = create_summary_middleware_from_context(
            context, backend=None, model=ChatOpenAI(model="deterministic-chat", api_key="test", base_url=replay["url"])
        )
        await revoke_agent(actors, agent)
        replay["action"]["plain"] = True
        replay["release"].set()
        load_models()
        pg_manager.initialize()
        try:
            with pytest.raises(AgentExecutionRevoked, match="权限已撤销"):
                await compressor._acreate_summary([HumanMessage(content="Private history")])
            assert replay["requests"] == []
        finally:
            await pg_manager.close()
    finally:
        await actors["client"].delete(f"/api/system/model-providers/{provider}", headers=actors["root"])
