"""Integration tests for viewer-oriented filesystem router endpoints."""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _get_provider():
    from yuxi.agents.backends import get_sandbox_provider

    return get_sandbox_provider()


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/chat/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    if not agents:
        pytest.skip("No agents available for viewer filesystem integration tests.")

    agent_id = agents[0].get("id")
    if not agent_id:
        pytest.skip("Agent payload missing id field.")

    create_resp = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": f"viewer-filesystem-test-{uuid.uuid4().hex[:8]}",
            "metadata": {},
        },
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    payload = create_resp.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, f"Create thread response missing thread identifier: {payload}"
    return thread_id


async def _write_workspace_file(thread_id: str, file_path: str, content: str) -> None:
    provider = _get_provider()
    sandbox = await asyncio.to_thread(provider.acquire, thread_id)
    try:
        command = (
            "python3 -c "
            f"\"from pathlib import Path; Path('{file_path}').parent.mkdir(parents=True, exist_ok=True); "
            f"Path('{file_path}').write_text({content!r}, encoding='utf-8')\""
        )
        result = await asyncio.to_thread(sandbox.execute, command)
        assert result.exit_code == 0, result.output
    finally:
        await asyncio.to_thread(provider.destroy, thread_id)


async def test_viewer_tree_requires_authentication(test_client):
    response = await test_client.get("/api/viewer/filesystem/tree", params={"thread_id": "x", "path": "/"})
    assert response.status_code == 401


async def test_viewer_tree_root_lists_user_data_namespace(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.get(
        "/api/viewer/filesystem/tree",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/mnt/user-data/" in paths


async def test_viewer_file_returns_raw_content_without_line_numbers(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    file_path = "/mnt/user-data/workspace/viewer_demo.txt"
    await _write_workspace_file(thread_id, file_path, "alpha\\nbeta\\n")

    response = await test_client.get(
        "/api/viewer/filesystem/file",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "alpha\nbeta\n"


async def test_viewer_download_returns_attachment_response(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)
    file_path = "/mnt/user-data/workspace/download_demo.txt"
    await _write_workspace_file(thread_id, file_path, "download-me\\n")

    response = await test_client.get(
        "/api/viewer/filesystem/download",
        params={"thread_id": thread_id, "path": file_path},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment;" in content_disposition
    assert "download_demo.txt" in content_disposition
    assert response.text == "download-me\n"
