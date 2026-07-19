import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_graph_build_status_and_failed_chunk_samples(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    status_response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/graph-build/status",
        headers=admin_headers,
    )
    samples_response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/graph-build/failed-chunks?limit=10",
        headers=admin_headers,
    )

    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["extraction_counts"] == {
        "pending": 0,
        "succeeded": 0,
        "failed": 0,
    }
    assert samples_response.status_code == 200, samples_response.text
    assert samples_response.json() == {"kb_id": kb_id, "samples": []}
