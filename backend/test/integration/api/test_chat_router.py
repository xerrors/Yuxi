"""
Integration tests for chat router endpoints.
"""

from __future__ import annotations

import base64
import io
import uuid

import pytest
from PIL import Image
from yuxi.agents.backends.sandbox import ensure_thread_dirs, sandbox_user_data_dir, sandbox_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_chat_endpoints_require_authentication(test_client):
    assert (await test_client.get("/api/chat/threads")).status_code == 401
    assert (await test_client.get("/api/agent")).status_code == 401


async def test_image_upload_composites_transparent_png_pixels_on_white(test_client, admin_headers):
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    image.putpixel((0, 0), (50, 87, 244, 0))
    image.putpixel((1, 0), (50, 87, 244, 255))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

    response = await test_client.post(
        "/api/chat/image/upload",
        headers=admin_headers,
        files={"file": ("transparent.png", image_bytes, "image/png")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["mime_type"] == "image/png"

    processed_data = base64.b64decode(payload["image_content"])
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        rgb_image = processed_image.convert("RGB")

    assert rgb_image.getpixel((0, 0)) == (255, 255, 255)
    assert rgb_image.getpixel((1, 0)) == (50, 87, 244)


async def test_thread_artifact_uses_image_signature_for_content_type(test_client, admin_headers):
    thread_id = await _create_thread_for_user(test_client, admin_headers)
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

    upload_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/attachments",
        headers=admin_headers,
        files={"file": ("mislabeled.jpg", image_bytes, "image/jpeg")},
    )

    assert upload_response.status_code == 200, upload_response.text
    attachment = upload_response.json()

    artifact_response = await test_client.get(attachment["original_artifact_url"], headers=admin_headers)

    assert artifact_response.status_code == 200, artifact_response.text
    assert artifact_response.headers["content-type"].startswith("image/png")
    assert artifact_response.content.startswith(b"\x89PNG\r\n\x1a\n")


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    if not agents:
        pytest.skip("No agents available for chat router integration tests.")

    agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    if not agent_id:
        pytest.skip("Agent payload missing slug field.")

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
    response = await test_client.get("/api/agent", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["agents"], list)
    if payload["agents"]:
        assert "agent_id" in payload["agents"][0]


async def test_admin_can_read_default_agent(test_client, admin_headers):
    response = await test_client.get("/api/agent/default", headers=admin_headers)
    assert response.status_code == 200, response.text
    agent = response.json()["agent"]
    assert agent["is_default"] is True
    assert agent["agent_id"]


async def test_agent_detail_filters_configurable_items_by_role(
    test_client,
    admin_headers,
    standard_user,
):
    agents_response = await test_client.get("/api/agent", headers=standard_user["headers"])
    assert agents_response.status_code == 200, agents_response.text
    agents = agents_response.json().get("agents", [])
    if not agents:
        pytest.skip("No agents are registered in the system.")

    agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    if not agent_id:
        pytest.skip("Agent payload missing slug field.")

    user_agent_response = await test_client.get(f"/api/agent/{agent_id}", headers=standard_user["headers"])
    assert user_agent_response.status_code == 200, user_agent_response.text
    user_items = user_agent_response.json()["agent"].get("configurable_items", {})
    assert "summary_threshold" not in user_items

    admin_agent_response = await test_client.get(f"/api/agent/{agent_id}", headers=admin_headers)
    assert admin_agent_response.status_code == 200, admin_agent_response.text
    admin_items = admin_agent_response.json()["agent"].get("configurable_items", {})
    assert "summary_threshold" in admin_items


async def test_setting_default_agent_requires_admin(test_client, admin_headers, standard_user):
    agents_response = await test_client.get("/api/agent", headers=admin_headers)
    assert agents_response.status_code == 200, agents_response.text
    agents = agents_response.json().get("agents", [])

    if not agents:
        pytest.skip("No agents are registered in the system.")

    candidate_agent_id = agents[0].get("agent_id") or agents[0].get("slug")
    if not candidate_agent_id:
        pytest.skip("Agent payload missing slug field.")

    forbidden_response = await test_client.post(
        f"/api/agent/{candidate_agent_id}/set_default",
        headers=standard_user["headers"],
    )
    assert forbidden_response.status_code == 403

    update_response = await test_client.post(
        f"/api/agent/{candidate_agent_id}/set_default",
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    agent = update_response.json()["agent"]
    assert agent["agent_id"] == candidate_agent_id
    assert agent["is_default"] is True


async def test_save_thread_artifact_to_workspace_copies_output_file(test_client, standard_user):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.md"

    ensure_thread_dirs(thread_id, uid)
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

    saved_path = sandbox_workspace_dir(thread_id, uid) / "saved_artifacts" / filename
    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == "# artifact\n"

    download_response = await test_client.get(payload["saved_artifact_url"], headers=headers)
    assert download_response.status_code == 200, download_response.text
    assert download_response.text == "# artifact\n"


async def test_save_thread_artifact_to_workspace_auto_renames_conflicts(test_client, standard_user):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await _create_thread_for_user(test_client, headers)
    filename = f"artifact-{uuid.uuid4().hex[:8]}.txt"
    renamed_filename = filename.replace(".txt", " (1).txt")

    ensure_thread_dirs(thread_id, uid)
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

    first_saved = sandbox_workspace_dir(thread_id, uid) / "saved_artifacts" / filename
    second_saved = sandbox_workspace_dir(thread_id, uid) / "saved_artifacts" / renamed_filename
    assert first_saved.read_text(encoding="utf-8") == "first\n"
    assert second_saved.read_text(encoding="utf-8") == "second\n"


async def test_save_thread_artifact_to_workspace_rejects_invalid_paths(test_client, standard_user):
    headers = standard_user["headers"]
    uid = str(standard_user["user"]["uid"])
    thread_id = await _create_thread_for_user(test_client, headers)

    invalid_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": "/home/gem/user-data/not-allowed/demo.txt"},
        headers=headers,
    )
    assert invalid_response.status_code == 404, invalid_response.text

    ensure_thread_dirs(thread_id, uid)
    directory_path = sandbox_workspace_dir(thread_id, uid) / "nested-dir"
    directory_path.mkdir(parents=True, exist_ok=True)
    directory_response = await test_client.post(
        f"/api/chat/thread/{thread_id}/artifacts/save",
        json={"path": "/home/gem/user-data/workspace/nested-dir"},
        headers=headers,
    )
    assert directory_response.status_code == 400, directory_response.text
