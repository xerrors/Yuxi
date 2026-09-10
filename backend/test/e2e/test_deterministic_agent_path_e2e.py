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
from yuxi.workspace.paths import user_workspace_dir, workspace_uid_dirname

from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

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


async def test_replay_rejects_requests_outside_deterministic_contract() -> None:
    valid_body = {
        "model": "deterministic-chat",
        "stream": True,
        "messages": [
            {"role": "system", "content": EXPECTED_PRELOADED_SKILL_MARKER},
            {"role": "user", "content": EXPECTED_OUTPUT},
        ],
        "tools": [{"type": "function", "function": {"name": EXPECTED_PRELOADED_TOOL}}],
    }
    cases = [
        ({}, valid_body, "invalid_authorization"),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {**valid_body, "model": "other-model"},
            "invalid_model",
        ),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {**valid_body, "stream": False},
            "stream_required",
        ),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {**valid_body, "messages": [{"role": "user", "content": "wrong"}]},
            "expected_input_missing",
        ),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {
                **valid_body,
                "messages": [{"role": "user", "content": EXPECTED_OUTPUT}],
            },
            "preloaded_skill_missing",
        ),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {**valid_body, "tools": []},
            "preloaded_tool_missing",
        ),
        (
            {"Authorization": "Bearer ci-replay-key"},
            {
                **valid_body,
                "messages": [
                    *valid_body["messages"],
                    {
                        "role": "tool",
                        "tool_call_id": EXPECTED_TOOL_CALL_ID,
                        "content": "unexpected result",
                    },
                ],
            },
            "tool_execution_result_missing",
        ),
    ]

    async with httpx.AsyncClient(base_url="http://localhost:8765", timeout=5) as client:
        for headers, body, expected_error in cases:
            response = await client.post("/v1/chat/completions", headers=headers, json=body)
            assert response.status_code == 422, response.text
            assert response.json() == {"error": expected_error}


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
        for _ in range(100):
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
                    "model": MODEL_SPEC,
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
                       conversation.thread_id
                FROM agent_runs run JOIN conversations conversation ON conversation.id = run.conversation_id
                WHERE run.created_by_run_id = $1 AND run.run_type = 'subagent'
                """,
                run_id,
            )
            assert len(children) == 1, children
            child = children[0]
            child_thread_id = str(child["thread_id"])
            assert child["status"] == "completed", dict(child)
            assert child["runtime_scope_id"] == thread_id
            payload = json.loads(child["input_payload"])
            assert payload["tool_approval_mode"] == mode
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


async def _assert_persisted_causality(run_id: str, request_id: str) -> None:
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
        assert manifest["manifest_version"] == 1
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

        resume_request_id = f"deterministic-large-resume-{uuid.uuid4()}"
        resume_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": resume_request_id},
                "resume": {"decisions": [{"type": "approve"}]},
                "created_by_run_id": parent_run_id,
            },
            headers=e2e_headers,
        )
        assert resume_response.status_code == 200, resume_response.text
        resume_run_id = str(resume_response.json()["run_id"])
        active_run_id = resume_run_id
        await consume_events(e2e_client, e2e_headers, resume_run_id)
        resume_run = await wait_for_run(e2e_client, e2e_headers, resume_run_id)
        assert resume_run["status"] == "completed", resume_run
        assert resume_run["output_message_id"] is not None, resume_run

        result = await e2e_client.get(f"/api/agent/runs/{resume_run_id}/result", headers=e2e_headers)
        assert result.status_code == 200, result.text
        assert result.json()["output"] == EXPECTED_OUTPUT

        conn = await asyncpg.connect(postgres_dsn())
        try:
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
        await asyncio.sleep(int(os.getenv("SANDBOX_KEEPALIVE_INTERVAL_SECONDS", "30")) + 1)
        await _run_deterministic(
            e2e_client,
            e2e_headers,
            agent_slug=agent_slug,
            thread_id=thread_id,
        )
        sandbox = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        recreated_read = sandbox.read(attachment_path)
        assert recreated_read.error is None, recreated_read
        assert recreated_read.file_data == {"content": overwritten_content, "encoding": "utf-8"}

        delete_response = await e2e_client.delete(
            f"/api/chat/thread/{thread_id}/attachments/{attachment['file_id']}",
            headers=e2e_headers,
        )
        assert delete_response.status_code == 200, delete_response.text
        await _run_deterministic(
            e2e_client,
            e2e_headers,
            agent_slug=agent_slug,
            thread_id=thread_id,
        )
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
