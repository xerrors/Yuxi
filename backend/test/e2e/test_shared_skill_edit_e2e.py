"""共享 Skill 编辑后由真实 worker 加载新内容。"""

from __future__ import annotations

import hashlib
import json
import uuid

import asyncpg
import httpx
import pytest
from e2e_helpers import cancel_run, delete_agent, postgres_dsn, wait_for_run
from yuxi.services.skills.projection import get_user_skills_root_dir

from test_deterministic_agent_path_e2e import MODEL_SPEC, _create_provider, _delete_provider

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(360)]


async def test_edited_shared_skill_is_loaded_by_next_run(e2e_client: httpx.AsyncClient, e2e_headers: dict[str, str]):
    """从 HTTP 编辑到 worker Run，回读实际投影与持久运行清单。"""
    slug = f"pytest-run-skill-{uuid.uuid4().hex[:8]}"
    agent_slug = f"pytest-run-agent-{uuid.uuid4().hex[:8]}"
    original = f"---\nname: {slug}\nslug: {slug}\ndescription: before\n---\n# Before\n"
    marker = f"UPDATED_SHARED_SKILL_{uuid.uuid4().hex}"
    updated = original.replace(
        "description: before", "description: after\ntool_dependencies:\n- present_artifacts"
    ).replace("# Before", f"# 图片生成技能\n{marker}")
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    provider_created = False
    skill_created = False
    agent_created = False
    thread_id = None
    run_id = None
    try:
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

        saved = await e2e_client.put(
            f"/api/system/skills/{slug}/file",
            headers=e2e_headers,
            json={
                "path": "SKILL.md",
                "content": updated,
                "expected_revision": hashlib.sha256(original.encode()).hexdigest(),
            },
        )
        assert saved.status_code == 200, saved.text

        await _create_provider(e2e_client, e2e_headers)
        provider_created = True
        agent = await e2e_client.post(
            "/api/agent",
            headers=e2e_headers,
            json={
                "name": agent_slug,
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "description": "共享 Skill 编辑后加载测试",
                "config_json": {
                    "context": {
                        "model": MODEL_SPEC,
                        "system_prompt": "不要调用工具，只输出 DETERMINISTIC_AGENT_E2E_OK。",
                        "tools": [],
                        "knowledges": [],
                        "mcps": [],
                        "skills": [slug],
                        "preload_skills": [slug],
                        "subagents": [],
                    }
                },
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                    "manage_scope": None,
                },
            },
        )
        assert agent.status_code == 200, agent.text
        agent_created = True
        thread = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={"agent_id": agent_slug, "title": f"pytest-shared-edit-{uuid.uuid4().hex[:8]}"},
        )
        assert thread.status_code == 200, thread.text
        thread_id = str(thread.json().get("thread_id") or thread.json()["id"])
        run = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "query": "只输出 DETERMINISTIC_AGENT_E2E_OK",
                "meta": {"request_id": str(uuid.uuid4())},
            },
        )
        assert run.status_code == 200, run.text
        run_id = str(run.json()["run_id"])
        final = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert final["status"] == "completed", final
        assert get_user_skills_root_dir(uid).joinpath(slug, "SKILL.md").read_text(encoding="utf-8") == updated

        conn = await asyncpg.connect(postgres_dsn())
        try:
            raw_manifest = await conn.fetchval("SELECT manifest FROM agent_runs WHERE id = $1", run_id)
        finally:
            await conn.close()
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
        assert [item["slug"] for item in manifest["resources"]["skills"]] == [slug]
        assert (
            manifest["resources"]["skills"][0]["preload_content_hash"] == hashlib.sha256(updated.encode()).hexdigest()
        )
    finally:
        await cancel_run(e2e_client, e2e_headers, run_id)
        if thread_id:
            response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
        if agent_created:
            await delete_agent(e2e_client, e2e_headers, agent_slug)
        if provider_created:
            await _delete_provider(e2e_client, e2e_headers)
        if skill_created:
            response = await e2e_client.delete(f"/api/system/skills/{slug}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
