from __future__ import annotations

import uuid

import pytest


@pytest.mark.self_managed_resources
@pytest.mark.integration
@pytest.mark.asyncio
async def test_personal_skill_install_list_preview_and_delete_without_database_record(
    test_client,
    standard_user,
):
    """个人 Skill 应完成真实 API 生命周期，同时不改变共享 Skill 数据。"""
    headers = standard_user["headers"]
    slug = f"pytest-personal-{uuid.uuid4().hex[:8]}"
    skill_md = f"---\nname: {slug}\ndescription: integration personal skill\n---\n# Personal\n"

    shared_before = await test_client.get("/api/system/skills", headers=headers)
    assert shared_before.status_code == 200, shared_before.text
    shared_count = len(shared_before.json().get("data") or [])

    prepare_response = await test_client.post(
        "/api/skills/import/prepare",
        headers=headers,
        files={"file": ("SKILL.md", skill_md.encode(), "text/markdown")},
    )
    assert prepare_response.status_code == 200, prepare_response.text
    draft = prepare_response.json()["data"]

    try:
        confirm_response = await test_client.post(
            f"/api/skills/personal/install-drafts/{draft['draft_id']}/confirm",
            headers=headers,
            json={"slugs": [draft["items"][0]["slug"]]},
        )
        assert confirm_response.status_code == 200, confirm_response.text
        assert confirm_response.json()["data"][0]["slug"] == slug
        assert "share_config" not in confirm_response.json()["data"][0]["skill"]

        replacement = await test_client.post(
            "/api/skills/import/prepare",
            headers=headers,
            files={"file": ("SKILL.md", skill_md.replace("# Personal", "# Replacement").encode(), "text/markdown")},
        )
        assert replacement.status_code == 200, replacement.text
        replacement_id = replacement.json()["data"]["draft_id"]
        try:
            repeated = await test_client.post(
                f"/api/skills/personal/install-drafts/{replacement_id}/confirm",
                headers=headers,
                json={"slugs": [slug]},
            )
            assert repeated.status_code == 200, repeated.text
            result = repeated.json()["data"][0]
            assert result["success"] is False and "已存在同名 Skill" in result["error"], result
        finally:
            discarded = await test_client.delete(f"/api/skills/install-drafts/{replacement_id}", headers=headers)
            assert discarded.status_code in {200, 404}, discarded.text

        cards_response = await test_client.get(
            "/api/skills",
            headers=headers,
        )
        assert cards_response.status_code == 200, cards_response.text
        personal = [item for item in cards_response.json()["data"] if item["slug"] == slug and item["source_scope"] == "personal"]
        assert len(personal) == 1
        assert "share_config" not in personal[0]
        assert "tool_dependencies" not in personal[0]

        accessible_response = await test_client.get("/api/skills/accessible", headers=headers)
        assert accessible_response.status_code == 200, accessible_response.text
        accessible_personal = next(
            item for item in accessible_response.json()["data"] if item["slug"] == slug and item["source_scope"] == "personal"
        )
        assert "share_config" not in accessible_personal

        preview_response = await test_client.get(
            f"/api/skills/personal/{slug}/file",
            headers=headers,
            params={"path": "SKILL.md"},
        )
        assert preview_response.status_code == 200, preview_response.text
        assert preview_response.json()["data"]["content"] == skill_md

        shared_after = await test_client.get("/api/system/skills", headers=headers)
        assert shared_after.status_code == 200, shared_after.text
        assert len(shared_after.json().get("data") or []) == shared_count
    finally:
        delete_response = await test_client.delete(f"/api/skills/personal/{slug}", headers=headers)
        assert delete_response.status_code in {200, 404}, delete_response.text
