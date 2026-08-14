"""
Integration tests for settings router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_reranker_endpoint_is_offline(test_client, standard_user, admin_headers):
    """Reranker listing endpoint has been removed; all roles get 404."""
    for headers in (None, standard_user["headers"], admin_headers):
        response = await test_client.get("/api/settings/rerankers", headers=headers)
        assert response.status_code == 404, response.text
