"""真实 worker 经 HTTP MCP 检索、读取知识正文与引用。"""

import asyncio
import json
import uuid
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from zipfile import ZipFile

import asyncpg
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from test.e2e.e2e_helpers import (
    archive_public_thread,
    output_text,
    postgres_dsn,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
    wait_model_provider_cache,
)
from test.live_api_cleanup import make_test_session_title
from test.support.openai_replay_server import send_openai_sse_response, serve_replay_handler
from yuxi.bootstrap.capabilities import knowledge_enabled
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.client import MinIOClient
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.knowledge.models import KnowledgeFile

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.self_managed_resources, pytest.mark.timeout(300)]
CONTENT = b"MCP transport proof.\nThe persistent needle belongs to this document.\n"


@pytest.mark.parametrize("skill_source", ["builtin", "shared", "agent_bound"])
async def test_worker_uses_mcp_for_five_tools_and_preserves_sources(e2e_client, e2e_headers, skill_source):
    """固定模型只发调用；索引、消息、正文和引用由真实系统独立证明。"""
    suffix = uuid.uuid4().hex[:12]
    provider = f"mcp-e2e-{suffix}"
    slug = f"mcp-tools-{suffix}"
    file_id = f"pytest_mcp_{suffix}"
    kb_id = thread_id = turn_id = None
    observed = {}
    prompts = []
    advertised = []
    skill_slug = "knowledge-base" if skill_source == "builtin" else f"mcp-skill-{suffix}"
    shared_skill_created = False
    marker = f"CURRENT_KNOWLEDGE_SKILL_{suffix}"
    skill_content = (
        f"---\nname: {skill_slug}\nslug: {skill_slug}\ndescription: Current knowledge MCP dependency\n"
        f"mcp_dependencies: [knowledge]\n---\n# Knowledge MCP\n{marker}\n"
    )
    calls = []
    buckets = []
    minio = get_minio_client()

    class Handler(BaseHTTPRequestHandler):
        """用确定性向量和工具脚本驱动真实模型适配器。"""

        def do_POST(self):
            """仅重放外部模型协议，不代替 MCP、数据库或沙盒。"""
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/embeddings"):
                inputs = request["input"]
                if isinstance(inputs, str):
                    inputs = [inputs]
                body = json.dumps(
                    {
                        "object": "list",
                        "data": [{"object": "embedding", "index": i, "embedding": [1, 0, 0, 0]} for i in range(len(inputs))],
                        "model": "replay-embedding",
                        "usage": {"prompt_tokens": 1, "total_tokens": 1},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            prompts.append(json.dumps(request["messages"], ensure_ascii=False))
            advertised.append({item["function"]["name"] for item in request.get("tools", [])})
            for message in request["messages"]:
                if message.get("role") == "tool":
                    observed[message["tool_call_id"]] = message["content"]
            step = len(observed)
            if step < len(calls):
                name, args = calls[step]
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call-mcp-{step}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "MCP_AGENT_OK: The persistent needle belongs to this document."}
                finish = "stop"
            common = {"id": "chatcmpl-mcp", "object": "chat.completion.chunk", "created": 1, "model": request["model"]}
            chunks = [
                {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            body = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """不记录用户上下文和出站凭据。"""

    load_models()
    pg_manager.initialize()
    with serve_replay_handler(Handler) as replay_url:
        try:
            await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider)
            configured = await e2e_client.put(
                f"/api/system/model-providers/{provider}",
                headers=e2e_headers,
                json={
                    "embedding_base_url": replay_url + "/embeddings",
                    "capabilities": ["chat", "embedding"],
                    "enabled_models": [
                        {"id": "deterministic-chat", "display_name": "Replay chat", "type": "chat", "source": "manual"},
                        {"id": "replay-embedding", "display_name": "Replay embedding", "type": "embedding", "dimension": 4},
                    ],
                },
            )
            assert configured.status_code == 200, configured.text
            await wait_model_provider_cache()
            created = await e2e_client.post(
                "/api/knowledge/knowledge-bases",
                headers=e2e_headers,
                json={
                    "name": f"pytest_mcp_agent_{suffix}",
                    "description": "MCP assembled-path fixture",
                    "kb_type": "milvus",
                    "embedding_model_spec": f"{provider}:replay-embedding",
                    "additional_params": {},
                },
            )
            assert created.status_code == 200, created.text
            kb_id = created.json()["kb_id"]
            buckets = [
                (MinIOClient.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}.md"),
                (MinIOClient.KB_BUCKETS["documents"], f"{kb_id}/{file_id}.md"),
            ]
            parsed, original = [await minio.aupload_file(bucket, key, CONTENT, content_type="text/markdown") for bucket, key in buckets]
            async with pg_manager.get_async_session_context() as db:
                db.add(
                    KnowledgeFile(
                        file_id=file_id,
                        kb_id=kb_id,
                        filename="mcp-agent-proof.md",
                        file_type="md",
                        content_type="text/markdown",
                        markdown_file=parsed.url,
                        minio_url=original.url,
                        path=original.url,
                        status="parsed",
                        is_folder=False,
                    )
                )
            indexed = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/documents/index",
                headers=e2e_headers,
                json={"file_ids": [file_id], "params": {}},
            )
            assert indexed.status_code == 200, indexed.text
            async with asyncio.timeout(120):
                while True:
                    job = await e2e_client.get(f"/api/background-jobs/{indexed.json()['job_id']}", headers=e2e_headers)
                    assert job.status_code == 200, job.text
                    if job.json()["job"]["status"] in {"success", "failed", "cancelled"}:
                        assert job.json()["job"]["status"] == "success", job.json()
                        break
                    await asyncio.sleep(0.2)
            async with pg_manager.get_async_session_context() as db:
                record = await db.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
                assert record.status == "indexed" and record.chunk_count > 0
            calls.extend(
                [
                    ("list_kbs", {}),
                    ("query_kb", {"kb_id": kb_id, "query_text": "persistent needle"}),
                    ("search_file", {"kb_id": kb_id, "query": "mcp-agent-proof"}),
                    ("open_kb_document", {"kb_id": kb_id, "file_id": file_id}),
                    ("find_kb_document", {"kb_id": kb_id, "file_id": file_id, "patterns": ["needle"]}),
                ]
            )
            if skill_source == "shared":
                prepared = await e2e_client.post(
                    "/api/skills/import/prepare",
                    headers=e2e_headers,
                    files={"file": ("SKILL.md", skill_content.encode(), "text/markdown")},
                )
                assert prepared.status_code == 200, prepared.text
                confirmed = await e2e_client.post(
                    f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}/confirm",
                    headers=e2e_headers,
                    json={"slugs": [skill_slug], "share_config": None},
                )
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["data"][0]["success"] is True, confirmed.text
                shared_skill_created = True
            agent = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": slug,
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {
                            "model": f"{provider}:deterministic-chat",
                            "tools": [],
                            "knowledges": [kb_id],
                            "mcps": [],
                            "skills": [] if skill_source == "agent_bound" else [skill_slug],
                            "preload_skills": [] if skill_source == "agent_bound" else [skill_slug],
                        }
                    },
                },
            )
            assert agent.status_code == 200, agent.text
            if skill_source == "agent_bound":
                package = BytesIO()
                with ZipFile(package, "w") as archive:
                    archive.writestr("SKILL.md", skill_content)
                bound = await e2e_client.post(
                    f"/api/agent/{slug}/self-skill/upload",
                    headers=e2e_headers,
                    files={"file": ("knowledge.zip", package.getvalue(), "application/zip")},
                )
                assert bound.status_code == 200, bound.text
                skill_slug = bound.json()["skill"]["slug"]
            current_skill = await e2e_client.get(f"/api/system/skills/{skill_slug}", headers=e2e_headers)
            assert current_skill.status_code == 200, current_skill.text
            assert current_skill.json()["data"]["mcp_dependencies"] == ["knowledge"]
            assert current_skill.json()["data"]["tool_dependencies"] == []
            response = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": suffix},
                json={"agent_id": slug, "input": "MCP_AGENT_TOOLS", "title": make_test_session_title("mcp-tools")},
            )
            assert response.status_code == 201, response.text
            thread_id = response.json()["id"]
            accepted = await wait_for_consumed_input(e2e_client, e2e_headers, response.json()["yuxi"]["receipt"])
            turn_id = accepted["turn_id"]
            turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread_id, turn_id, timeout=120)
            assert turn["status"] == "completed", turn
            assert "MCP_AGENT_OK" in output_text(turn["yuxi"]["output"])
            assert set(observed) == {f"call-mcp-{index}" for index in range(5)}
            assert {name for name, _ in calls}.issubset(advertised[0]), advertised[0]
            if skill_source != "builtin":
                assert marker in prompts[0]
            assert [item["kb_id"] for item in json.loads(observed["call-mcp-0"])] == [kb_id]
            assert "persistent needle" in observed["call-mcp-1"]
            assert file_id in observed["call-mcp-2"]
            assert "persistent needle" in observed["call-mcp-3"]
            assert json.loads(observed["call-mcp-4"])["total_matches"] == 1
            sources = await e2e_client.get(f"/api/agent/threads/{thread_id}/turns/{turn_id}/references", headers=e2e_headers)
            assert sources.status_code == 200, sources.text
            assert sources.json()["available"] is True
            assert any(source["file_id"] == file_id and "persistent needle" in source["content"] for source in sources.json()["sources"])
            conn = await asyncpg.connect(postgres_dsn())
            try:
                manifest = json.loads(await conn.fetchval("SELECT manifest FROM agent_runs WHERE id=$1", accepted["run_id"]))
                rows = await conn.fetch(
                    "SELECT execution_status, extra_metadata FROM messages WHERE run_id=$1 AND message_type='tool_audit'",
                    accepted["run_id"],
                )
            finally:
                await conn.close()
            assert skill_slug in {item["slug"] for item in manifest["resources"]["skills"]}
            assert len(rows) == 5 and all(row["execution_status"] == "completed" for row in rows), rows
            assert all(json.loads(row["extra_metadata"])["output"]["status"] == "success" for row in rows)
        finally:
            if thread_id:
                await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
            removed = await e2e_client.delete(f"/api/agent/{slug}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
            if shared_skill_created:
                removed = await e2e_client.delete(f"/api/system/skills/{skill_slug}", headers=e2e_headers)
                assert removed.status_code == 200, removed.text
            if kb_id:
                removed = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
                assert removed.status_code == 200, removed.text
            for bucket, key in buckets:
                await minio.adelete_file(bucket, key)
            removed = await e2e_client.delete(f"/api/system/model-providers/{provider}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
            await pg_manager.close()


async def test_worker_preserves_selection_and_completes_after_knowledge_error(e2e_client, e2e_headers):
    """知识操作失败后继续完成回答，持久选择不被不可用资源清空。"""
    suffix = uuid.uuid4().hex[:12]
    provider = f"mcp-error-{suffix}"
    slug = f"mcp-error-{suffix}"
    selection = "all"
    unavailable_kb_id = f"kb_unavailable_{suffix}"
    thread_id = turn_id = None
    observed = []
    advertised = []
    knowledge_deployed = knowledge_enabled()

    class Handler(BaseHTTPRequestHandler):
        """发起一个知识工具，再根据真实错误继续生成最终回答。"""

        def do_POST(self):
            """重放模型协议并记录模型实际接收的工具结果。"""
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            advertised.append({item["function"]["name"] for item in request.get("tools", [])})
            observed[:] = [message["content"] for message in request["messages"] if message.get("role") == "tool"]
            if observed:
                delta, finish = {"role": "assistant", "content": "KNOWLEDGE_FAILURE_HANDLED"}, "stop"
            else:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-knowledge-unavailable",
                            "type": "function",
                            "function": {"name": "query_kb", "arguments": json.dumps({"kb_id": unavailable_kb_id, "query_text": "needle"})},
                        }
                    ],
                }
                finish = "tool_calls"
            common = {"id": "chatcmpl-mcp-error", "object": "chat.completion.chunk", "created": 1, "model": request["model"]}
            send_openai_sse_response(
                self,
                [
                    {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                ],
            )

        def log_message(self, *_args):
            """不记录用户上下文或凭据。"""

    with serve_replay_handler(Handler) as replay_url:
        try:
            await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider)
            created = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": slug,
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {
                            "model": f"{provider}:deterministic-chat",
                            "tools": [],
                            "knowledges": selection,
                            "mcps": [],
                            "skills": ["knowledge-base"],
                            "preload_skills": ["knowledge-base"],
                        }
                    },
                },
            )
            assert created.status_code == 200, created.text
            response = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": suffix},
                json={"agent_id": slug, "input": "Continue after unavailable knowledge", "title": make_test_session_title("mcp-error")},
            )
            assert response.status_code == 201, response.text
            thread_id = response.json()["id"]
            accepted = await wait_for_consumed_input(e2e_client, e2e_headers, response.json()["yuxi"]["receipt"])
            turn_id = accepted["turn_id"]
            turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread_id, turn_id)
            assert turn["status"] == "completed", turn
            assert "KNOWLEDGE_FAILURE_HANDLED" in output_text(turn["yuxi"]["output"])
            assert turn["yuxi"]["result_run_id"] == accepted["run_id"]
            assert advertised and all(("query_kb" in names) is knowledge_deployed for names in advertised), advertised
            assert len(observed) == 1, observed
            expected_error = "Knowledge tool failed (404)" if knowledge_deployed else "能力受限：当前调用者无权使用该工具。"
            assert expected_error in observed[0], observed
            conn = await asyncpg.connect(postgres_dsn())
            try:
                snapshot = await conn.fetchval("SELECT config_snapshot FROM sessions WHERE thread_id=$1", thread_id)
                run = await conn.fetchrow(
                    "SELECT r.status, r.turn_id, m.run_id AS output_run_id, m.content AS output_content "
                    "FROM agent_runs r JOIN messages m ON m.id=r.output_message_id WHERE r.id=$1",
                    accepted["run_id"],
                )
                models = await conn.fetch(
                    "SELECT operation_id, extra_metadata FROM messages WHERE run_id=$1 AND message_type='model_audit'",
                    accepted["run_id"],
                )
                audits = await conn.fetch(
                    "SELECT run_id, turn_id, operation_id, execution_status, extra_metadata "
                    "FROM messages WHERE run_id=$1 AND message_type='tool_audit'",
                    accepted["run_id"],
                )
            finally:
                await conn.close()
            assert json.loads(snapshot)["knowledges"] == selection
            assert (run["status"], run["turn_id"], run["output_run_id"]) == ("completed", turn_id, accepted["run_id"])
            assert "KNOWLEDGE_FAILURE_HANDLED" in run["output_content"]
            if knowledge_deployed:
                assert len(audits) == 1, audits
                audit = audits[0]
                assert (audit["run_id"], audit["turn_id"], audit["operation_id"]) == (
                    accepted["run_id"],
                    turn_id,
                    "call-knowledge-unavailable",
                )
                assert audit["execution_status"] == "failed"
                metadata = json.loads(audit["extra_metadata"])
                assert metadata["tool_name"] == "query_kb"
                assert metadata["input"] == {"kb_id": unavailable_kb_id, "query_text": "needle"}
                assert metadata["output"]["status"] == "error"
                assert expected_error in json.dumps(metadata["output"], ensure_ascii=False)
            else:
                # 授权在 BaseTool 之前拒绝：没有真实执行生命周期，拒绝结果由中间件审计留痕，
                # 拒绝 ToolMessage 本身仍由 checkpoint 拥有。
                assert len(audits) == 1, audits
                audit = audits[0]
                assert (audit["run_id"], audit["turn_id"], audit["operation_id"]) == (
                    accepted["run_id"],
                    turn_id,
                    "call-knowledge-unavailable",
                )
                assert audit["execution_status"] == "failed"
                metadata = json.loads(audit["extra_metadata"])
                assert metadata["tool_name"] == "query_kb"
                assert metadata["source"] == "middleware"
                assert metadata["input"] == {"kb_id": unavailable_kb_id, "query_text": "needle"}
                assert "能力受限" in metadata["error_message"]
                declarations = [
                    (model["operation_id"], call)
                    for model in models
                    for call in json.loads(model["extra_metadata"]).get("tool_calls", [])
                    if call["id"] == "call-knowledge-unavailable"
                ]
                assert len(declarations) == 1, declarations
                operation_id, declaration = declarations[0]
                assert declaration["name"] == "query_kb"
                assert declaration["args"] == {"kb_id": unavailable_kb_id, "query_text": "needle"}
                async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
                    saved = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
                assert saved is not None
                assert saved.metadata["yuxi_run_id"] == accepted["run_id"]
                assert saved.metadata["turn_id"] == turn_id
                messages = saved.checkpoint["channel_values"]["messages"]
                source_models = [message for message in messages if isinstance(message, AIMessage) and message.id == operation_id]
                assert len(source_models) == 1
                assert declaration in source_models[0].tool_calls
                rejected = [
                    message for message in messages if isinstance(message, ToolMessage) and message.tool_call_id == declaration["id"]
                ]
                assert len(rejected) == 1 and rejected[0].status == "error"
                assert rejected[0].content == expected_error
                assert isinstance(messages[-1], AIMessage)
                assert messages[-1].text == run["output_content"]
        finally:
            if thread_id:
                await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
            for path in (f"/api/agent/{slug}", f"/api/system/model-providers/{provider}"):
                removed = await e2e_client.delete(path, headers=e2e_headers)
                assert removed.status_code in {200, 404}, removed.text
