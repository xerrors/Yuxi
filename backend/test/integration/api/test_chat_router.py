"""
Integration tests for chat router endpoints.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_user_data_dir, sandbox_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_chat_endpoints_require_authentication(test_client):
    assert (await test_client.get("/api/chat/default_agent")).status_code == 401
    assert (await test_client.get("/api/chat/agent")).status_code == 401


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/chat/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    if not agents:
        pytest.skip("No agents available for chat router integration tests.")

    agent_id = agents[0].get("id")
    if not agent_id:
        pytest.skip("Agent payload missing id field.")

    create_resp = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": f"chat-router-test-{uuid.uuid4().hex[:8]}",
            "metadata": {},
        },
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    payload = create_resp.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, f"Create thread response missing thread identifier: {payload}"
    return thread_id


async def test_admin_can_list_agents(test_client, admin_headers):
    response = await test_client.get("/api/chat/agent", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["agents"], list)
    if payload["agents"]:
        assert "id" in payload["agents"][0]


async def test_admin_can_read_default_agent(test_client, admin_headers):
    response = await test_client.get("/api/chat/default_agent", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert "default_agent_id" in response.json()


async def test_setting_default_agent_requires_admin(test_client, admin_headers, standard_user):
    # Attempt as admin first to obtain a candidate agent id.
    agents_response = await test_client.get("/api/chat/agent", headers=admin_headers)
    assert agents_response.status_code == 200, agents_response.text
    agents = agents_response.json().get("agents", [])

    if not agents:
        pytest.skip("No agents are registered in the system.")

    candidate_agent_id = agents[0].get("id")
    if not candidate_agent_id:
        pytest.skip("Agent payload missing id field.")

    # Normal user should not be able to update the default agent.
    forbidden_response = await test_client.post(
        "/api/chat/set_default_agent",
        json={"agent_id": candidate_agent_id},
        headers=standard_user["headers"],
    )
    assert forbidden_response.status_code == 403

    # Admin should succeed.
    update_response = await test_client.post(
        "/api/chat/set_default_agent",
        json={"agent_id": candidate_agent_id},
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["default_agent_id"] == candidate_agent_id


async def test_save_thread_artifact_to_workspace_copies_output_file(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.md"

    ensure_thread_dirs(thread_id, user_id)
    source_path = sandbox_user_data_dir(thread_id) / "outputs" / filename
    source_path.write_text("# artifact\n", encoding="utf-8")

    response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": f"/home/gem/user-data/outputs/{filename}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["name"] == filename
    assert payload["source_path"] == f"/home/gem/user-data/outputs/{filename}"
    assert payload["saved_path"] == f"/home/gem/user-data/workspace/saved_artifacts/{filename}"

    saved_path = sandbox_workspace_dir(thread_id, user_id) / "saved_artifacts" / filename
    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == "# artifact\n"

    download_response = await test_client.get(payload["saved_artifact_url"], headers=headers)
    assert download_response.status_code == 200, download_response.text
    assert download_response.text == "# artifact\n"


async def test_save_thread_artifact_to_workspace_auto_renames_conflicts(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.txt"
    renamed_filename = filename.replace(".txt", " (1).txt")

    ensure_thread_dirs(thread_id, user_id)
    source_path = sandbox_user_data_dir(thread_id) / "outputs" / filename
    source_path.write_text("first\n", encoding="utf-8")

    first_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": f"/home/gem/user-data/outputs/{filename}"},
        headers=headers,
    )
    assert first_response.status_code == 200, first_response.text

    source_path.write_text("second\n", encoding="utf-8")
    second_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": f"/home/gem/user-data/outputs/{filename}"},
        headers=headers,
    )
    assert second_response.status_code == 200, second_response.text

    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["saved_path"] == f"/home/gem/user-data/workspace/saved_artifacts/{filename}"
    assert second_payload["saved_path"] == f"/home/gem/user-data/workspace/saved_artifacts/{renamed_filename}"

    first_saved = sandbox_workspace_dir(thread_id, user_id) / "saved_artifacts" / filename
    second_saved = sandbox_workspace_dir(thread_id, user_id) / "saved_artifacts" / renamed_filename
    assert first_saved.read_text(encoding="utf-8") == "first\n"
    assert second_saved.read_text(encoding="utf-8") == "second\n"


async def test_save_thread_artifact_to_workspace_rejects_invalid_paths(test_client, standard_user):
    headers = standard_user["headers"]
    user_id = str(standard_user["user"]["id"])
    thread_id = await _create_thread_for_user(test_client, headers)

    invalid_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": "/home/gem/user-data/not-allowed/demo.txt"},
        headers=headers,
    )
    assert invalid_response.status_code == 404, invalid_response.text

    ensure_thread_dirs(thread_id, user_id)
    directory_path = sandbox_workspace_dir(thread_id, user_id) / "nested-dir"
    directory_path.mkdir(parents=True, exist_ok=True)
    directory_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": "/home/gem/user-data/workspace/nested-dir"},
        headers=headers,
    )
    assert directory_response.status_code == 400, directory_response.text


async def test_docx_attachment_is_materialized_into_state_files_and_readable(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    docx_path = Path(__file__).resolve().parents[2] / "data" / "测试文档.docx"

    with docx_path.open("rb") as handle:
        upload_response = await test_client.post(
            f"/api/chat/thread/{thread_id}/attachments",
            files={
                "file": (
                    docx_path.name,
                    handle,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=headers,
        )
    assert upload_response.status_code == 200, upload_response.text

    attachment = upload_response.json()
    assert attachment["file_name"] == docx_path.name
    assert attachment["status"] == "parsed"
    assert attachment["path"].endswith(".md")

    state_response = await test_client.get(f"/api/chat/thread/{thread_id}/state", headers=headers)
    assert state_response.status_code == 200, state_response.text
    agent_state = state_response.json()["agent_state"]
    files = agent_state["files"]
    assert attachment["path"] in files, agent_state
    assert files[attachment["path"]]["content"], files[attachment["path"]]

    content_response = await test_client.get(
        f"/api/chat/thread/{thread_id}/files/content",
        params={"path": attachment["path"]},
        headers=headers,
    )
    assert content_response.status_code == 200, content_response.text
    payload = content_response.json()
    assert payload["path"] == attachment["path"]
    assert payload["content"]
