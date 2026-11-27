"""用小配额和真实 HTTP、worker 与文件验证上传、任务暂缓及恢复。"""

import asyncio
import os
import uuid
from contextlib import aclosing

import pytest

from test.e2e.e2e_helpers import (
    archive_public_thread,
    iter_public_thread_events,
    public_message,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.support.permission_convergence import SHARED, create_agent
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.workspace.paths import user_workspace_dir
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH
from yuxi.modules.workspace.services.quota import scan_workspace_usage

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources, pytest.mark.timeout(180)]


async def test_over_quota_allows_active_turn_and_defers_new_turns_in_order(actors, replay):
    """用量超额时阻止上传，新消息继续入队，已开始的 Turn 可完成审批。"""
    expected = os.getenv("E2E_WORKSPACE_QUOTA_BYTES")
    if expected is None:
        pytest.skip("需要显式指定隔离部署的 E2E_WORKSPACE_QUOTA_BYTES")
    client, db = actors["client"], actors["db"]
    admin, user, other = (actors["identities"][key] for key in ("admin0", "user0", "user1"))
    headers, uid = user["headers"], user["uid"]
    usage_url = "/api/workspace/usage"
    usage = await client.get(usage_url, headers=headers)
    assert usage.status_code == 200, usage.text
    limit = usage.json()["limit_bytes"]
    assert limit == int(expected) and 65536 <= limit <= 8 * 1024**2

    suffix = uuid.uuid4().hex[:8]
    provider = f"ci-quota-{suffix}"
    model = f"{provider}:deterministic-chat"
    await register_replay_provider(client, actors["root"], provider_id=provider, base_url=replay["url"])
    agent = await create_agent(actors, admin, visibility="shared", share_config=SHARED, config_json={"context": {"model": model}})
    root = user_workspace_dir(uid)
    folder, proof, filler, note, denied = (f"quota-{suffix}{end}" for end in ("", "-result.txt", "-fill.bin", "-note.txt", "-denied.txt"))
    content = "current turn finished"
    replay["action"].update(name="write_file", arguments={"file_path": f"{WORKSPACE_PATH}/{proof}", "content": content})

    async def upload(who, name, data):
        """由工作区上传入口写入。"""
        return await client.post("/api/workspace/upload", headers=who, data={"parent_path": "/"}, files={"files": (name, data)})

    async def post_event(thread_id, key, event):
        """提交输入或控制事件并固定本例幂等键。"""
        return await client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**headers, "Idempotency-Key": f"{suffix}-{key}"},
            json={"events": [event]},
        )

    async def submit(thread_id, key):
        """提交可辨识的 FIFO 输入，保留原始回执。"""
        response = await post_event(
            thread_id, key, {"type": "agent.session.input.message", "input": [public_message(key)], "yuxi": {"mode": "follow_up"}}
        )
        assert response.status_code == 202, response.text
        return response.json()

    thread_id = idle_id = draft_id = None
    queued = {}
    try:
        initial = await client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": suffix},
            json={
                "agent_id": agent["slug"],
                "agent": {"model": model},
                "input": [public_message("写入结果")],
                "tool_approval_mode": "default",
                "title": f"pytest quota {suffix}",
            },
        )
        assert initial.status_code == 201, initial.text
        thread_id = initial.json()["id"]
        first = await wait_for_consumed_input(client, headers, initial.json()["yuxi"]["receipt"])
        first_turn_id = first["turn_id"]
        session = f"/api/v1/agents/sessions/{thread_id}"
        assert await asyncio.to_thread(replay["entered"].wait, 25)
        queued[thread_id] = [await submit(thread_id, "before-quota")]

        assert (await upload(headers, note, b"original")).status_code == 200
        filled = await upload(headers, filler, b"x" * limit)
        assert filled.status_code == 200, filled.text
        assert (root / filler).stat().st_blocks * 512 >= limit
        await _scan_and_wait_for_usage(client, headers, usage_url, over_quota=True)
        queued[thread_id].append(await submit(thread_id, "after-quota"))
        assert await submit(thread_id, "before-quota") == queued[thread_id][0]

        workdir = root / await db.fetchval(
            "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=$1", thread_id
        )
        for response in (
            await upload(headers, denied, b"denied"),
            await client.post(
                "/api/viewer/filesystem/upload",
                headers=headers,
                data={"thread_id": thread_id, "parent_path": "/"},
                files={"files": (denied, b"denied")},
            ),
            await client.post("/api/v1/agents/files", headers=headers, files={"file": (denied, b"denied", "text/plain")}),
        ):
            assert response.status_code == 409 and response.json()["detail"]["code"] == "workspace_quota_exceeded", response.text
        assert not (root / denied).exists() and not (workdir / denied).exists()
        assert not await db.fetchval("SELECT EXISTS(SELECT 1 FROM agent_attachments WHERE uid=$1 AND filename=$2)", uid, denied)
        other_upload = await upload(other["headers"], filler, b"other")
        assert other_upload.status_code == 200, other_upload.text
        assert (user_workspace_dir(other["uid"]) / filler).read_bytes() == b"other"

        edit = await client.put("/api/workspace/file", headers=headers, json={"path": f"/{note}", "content": "edited"})
        directory = await client.post("/api/workspace/directory", headers=headers, json={"parent_path": "/", "name": folder})
        viewer_directory = await client.post(
            "/api/viewer/filesystem/directory", headers=headers, json={"thread_id": thread_id, "parent_path": "/", "name": folder}
        )
        assert edit.status_code == 200 and (root / note).read_text() == "edited", edit.text
        assert directory.status_code == 200 and (root / folder).is_dir(), directory.text
        assert viewer_directory.status_code == 200 and (workdir / folder).is_dir(), viewer_directory.text

        skill = f"---\nslug: {folder}\nname: Quota fixture\ndescription: quota test\n---\n# Test\n"
        draft = await client.post("/api/skills/import/prepare", headers=headers, files={"file": ("SKILL.md", skill.encode())})
        assert draft.status_code == 200, draft.text
        draft_id = draft.json()["data"]["draft_id"]
        installed = await client.post(f"/api/skills/personal/install-drafts/{draft_id}/confirm", headers=headers, json={"slugs": [folder]})
        assert installed.status_code == 200 and installed.json()["summary"]["success"] == 1, installed.text
        saved = await client.get(f"/api/skills/personal/{folder}/file", headers=headers, params={"path": "SKILL.md"})
        assert saved.status_code == 200 and saved.json()["data"]["content"] == skill, saved.text

        replay["release"].set()
        async with asyncio.timeout(25):
            while True:
                snapshot = await client.get(f"{session}/turns/{first_turn_id}", headers=headers)
                assert snapshot.status_code == 200, snapshot.text
                pending = snapshot.json()
                if pending["status"] == "requires_action":
                    break
                assert pending["status"] not in {"failed", "cancelled", "completed"}, pending
                await asyncio.sleep(0.2)
        waitpoint = pending["yuxi"]["waitpoint"]
        assert waitpoint["run_id"] == first["run_id"] and not (root / proof).exists()
        replay["action"]["plain"] = True
        resumed = await post_event(
            thread_id,
            "resume",
            {
                "type": "yuxi.session.input.resume",
                "turn_id": first_turn_id,
                "waitpoint_id": waitpoint["id"],
                "response": {"type": "approval", "decisions": [{"call_id": waitpoint["calls"][0]["call_id"], "decision": "approve"}]},
            },
        )
        assert resumed.status_code == 202, resumed.text
        resumed_run = resumed.json()["run_id"]
        assert resumed_run != first["run_id"] and resumed.json()["turn_id"] == first_turn_id
        async with asyncio.timeout(25), aclosing(iter_public_thread_events(client, headers, thread_id)) as events:
            async for event in events:
                if event.get("turn_id") == first_turn_id and event["type"] in {
                    "agent.session.turn.completed",
                    "agent.session.turn.failed",
                    "agent.session.turn.cancelled",
                }:
                    assert event["type"] == "agent.session.turn.completed", event
                    break
            else:
                pytest.fail("审批恢复的 Turn 未发布终态事件")
        finished = await wait_for_terminal_turn(client, headers, thread_id, first_turn_id)
        assert finished["status"] == "completed" and finished["yuxi"]["result_run_id"] == resumed_run, finished
        assert (root / proof).read_text() == content

        idle = await client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": f"{suffix}-idle"},
            json={"agent_id": agent["slug"], "agent": {"model": model}, "title": f"pytest quota idle {suffix}"},
        )
        assert idle.status_code == 201, idle.text
        idle_id = idle.json()["id"]
        queued[idle_id] = [await submit(idle_id, "idle-at-quota")]
        assert (await client.get(usage_url, headers=headers)).json()["over_quota"]
        pending_ids = [receipt["input_id"] for receipts in queued.values() for receipt in receipts]
        queue = await client.get(f"{session}/queue", headers=headers)
        assert queue.status_code == 200, queue.text
        assert [input_item["input_id"] for input_item in queue.json()["inputs"]] == pending_ids[:2]
        rows = await db.fetch("SELECT status, turn_id, consumed_run_id FROM agent_inputs WHERE id=ANY($1)", pending_ids)
        assert [tuple(row) for row in rows] == [("pending", None, None)] * len(pending_ids)

        deleted = await client.delete("/api/workspace/file", headers=headers, params={"path": f"/{filler}"})
        assert deleted.status_code == 200 and not (root / filler).exists(), deleted.text
        await _scan_and_wait_for_usage(client, headers, usage_url, over_quota=False)
        for queued_thread, receipts in queued.items():
            for receipt in receipts:
                item = await wait_for_consumed_input(client, headers, receipt)
                turn = await wait_for_terminal_turn(client, headers, queued_thread, item["turn_id"])
                assert turn["status"] == "completed", turn

        fifo_ids = [receipt["input_id"] for receipt in queued[thread_id]]
        rows = await db.fetch(
            "SELECT i.id FROM agent_inputs i JOIN agent_runs r ON r.id=i.consumed_run_id WHERE i.id=ANY($1) ORDER BY r.execution_seq",
            fifo_ids,
        )
        assert [row["id"] for row in rows] == fifo_ids
        restored = await upload(headers, filler, b"restored")
        assert restored.status_code == 200 and (root / filler).read_bytes() == b"restored", restored.text
    finally:
        replay["action"]["plain"] = True
        replay["release"].set()
        for queued_thread, receipts in queued.items():
            for receipt in receipts:
                input_id = receipt["input_id"]
                if await db.fetchval("SELECT status FROM agent_inputs WHERE id=$1", input_id) == "pending":
                    await post_event(
                        queued_thread, f"cleanup-{input_id}", {"type": "yuxi.session.input.cancel_input", "input_id": input_id}
                    )
        if thread_id:
            await client.delete("/api/viewer/filesystem/file", headers=headers, params={"thread_id": thread_id, "path": f"/{folder}"})
        for who, name in ((headers, filler), (headers, proof), (headers, note), (headers, folder), (other["headers"], filler)):
            await client.delete("/api/workspace/file", headers=who, params={"path": f"/{name}"})
        if draft_id:
            await client.delete(f"/api/skills/install-drafts/{draft_id}", headers=headers)
            await client.delete(f"/api/skills/personal/{folder}", headers=headers)
        for cleanup_thread in (idle_id, thread_id):
            if cleanup_thread:
                active_turn = await db.fetchval(
                    "SELECT id FROM agent_turns WHERE thread_id=$1 AND status IN ('running', 'waiting', 'cancelling')", cleanup_thread
                )
                await archive_public_thread(client, headers, cleanup_thread, turn_id=active_turn)
        await client.delete(f"/api/system/model-providers/{provider}", headers=actors["root"])


async def _scan_and_wait_for_usage(client, headers, usage_url, *, over_quota):
    """实际计量隔离卷并回读持久快照，避免等待完整巡检周期。"""
    load_models()
    pg_manager.initialize()
    try:
        async with asyncio.timeout(20):
            while True:
                await scan_workspace_usage()
                response = await client.get(usage_url, headers=headers)
                assert response.status_code == 200, response.text
                usage = response.json()
                if usage["measured_at"] and usage["over_quota"] == over_quota:
                    return
                await asyncio.sleep(0.1)
    finally:
        await pg_manager.close()
