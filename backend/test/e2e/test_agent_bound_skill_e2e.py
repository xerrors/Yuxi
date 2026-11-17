"""Agent 专属 Skill 自动注入真实 worker，按 Run 清单和投影验收。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from io import BytesIO
from zipfile import ZipFile

import asyncpg
import httpx
import pytest
from e2e_helpers import (
    RUN_TIMEOUT_SECONDS,
    archive_public_thread,
    delete_agent,
    iter_public_thread_events,
    postgres_dsn,
    wait_for_blocked_response,
    wait_for_consumed_input,
    wait_model_provider_cache,
)

from test.live_api_cleanup import make_test_session_title
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(360)]


@pytest.mark.parametrize(
    "skill_source,edit_during_run,create_with_resources",
    [("bound", False, False), ("bound", True, False), ("bound", False, True), ("shared", False, False)],
)
async def test_preloaded_skill_edit_is_loaded_by_the_worker(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    skill_source: str,
    edit_during_run: bool,
    create_with_resources: bool,
    remote_tool,
):
    """验证 bound 与 shared Skill 编辑后的投影和运行清单。"""
    slug = f"pytest-run-skill-{uuid.uuid4().hex[:8]}"
    agent_slug = f"pytest-run-agent-{uuid.uuid4().hex[:8]}"
    marker_token = uuid.uuid4().hex
    marker = f"{'BOUND' if skill_source == 'bound' else 'UPDATED_SHARED'}_SKILL_{marker_token}"
    gate = str(uuid.uuid4())
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    provider_created = False
    skill_created = False
    agent_created = False
    thread_id = None
    run_id = None
    turn_id = None
    provider_id = f"ci-skill-replay-{uuid.uuid4().hex[:8]}"
    mcp_slug = f"ci-create-{uuid.uuid4().hex[:8]}" if create_with_resources else None
    try:
        provider = await e2e_client.post(
            "/api/system/model-providers",
            headers=e2e_headers,
            json={
                "provider_id": provider_id,
                "display_name": "Skill edit deterministic replay",
                "provider_type": "openai",
                "base_url": os.environ["OPENAI_REPLAY_URL"],
                "api_key": "ci-replay-key",
                "capabilities": ["chat"],
                "enabled_models": [{"id": "deterministic-chat", "display_name": "Replay", "type": "chat", "source": "manual"}],
                "is_enabled": True,
            },
        )
        assert provider.status_code == 200, provider.text
        provider_created = True
        await wait_model_provider_cache()
        if skill_source == "shared":
            original = f"---\nname: {slug}\nslug: {slug}\ndescription: before\n---\n# Before\n"
            prepared = await e2e_client.post(
                "/api/skills/import/prepare",
                headers=e2e_headers,
                files={"file": ("SKILL.md", original.encode(), "text/markdown")},
            )
            assert prepared.status_code == 200, prepared.text
            draft_id = prepared.json()["data"]["draft_id"]
            confirmed = await e2e_client.post(
                f"/api/skills/install-drafts/{draft_id}/confirm",
                headers=e2e_headers,
                json={"slugs": [slug], "share_config": None},
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["data"][0]["success"] is True
            skill_created = True
        payload = {
            "name": agent_slug,
            "slug": agent_slug,
            "backend_id": "ChatbotAgent",
            "visibility": "shared",
            "description": "专属 Skill 自动加载测试",
            "config_json": {
                "context": {
                    "model": f"{provider_id}:deterministic-chat",
                    "system_prompt": "不要调用工具，只输出 DETERMINISTIC_AGENT_E2E_OK。",
                    "tools": ["present_artifacts"],
                    "knowledges": [],
                    "mcps": [],
                    "skills": [slug] if skill_source == "shared" else [],
                    "preload_skills": [slug] if skill_source == "shared" else [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                "manage_scope": None,
            },
        }
        if create_with_resources:
            server = await e2e_client.post(
                "/api/system/mcp-servers",
                headers=e2e_headers,
                json={
                    "slug": mcp_slug,
                    "name": "Creation MCP",
                    "transport": "streamable_http",
                    "url": remote_tool["url"],
                },
            )
            assert server.status_code == 200, server.text
            payload["config_json"]["context"]["mcps"] = [mcp_slug]
            package = BytesIO()
            with ZipFile(package, "w") as archive:
                archive.writestr("SKILL.md", "---\nslug: create-guide\nname: Guide\ndescription: creation\n---\n\n# 操作指南\n")
                archive.writestr("references/creation.txt", "CREATION_REFERENCE")
            agent = await e2e_client.post(
                "/api/agent/with-skill",
                headers=e2e_headers,
                data={"agent": json.dumps(payload)},
                files={"file": ("guide.zip", package.getvalue(), "application/zip")},
            )
        else:
            agent = await e2e_client.post("/api/agent", headers=e2e_headers, json=payload)
        assert agent.status_code == 200, agent.text
        agent_created = True
        if skill_source == "bound":
            bound = await e2e_client.request(
                "GET" if create_with_resources else "POST", f"/api/agent/{agent_slug}/self-skill", headers=e2e_headers
            )
            assert bound.status_code == 200, bound.text
            slug = bound.json()["skill"]["slug"]
        root = await e2e_client.get(f"/api/system/skills/{slug}/file?path=SKILL.md", headers=e2e_headers)
        assert root.status_code == 200, root.text
        original = root.json()["data"]["content"]
        if skill_source == "bound":
            updated = original.replace("---\n\n# 操作指南", "---\n\n# 对话组件") + marker
        else:
            updated = original.replace("description: before", "description: after").replace("# Before", f"# 对话组件\n{marker}")
        saved = await e2e_client.put(
            f"/api/system/skills/{slug}/file",
            headers=e2e_headers,
            json={"path": "SKILL.md", "content": updated, "expected_revision": root.json()["data"]["revision"]},
        )
        assert saved.status_code == 200, saved.text
        if skill_source == "bound":
            history = await e2e_client.get(f"/api/system/skills/{slug}/versions", headers=e2e_headers)
            released = await e2e_client.post(
                f"/api/system/skills/{slug}/versions",
                headers=e2e_headers,
                json={"expected_revision": history.json()["data"]["revision"]},
            )
            assert released.status_code == 200, released.text
            changed = await e2e_client.put(
                f"/api/system/skills/{slug}/file",
                headers=e2e_headers,
                json={
                    "path": "SKILL.md",
                    "content": updated.replace(marker, "UNRELEASED_GUIDE"),
                    "expected_revision": saved.json()["data"]["revision"],
                },
            )
            assert changed.status_code == 200, changed.text
            latest = await e2e_client.get(f"/api/system/skills/{slug}/versions", headers=e2e_headers)
            restored = await e2e_client.post(
                f"/api/system/skills/{slug}/versions/{released.json()['data']['version']}/restore",
                headers=e2e_headers,
                json={"expected_revision": latest.json()["data"]["revision"]},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["data"]["skill"]["slug"] == slug
        thread = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": f"skill-edit-thread-{uuid.uuid4()}"},
            json={"agent_id": agent_slug, "title": make_test_session_title("shared-skill-edit")},
        )
        assert thread.status_code == 201, thread.text
        thread_id = str(thread.json()["id"])
        run = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**e2e_headers, "Idempotency-Key": f"skill-edit-input-{uuid.uuid4()}"},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "DETERMINISTIC_AGENT_E2E_OK"
                                        + (f" DETERMINISTIC_BOUND_ROOT:{marker_token}" if skill_source == "bound" else "")
                                        + (" DETERMINISTIC_CREATE_MCP" if create_with_resources else "")
                                        + (f" DETERMINISTIC_BLOCK_BEFORE_RESPONSE:{gate}" if edit_during_run else ""),
                                    }
                                ],
                            }
                        ],
                        "yuxi": {"mode": "follow_up"},
                    }
                ]
            },
        )
        assert run.status_code == 202, run.text
        consumed = await wait_for_consumed_input(e2e_client, e2e_headers, run.json())
        run_id, turn_id = consumed["run_id"], consumed["turn_id"]
        if edit_during_run:
            async with httpx.AsyncClient(base_url=os.environ["OPENAI_REPLAY_URL"].removesuffix("/v1"), timeout=10) as replay:
                await wait_for_blocked_response(replay, gate)
                changed = updated.replace(marker, f"BOUND_SKILL_{uuid.uuid4().hex}")
                edited = await e2e_client.put(
                    f"/api/system/skills/{slug}/file",
                    headers=e2e_headers,
                    json={
                        "path": "SKILL.md",
                        "content": changed,
                        "expected_revision": hashlib.sha256(updated.encode()).hexdigest(),
                    },
                )
                assert edited.status_code == 200, edited.text
                released = await replay.get("/release-blocking", params={"token": gate})
                assert released.status_code == 200
                # replay 在每个真实模型请求中要求旧的专属根标记，第二次模型请求验证执行中快照。
        async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
            async for event in iter_public_thread_events(e2e_client, e2e_headers, thread_id):
                if event.get("turn_id") == turn_id and event["type"] in {
                    "agent.session.turn.completed",
                    "agent.session.turn.failed",
                    "agent.session.turn.cancelled",
                }:
                    break
            else:
                pytest.fail("专属 Skill 的 Thread SSE 在 Turn 终态前断开")
        turn = await e2e_client.get(
            f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}",
            headers=e2e_headers,
        )
        assert turn.status_code == 200, turn.text
        assert turn.json()["status"] == "completed", turn.text
        assert turn.json()["yuxi"]["result_run_id"] == run_id, turn.text
        if edit_during_run:
            detail = await e2e_client.get(f"/api/system/skills/{slug}", headers=e2e_headers)
            assert (get_skill_data_dir() / detail.json()["data"]["dir_path"] / "SKILL.md").read_text() == changed
        else:
            assert get_user_skills_root_dir(uid).joinpath(slug, "SKILL.md").read_text(encoding="utf-8") == updated

        conn = await asyncpg.connect(postgres_dsn())
        try:
            raw_manifest = await conn.fetchval("SELECT manifest FROM agent_runs WHERE id = $1", run_id)
        finally:
            await conn.close()
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
        assert [item["slug"] for item in manifest["resources"]["skills"]] == [slug]
        if create_with_resources:
            assert manifest["resources"]["mcps"] == [mcp_slug]
            assert get_user_skills_root_dir(uid).joinpath(slug, "references/creation.txt").read_text() == "CREATION_REFERENCE"
            assert not remote_tool["effect"].exists()
        assert manifest["resources"]["skills"][0]["preload_content_hash"] == hashlib.sha256(updated.encode()).hexdigest()
    finally:
        if edit_during_run:
            async with httpx.AsyncClient(base_url=os.environ["OPENAI_REPLAY_URL"].removesuffix("/v1"), timeout=10) as replay:
                await replay.get("/release-blocking", params={"token": gate})
        if thread_id:
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        if agent_created:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        if mcp_slug:
            response = await e2e_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if provider_created:
            response = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if skill_created:
            response = await e2e_client.delete(f"/api/system/skills/{slug}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if agent_created:
            assert not get_user_skills_root_dir(uid).joinpath(slug).exists()
