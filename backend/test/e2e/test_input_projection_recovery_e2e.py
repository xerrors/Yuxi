"""真实进程退出后，由长期 worker 恢复已消费输入的投递与租约。"""

import asyncio
import base64
import multiprocessing
import os
import uuid
from io import BytesIO

import asyncpg
import pytest
from PIL import Image

from yuxi.api.routers.public_v1.agents.schemas import InputMessage, input_messages_to_domain
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.inputs import accept_message
from test.e2e.e2e_helpers import archive_public_thread, delete_agent, postgres_dsn
from test.e2e.e2e_helpers import (
    DETERMINISTIC_OUTPUT as OUTPUT,
    create_lifecycle_agent as _agent,
    output_text,
    public_message as _message,
    register_replay_provider as _provider,
    wait_for_terminal_turn,
)
from test.live_api_cleanup import make_test_session_title

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(180)]


def _consume_and_exit(thread_id, claim_lease):
    """在独立进程提交真实消费事务后退出，故意不投递或释放执行租约。"""
    from yuxi.infrastructure.postgres.models import load_models
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.agents.repositories.runs import AgentRunRepository
    from yuxi.modules.agents.repositories.sessions import SessionRepository
    from yuxi.modules.agents.services.scheduler import claim_next_input

    async def consume():
        """使进程丢失发生在事务提交之后，而非模拟数据库回滚。"""
        load_models()
        pg_manager.initialize()
        async with pg_manager.get_async_session_context() as db:
            session = await SessionRepository(db).lock_session_by_thread_id(thread_id)
            session.queue_paused = False
            claimed_run_id = await claim_next_input(db=db, agent_session=session)
            assert claimed_run_id is not None
            if claim_lease:
                _, acquired = await AgentRunRepository(db).mark_running(claimed_run_id, worker_id="e2e-lost-process", lease_seconds=1)
                assert acquired

    asyncio.run(consume())
    os._exit(17)


