from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from e2e_helpers import cancel_run, consume_events, wait_for_run
from test.live_api_cleanup import remove_e2e_thread_storage
from yuxi.agents.skills.service import get_personal_skills_root_dir, get_thread_skills_root_dir
from yuxi.utils.paths import VIRTUAL_PATH_WORKSPACE_SKILLS

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def test_main_agent_reads_personal_skill_from_workspace(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    """真实主 Agent 应直接从个人工作区读取 SKILL.md。"""
    uid = e2e_agent_context["uid"]
    marker = f"PERSONAL_SKILL_E2E_{uuid.uuid4().hex[:10].upper()}"
    slug = f"pytest-personal-agent-{uuid.uuid4().hex[:8]}"
    agent_slug = f"e2e-personal-skill-{uuid.uuid4().hex[:8]}"
    skill_md = (
        f"---\nname: {slug}\ndescription: Return the verification marker when explicitly requested.\n---\n"
        f"# Verification\nWhen the user asks for the personal Skill marker, reply with exactly `{marker}`.\n"
    )
    run_id: str | None = None
    thread_id: str | None = None
    agent_created = False

    prepare_response = await e2e_client.post(
        "/api/skills/import/prepare",
        headers=e2e_headers,
        files={"file": ("SKILL.md", skill_md.encode(), "text/markdown")},
    )
    assert prepare_response.status_code == 200, prepare_response.text
    draft = prepare_response.json()["data"]
    confirm_response = await e2e_client.post(
        f"/api/skills/personal/install-drafts/{draft['draft_id']}/confirm",
        headers=e2e_headers,
        json={"slugs": [draft["items"][0]["slug"]]},
    )
    assert confirm_response.status_code == 200, confirm_response.text

    try:
        default_response = await e2e_client.get("/api/agent/default", headers=e2e_headers)
        assert default_response.status_code == 200, default_response.text
        default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}
        context: dict[str, Any] = {
            "system_prompt": (
                f"收到请求后必须先读取 {VIRTUAL_PATH_WORKSPACE_SKILLS}/{slug}/SKILL.md，"
                "然后严格遵循其中的 Verification 指令，不要添加解释。"
            ),
            "tools": [],
            "knowledges": [],
            "mcps": [],
            "skills": [slug],
            "subagents": [],
        }
        if default_context.get("model"):
            context["model"] = default_context["model"]

        agent_response = await e2e_client.post(
            "/api/agent",
            headers=e2e_headers,
            json={
                "name": f"E2E Personal Skill {slug[-8:]}",
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "description": "真实个人 Skill Agent E2E 临时智能体",
                "config_json": {"context": context},
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                    "manage_scope": None,
                },
            },
        )
        assert agent_response.status_code == 200, agent_response.text
        agent_created = True

        thread_response = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={
                "agent_id": agent_slug,
                "title": f"personal-skill-e2e-{slug[-8:]}",
                "metadata": {"_yuxi_e2e": True, "test": "personal-skill-e2e"},
            },
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_payload = thread_response.json()
        thread_id = str(thread_payload.get("thread_id") or thread_payload["id"])

        run_response = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "query": "请读取并返回 personal Skill marker。",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": f"personal-skill-e2e-{uuid.uuid4()}"},
            },
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])

        event_counts = await consume_events(e2e_client, e2e_headers, run_id)
        assert event_counts.get("messages", 0) > 0, event_counts
        run_payload = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run_payload.get("status") == "completed", run_payload

        result_response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result_response.status_code == 200, result_response.text
        assert marker in str(result_response.json().get("output") or ""), result_response.text

        personal_skill = get_personal_skills_root_dir(uid) / slug / "SKILL.md"
        assert personal_skill.read_text(encoding="utf-8") == skill_md
        assert not (get_thread_skills_root_dir(thread_id) / slug).exists()
    finally:
        await cancel_run(e2e_client, e2e_headers, run_id)
        if thread_id:
            thread_delete = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
            assert thread_delete.status_code in {200, 404}, thread_delete.text
            remove_e2e_thread_storage(thread_id)
        if agent_created:
            agent_delete = await e2e_client.delete(f"/api/agent/{agent_slug}", headers=e2e_headers)
            assert agent_delete.status_code in {200, 404}, agent_delete.text
        skill_delete = await e2e_client.delete(f"/api/skills/personal/{slug}", headers=e2e_headers)
        assert skill_delete.status_code in {200, 404}, skill_delete.text
