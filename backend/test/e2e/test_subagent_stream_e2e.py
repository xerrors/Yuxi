from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import httpx
import pytest

from e2e_helpers import cancel_run, delete_agent
from test.live_api_cleanup import remove_e2e_thread_storage

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "300"))


def _assert_ok(response: httpx.Response) -> None:
    assert response.status_code < 400, response.text


async def _create_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post("/api/agent", json=payload, headers=headers)
    _assert_ok(response)
    agent = response.json().get("agent")
    assert isinstance(agent, dict), response.text
    return agent


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_id: str, marker: str) -> str:
    response = await client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": f"subagent-stream-e2e-{marker}",
            "metadata": {"_yuxi_e2e": True, "test": "subagent-stream-e2e", "marker": marker},
        },
        headers=headers,
    )
    _assert_ok(response)
    payload = response.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, payload
    return str(thread_id)


async def _create_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_slug: str,
    thread_id: str,
    query: str,
) -> str:
    response = await client.post(
        "/api/agent/runs",
        json={
            "query": query,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "meta": {"request_id": f"subagent-stream-e2e-{uuid.uuid4()}"},
        },
        headers=headers,
    )
    _assert_ok(response)
    run_id = response.json().get("run_id")
    assert run_id, response.text
    return str(run_id)


async def _iter_sse(client: httpx.AsyncClient, headers: dict[str, str], run_id: str):
    async with client.stream("GET", f"/api/agent/runs/{run_id}/events", headers=headers) as response:
        _assert_ok(response)
        event = "message"
        event_id = None
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                if data_lines:
                    data_text = "\n".join(data_lines)
                    yield event, json.loads(data_text), event_id
                event = "message"
                event_id = None
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip() or "message"
            elif line.startswith("id:"):
                event_id = line[len("id:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())


def _collect_message_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = []
    chunk = payload.get("chunk")
    if isinstance(chunk, dict):
        chunks.append(chunk)
    items = payload.get("items")
    if isinstance(items, list):
        chunks.extend(item for item in items if isinstance(item, dict))
    return chunks


async def _consume_run_stream(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
) -> tuple[dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    event_counts: dict[str, int] = {}
    latest_agent_state: dict[str, Any] = {}
    message_chunks: list[dict[str, Any]] = []
    terminal_status = ""

    async def consume() -> None:
        nonlocal latest_agent_state, terminal_status
        async for event, payload, _event_id in _iter_sse(client, headers, run_id):
            event_counts[event] = event_counts.get(event, 0) + 1
            if event == "messages":
                message_chunks.extend(_collect_message_chunks(payload))
            if event == "custom" and payload.get("name") == "yuxi.agent_state":
                agent_state = payload.get("agent_state")
                if isinstance(agent_state, dict):
                    latest_agent_state = agent_state
            assert event != "error", payload
            if event == "end":
                event_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                terminal_status = str(event_payload.get("status") or "")
                return

    await asyncio.wait_for(consume(), timeout=RUN_TIMEOUT_SECONDS)
    assert terminal_status == "completed", {"status": terminal_status, "event_counts": event_counts}
    return event_counts, latest_agent_state, message_chunks


def _find_tool_call_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        tool_calls = value.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if isinstance(tool_call, dict) and tool_call.get("id"):
                    ids.add(str(tool_call["id"]))
        for child in value.values():
            ids.update(_find_tool_call_ids(child))
    elif isinstance(value, list):
        for item in value:
            ids.update(_find_tool_call_ids(item))
    return ids


async def _read_thread_file(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    thread_id: str,
    path: str,
) -> str:
    response = await client.get(
        f"/api/chat/thread/{thread_id}/files/content",
        params={"path": path},
        headers=headers,
    )
    _assert_ok(response)
    content = response.json().get("content")
    assert isinstance(content, list), response.text
    return "\n".join(str(line) for line in content)


async def test_subagent_stream_records_run_and_shares_output_files(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
):
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    _assert_ok(me_response)
    me = me_response.json()
    if me.get("role") not in {"admin", "superadmin"}:
        pytest.skip("Subagent E2E needs an admin user to create temporary agents.")
    uid = str(me.get("uid") or "")
    assert uid, me

    suffix = uuid.uuid4().hex[:8]
    marker = f"YUXI_SUBAGENT_STREAM_E2E_{suffix}"
    sub_slug = f"e2e-subagent-{suffix}"
    main_slug = f"e2e-main-{suffix}"
    output_path = "/home/gem/user-data/outputs/subagents.txt"
    expected_content = "由这个子智能体创建"
    created_agents: list[str] = []
    run_id: str | None = None
    thread_id: str | None = None
    child_thread_id: str | None = None
    run_completed = False

    default_response = await e2e_client.get("/api/agent/default", headers=e2e_headers)
    _assert_ok(default_response)
    default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}
    base_context: dict[str, Any] = {"tools": [], "knowledges": [], "mcps": [], "skills": []}
    if default_context.get("model"):
        base_context["model"] = default_context["model"]

    share_config = {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        "manage_scope": None,
    }

    try:
        sub_agent = await _create_agent(
            e2e_client,
            e2e_headers,
            {
                "name": f"E2E 子智能体 {suffix}",
                "slug": sub_slug,
                "backend_id": "SubAgentBackend",
                "description": "真实流式 E2E 子智能体",
                "config_json": {
                    "context": {
                        **base_context,
                        "system_prompt": (
                            "你是专门负责文件写入和文件校验的子智能体。收到任务后必须使用文件系统工具完成任务，"
                            "不要向用户提问。必须严格写入用户指定路径，文件内容必须完全符合用户要求，"
                            "不要自动追加句号、引号、说明或其他字符。完成后只回复写入的路径和文件内容。"
                        ),
                    }
                },
                "share_config": share_config,
                "is_subagent": True,
            },
        )
        created_agents.append(sub_slug)

        await _create_agent(
            e2e_client,
            e2e_headers,
            {
                "name": f"E2E 主智能体 {suffix}",
                "slug": main_slug,
                "backend_id": "ChatbotAgent",
                "description": "真实流式 E2E 主智能体",
                "config_json": {
                    "context": {
                        **base_context,
                        "subagents": [sub_slug],
                        "system_prompt": (
                            "你是主智能体。遇到用户要求创建、修改或验证文件的任务时，"
                            "必须调用 task 工具交给可用子智能体完成，不要自己写文件，"
                            "也不要通过 shell、curl 或 HTTP API 调用子智能体。子智能体完成后，简短汇总结果。"
                        ),
                    }
                },
                "share_config": share_config,
                "is_subagent": False,
            },
        )
        created_agents.append(main_slug)

        default_agents_response = await e2e_client.get("/api/agent", headers=e2e_headers)
        _assert_ok(default_agents_response)
        default_agent_slugs = {str(item.get("slug")) for item in default_agents_response.json().get("agents") or []}
        assert sub_slug not in default_agent_slugs

        management_agents_response = await e2e_client.get("/api/agent?include_subagents=true", headers=e2e_headers)
        _assert_ok(management_agents_response)
        management_agent_slugs = {
            str(item.get("slug")) for item in management_agents_response.json().get("agents") or []
        }
        assert sub_slug in management_agent_slugs

        thread_id = await _create_thread(e2e_client, e2e_headers, main_slug, marker)
        query = (
            f"请调用子智能体 {sub_slug} 在 outputs 目录创建文件 {output_path}。"
            "必须通过 task 工具调用子智能体完成。"
            f"文件内容必须完全等于下面一行：\n{expected_content}\n"
            "不要添加句号、引号、说明或其他任何字符。完成后只需要回复文件路径。"
        )
        run_id = await _create_run(
            e2e_client,
            e2e_headers,
            agent_slug=main_slug,
            thread_id=thread_id,
            query=query,
        )

        event_counts, stream_agent_state, message_chunks = await _consume_run_stream(
            e2e_client,
            e2e_headers,
            run_id,
        )
        assert event_counts.get("messages", 0) > 0

        run_response = await e2e_client.get(f"/api/agent/runs/{run_id}", headers=e2e_headers)
        _assert_ok(run_response)
        assert (run_response.json().get("run") or {}).get("status") == "completed"

        state_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/state", headers=e2e_headers)
        _assert_ok(state_response)
        final_agent_state = state_response.json().get("agent_state") or stream_agent_state
        subagent_runs = final_agent_state.get("subagent_runs") or []
        assert subagent_runs, final_agent_state
        completed_runs = [
            item
            for item in subagent_runs
            if item.get("status") == "completed" and item.get("subagent_slug") == sub_slug
        ]
        assert completed_runs, final_agent_state
        completed_run = max(completed_runs, key=lambda item: str(item.get("created_at") or ""))
        assert completed_run.get("subagent_name") == sub_agent["name"]
        assert completed_run.get("child_thread_id")
        assert completed_run.get("id")

        child_thread_id = str(completed_run["child_thread_id"])
        child_state_response = await e2e_client.get(
            f"/api/chat/thread/{child_thread_id}/state",
            params={"include_messages": "true"},
            headers=e2e_headers,
        )
        _assert_ok(child_state_response)
        child_state_payload = child_state_response.json()
        assert child_state_payload.get("parent_thread_id") == thread_id
        child_subagent_run = child_state_payload.get("subagent_run") or {}
        assert child_subagent_run.get("child_thread_id") == child_thread_id
        assert child_subagent_run.get("run_id")
        child_run_response = await e2e_client.get(
            f"/api/agent/runs/{child_subagent_run['run_id']}",
            headers=e2e_headers,
        )
        _assert_ok(child_run_response)
        child_run = child_run_response.json().get("run") or {}
        assert child_run.get("run_type") == "subagent"
        assert child_run.get("conversation_thread_id") == child_thread_id
        assert child_run.get("created_by_run_id") == run_id
        assert child_run.get("status") == "completed"
        assert child_state_payload.get("messages"), child_state_payload
        child_messages_text = json.dumps(child_state_payload["messages"], ensure_ascii=False, default=str)
        assert "write_file" in child_messages_text and output_path in child_messages_text, {
            "message": "子智能体未执行目标 write_file 调用",
            "subagent_run": completed_run,
            "messages": child_state_payload["messages"],
        }

        leaked_child_chunks = [
            chunk for chunk in message_chunks if child_thread_id in json.dumps(chunk, ensure_ascii=False, default=str)
        ]
        assert leaked_child_chunks == []

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        _assert_ok(history_response)
        history_payload = history_response.json()
        tool_call_ids = _find_tool_call_ids(history_payload)
        assert str(completed_run["id"]) in tool_call_ids
        assert child_thread_id in json.dumps(history_payload, ensure_ascii=False)

        files_response = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/files",
            params={"path": "/home/gem/user-data/outputs", "recursive": "true"},
            headers=e2e_headers,
        )
        _assert_ok(files_response)
        files_payload = files_response.json()
        file_paths = {str(item.get("path") or "") for item in files_payload.get("files") or []}
        assert output_path in file_paths, {
            "output_path": output_path,
            "files": files_payload,
            "subagent_run": completed_run,
        }
        assert (await _read_thread_file(e2e_client, e2e_headers, thread_id, output_path)).strip() == expected_content

        tree_response = await e2e_client.get(
            "/api/viewer/filesystem/tree",
            params={"thread_id": thread_id, "path": "/home/gem/user-data/outputs"},
            headers=e2e_headers,
        )
        _assert_ok(tree_response)
        assert output_path in json.dumps(tree_response.json(), ensure_ascii=False)

        viewer_file_response = await e2e_client.get(
            "/api/viewer/filesystem/file",
            params={"thread_id": thread_id, "path": output_path},
            headers=e2e_headers,
        )
        _assert_ok(viewer_file_response)
        assert expected_content in json.dumps(viewer_file_response.json(), ensure_ascii=False)
        run_completed = True

    finally:
        if not run_completed:
            await cancel_run(e2e_client, e2e_headers, run_id)
        for cleanup_thread_id in (thread_id, child_thread_id):
            if cleanup_thread_id:
                delete_response = await e2e_client.delete(
                    f"/api/chat/thread/{cleanup_thread_id}",
                    headers=e2e_headers,
                )
                assert delete_response.status_code in {200, 404}, delete_response.text
                remove_e2e_thread_storage(cleanup_thread_id)
        for slug in reversed(created_agents):
            await delete_agent(e2e_client, e2e_headers, slug)