@pytest.mark.parametrize(
    "claim_lease,batch,delete_files",
    [(False, False, False), (True, False, False), (False, True, False), (False, True, True)],
)
async def test_process_loss_preserves_consumed_projection(e2e_client, e2e_headers, claim_lease, batch, delete_files):
    """提交后补投或租约过期都保持同一输入、消息身份和 Turn 结果归属。"""
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    await _provider(e2e_client, e2e_headers)
    slug = await _agent(e2e_client, e2e_headers, str(me.json()["uid"]))
    conn = await asyncpg.connect(postgres_dsn())
    thread_id = turn_id = None
    process = None
    try:
        response = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"agent_id": slug, "title": make_test_session_title("projection-recovery")},
        )
        assert response.status_code == 201, response.text
        thread_id = response.json()["id"]
        await conn.execute("UPDATE sessions SET queue_paused=true WHERE thread_id=$1", thread_id)
        input_ids = []
        file_ids = []
        for index in range(2 if batch else 1):
            messages = [_message(OUTPUT), _message("SECOND_INPUT")]
            attachment_ids = []
            if batch:
                uploaded = await e2e_client.post(
                    "/api/v1/agents/files",
                    headers=e2e_headers,
                    files={"file": (f"batch-{index}.txt", f"batch input {index}".encode(), "text/plain")},
                )
                assert uploaded.status_code == 201, uploaded.text
                attachment_ids = [uploaded.json()["id"]]
                file_ids.extend(attachment_ids)
                messages = [
                    _message(
                        f"{OUTPUT} DETERMINISTIC_PNG_INPUT DETERMINISTIC_ATTACHMENT_BATCH "
                        f"BATCH_INPUT_{index}_MESSAGE_0 DETERMINISTIC_ATTACHMENT_ANCHOR:none"
                    ),
                    _message(
                        f"SECOND_INPUT DETERMINISTIC_ATTACHMENT_BATCH BATCH_INPUT_{index}_MESSAGE_1 "
                        f"DETERMINISTIC_ATTACHMENT_ANCHOR:batch-{index}.txt"
                    ),
                ]
                png = BytesIO()
                Image.new("RGB", (16, 12), "red").save(png, format="PNG")
                messages[0]["content"].append(
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode(),
                    }
                )
            # 故障窗口从接收完成开始；公开用户恢复暂停队列由独立 HTTP E2E 验证。
            load_models()
            pg_manager.initialize()
            try:
                async with pg_manager.get_async_session_context() as db:
                    receipt = await accept_message(
                        db=db,
                        scope=ActorScope(uid=str(me.json()["uid"]), app_id=None),
                        thread_id=thread_id,
                        idempotency_key=str(uuid.uuid4()),
                        source="test_queue",
                        mode="steer" if batch else "follow_up",
                        messages=input_messages_to_domain([InputMessage.model_validate(item) for item in messages]),
                        attachment_file_ids=attachment_ids,
                    )
            finally:
                await pg_manager.close()
            input_ids.append(receipt["input_id"])
            assert receipt["run_id"] is None
        assert await conn.fetchval("SELECT count(*) FROM messages WHERE source_input_id=ANY($1::varchar[])", input_ids) == 0

        if delete_files:
            prepared = await conn.fetch("SELECT status, object_name FROM agent_attachments WHERE id=ANY($1::varchar[])", file_ids)
            assert len(prepared) == len(file_ids)
            assert all(row["status"] == "ready" and row["object_name"] is None for row in prepared)
            workdir = await conn.fetchval(
                "SELECT p.workdir_path FROM projects p JOIN sessions s ON s.project_id=p.id WHERE s.thread_id=$1",
                thread_id,
            )
            removed = await e2e_client.delete("/api/workspace/file", headers=e2e_headers, params={"path": f"/{workdir}"})
            assert removed.status_code == 200, removed.text
            parent, name = workdir.rsplit("/", 1)
            restored = await e2e_client.post("/api/workspace/directory", headers=e2e_headers, json={"parent_path": f"/{parent}", "name": name})
            assert restored.status_code == 200, restored.text

        process = multiprocessing.get_context("spawn").Process(target=_consume_and_exit, args=(thread_id, claim_lease))
        process.start()
        await asyncio.to_thread(process.join, 30)
        assert process.exitcode == 17, "故障进程没有在消费提交后退出"
        consumed = await conn.fetch("SELECT * FROM agent_inputs WHERE id=ANY($1::varchar[]) ORDER BY received_seq", input_ids)
        turn_id, run_id = consumed[0]["turn_id"], consumed[0]["consumed_run_id"]
        assert all((item["turn_id"], item["consumed_run_id"]) == (turn_id, run_id) for item in consumed)
        received_at = {item["id"]: item["created_at"] for item in consumed}
        before = await conn.fetch(
            "SELECT id,source_input_id,input_position,received_at,created_at,turn_id,run_id FROM messages "
            "WHERE source_input_id=ANY($1::varchar[]) ORDER BY id",
            input_ids,
        )
        assert [(row["source_input_id"], row["input_position"]) for row in before] == [(id_, pos) for id_ in input_ids for pos in (0, 1)]
        assert all(row["received_at"] == received_at[row["source_input_id"]] <= row["created_at"] for row in before)
        assert all((row["turn_id"], row["run_id"]) == (turn_id, run_id) for row in before)
        if batch:
            for input_id, file_id in zip(input_ids, file_ids, strict=True):
                anchor = await conn.fetchrow(
                    "SELECT m.source_input_id,m.input_position FROM agent_attachments a JOIN messages m ON m.id=a.message_id WHERE a.id=$1",
                    file_id,
                )
                assert (anchor["source_input_id"], anchor["input_position"]) == (input_id, 1)
                snapshot = await e2e_client.get(
                    f"/api/v1/agents/sessions/{thread_id}/inputs/{input_id}",
                    headers=e2e_headers,
                )
                items = snapshot.json()["items"]
                assert not items[0]["yuxi"].get("attachments")
                assert items[0]["content"][1]["type"] == "input_image"
                assert items[0]["content"][1]["image_url"].startswith("data:image/png;base64,")
                assert [file["file_id"] for file in items[1]["yuxi"]["attachments"]] == [file_id]

        # 恢复本身按 30 秒周期触发，不能复用普通执行的 30 秒总等待。
        turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread_id, turn_id, timeout=90)
        if claim_lease:
            assert turn["status"] == "failed", turn
            assert turn["yuxi"]["result_run_id"] is None
            assert await conn.fetchval("SELECT error_type FROM agent_runs WHERE id=$1", run_id) == "worker_lease_expired"
            assert await conn.fetchval("SELECT count(*) FROM agent_inputs WHERE thread_id=$1 AND status='pending'", thread_id) == 0
            assert not await conn.fetchval("SELECT queue_paused FROM sessions WHERE thread_id=$1", thread_id)
        elif delete_files:
            assert turn["status"] == "failed", turn
            assert turn["yuxi"]["result_run_id"] is None
            failure = await conn.fetchrow("SELECT error_message, first_model_request_at FROM agent_runs WHERE id=$1", run_id)
            assert "附件文件已不存在或不可访问" in failure["error_message"]
            assert failure["first_model_request_at"] is None
        else:
            assert turn["status"] == "completed", turn
            assert turn["yuxi"]["result_run_id"] == run_id
            assert output_text(turn["yuxi"]["output"]) == OUTPUT
        assert await conn.fetch("SELECT * FROM agent_inputs WHERE id=ANY($1::varchar[]) ORDER BY received_seq", input_ids) == consumed
        assert (
            await conn.fetch(
                "SELECT id,source_input_id,input_position,received_at,created_at,turn_id,run_id FROM messages "
                "WHERE source_input_id=ANY($1::varchar[]) ORDER BY id",
                input_ids,
            )
            == before
        )
        assert await conn.fetchval("SELECT count(*) FROM agent_runs WHERE thread_id=$1", thread_id) == 1
        assert await conn.fetchval("SELECT count(*) FROM agent_run_attempts WHERE run_id=$1", run_id) == 1
    finally:
        if process is not None and process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join)
        if thread_id:
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        await conn.close()
        await delete_agent(e2e_client, e2e_headers, slug)
