"""独立环境中的文档限制 HTTP 与配置持久化验收。"""

import asyncio
import os
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_limits_permissions_validation_concurrency_and_upload(test_client, admin_headers, standard_user):
    """真实 HTTP 验证保存、并发、上传限制和通用配置旁路。"""
    if os.getenv("TEST_ALLOW_PLATFORM_SETTINGS_MUTATION") != "1":
        pytest.skip("Requires isolated settings environment")
    url = "/api/system/document-limits"
    initial = await test_client.get(url, headers=admin_headers)
    assert initial.status_code == 200, initial.text
    original = initial.json()
    try:
        assert (await test_client.get(url)).status_code == 401
        assert (await test_client.get(url, headers=standard_user["headers"])).status_code == 200
        payload = {"revision": original["revision"], "upload_max_mib": 1, "ocr_max_pages": 2}
        denied = await test_client.put(url, headers=standard_user["headers"], json=payload)
        assert denied.status_code == 403
        for key, value in (
            ("upload_max_mib", 0),
            ("upload_max_mib", 1.5),
            ("upload_max_mib", True),
            ("ocr_max_pages", -1),
            ("upload_max_mib", original["hard_upload_max_mib"] + 1),
        ):
            result = await test_client.put(url, headers=admin_headers, json={**payload, key: value})
            assert result.status_code == 422, result.text
        for key in ("document_limits",):
            result = await test_client.put(
                f"/api/system/config/options/{key}", headers=admin_headers, json={"value": {"revision": 100}}
            )
            assert result.status_code == 403
        results = await asyncio.gather(*(test_client.put(url, headers=admin_headers, json=payload) for _ in range(2)))
        assert sorted(r.status_code for r in results) == [200, 409]
        winner = next(r.json() for r in results if r.status_code == 200)
        assert winner["effective_upload_max_bytes"] == 1024 * 1024
        assert (await test_client.get(url, headers=standard_user["headers"])).json()["ocr_max_pages"] == 2
        rejected = await test_client.post(
            "/api/knowledge/files/upload",
            headers=admin_headers,
            files={"file": ("over-limit.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
        )
        assert rejected.status_code in (400, 413), rejected.text
        assert "当前上限" in rejected.text
        reset = await test_client.request("DELETE", url, headers=admin_headers, json={"revision": winner["revision"]})
        assert reset.status_code == 200, reset.text
        assert reset.json()["upload_max_mib"] == original["defaults"]["upload_max_mib"]
    finally:
        current = await test_client.get(url, headers=admin_headers)
        if current.status_code == 200:
            restored = await test_client.put(
                url,
                headers=admin_headers,
                json={
                    "revision": current.json()["revision"],
                    "upload_max_mib": original["upload_max_mib"],
                    "ocr_max_pages": original["ocr_max_pages"],
                },
            )
            assert restored.status_code == 200, restored.text
