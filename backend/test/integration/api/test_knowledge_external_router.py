"""
Integration tests for the knowledge external API exposed to the CLI / external agents.

External routes live under `/api/knowledge/databases/external/...` and reuse the
existing knowledge_base service. These tests cover the main paths, parameter
errors and the per-user access boundary.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_restricted_database(test_client, admin_headers):
    """Create a KB shared only with its creator (admin), so non-owners get 404."""
    profile_response = await test_client.get("/api/auth/me", headers=admin_headers)
    assert profile_response.status_code == 200, profile_response.text
    owner_uid = profile_response.json()["uid"]

    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_external_{uuid.uuid4().hex[:8]}",
            "description": "external API test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [owner_uid],
                },
                "manage_scope": None,
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _delete_database(test_client, admin_headers, kb_id):
    response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert response.status_code in (200, 404), response.text


async def test_external_list_requires_auth(test_client):
    response = await test_client.get("/api/knowledge/databases/external")
    assert response.status_code == 401


async def test_external_list_returns_user_databases(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get("/api/knowledge/databases/external", headers=admin_headers)
    assert response.status_code == 200, response.text
    databases = response.json().get("databases", [])
    matching = [db for db in databases if db.get("kb_id") == kb_id]
    assert matching, "created knowledge base should be visible to its owner"
    assert "supports_documents" in matching[0]
    assert "kb_type" in matching[0]


async def test_external_files_lists_and_searches(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    list_response = await test_client.get(
        f"/api/knowledge/databases/external/{kb_id}/files",
        headers=admin_headers,
    )
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()
    assert isinstance(payload.get("files"), list)

    search_response = await test_client.get(
        f"/api/knowledge/databases/external/{kb_id}/files",
        params={"query": "nonexistent-needle-xyz", "offset": 0, "limit": 50},
        headers=admin_headers,
    )
    assert search_response.status_code == 200, search_response.text
    assert search_response.json()["total"] == 0


async def test_external_files_unknown_kb_returns_404(test_client, admin_headers):
    response = await test_client.get(
        "/api/knowledge/databases/external/kb_does_not_exist/files",
        headers=admin_headers,
    )
    assert response.status_code == 404


async def test_external_open_unknown_file_returns_400(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(
        f"/api/knowledge/databases/external/{kb_id}/files/file_does_not_exist/open",
        headers=admin_headers,
    )
    assert response.status_code == 400


@pytest.mark.parametrize("patterns", [[], ["keyword"]])
async def test_external_find_rejects_bad_patterns(test_client, admin_headers, knowledge_database, patterns):
    kb_id = knowledge_database["kb_id"]
    response = await test_client.post(
        f"/api/knowledge/databases/external/{kb_id}/files/file_does_not_exist/find",
        json={"patterns": patterns},
        headers=admin_headers,
    )
    assert response.status_code == 400


async def test_external_retrieve_returns_structured_response(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    response = await test_client.post(
        f"/api/knowledge/databases/external/{kb_id}/retrieve",
        json={"query": "hello", "file_name": None, "options": {}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kb_id"] == kb_id
    assert isinstance(payload["results"], list)


async def test_external_parse_and_index_routes_are_not_exposed(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    for path in (
        f"/api/knowledge/databases/external/{kb_id}/parse",
        f"/api/knowledge/databases/external/{kb_id}/parse-pending",
        f"/api/knowledge/databases/external/{kb_id}/index",
        f"/api/knowledge/databases/external/{kb_id}/index-pending",
    ):
        response = await test_client.post(path, json={}, headers=admin_headers)
        assert response.status_code in (404, 405), path


async def test_external_access_is_restricted_to_owner(test_client, admin_headers, standard_user):
    database = await _create_restricted_database(test_client, admin_headers)
    kb_id = database["kb_id"]
    try:
        owner_response = await test_client.get(
            "/api/knowledge/databases/external",
            headers=admin_headers,
        )
        assert owner_response.status_code == 200
        assert any(db.get("kb_id") == kb_id for db in owner_response.json()["databases"])

        other_response = await test_client.get(
            "/api/knowledge/databases/external",
            headers=standard_user["headers"],
        )
        assert other_response.status_code == 200
        assert all(db.get("kb_id") != kb_id for db in other_response.json()["databases"])

        forbidden = await test_client.get(
            f"/api/knowledge/databases/external/{kb_id}/files",
            headers=standard_user["headers"],
        )
        assert forbidden.status_code == 404
    finally:
        await _delete_database(test_client, admin_headers, kb_id)
