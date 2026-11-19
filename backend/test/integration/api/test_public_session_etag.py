"""公开会话页按认证作用域和实际分页正文进行 HTTP 条件读取。"""

from uuid import uuid4

import pytest

from test.integration.api.helpers import delete_created_threads

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_session_page_revalidates_body_and_reflects_updates(test_client, standard_user, admin_headers):
    """逐次校验正文、缓存响应头、不同分页、匿名访问和外部改名。"""
    sessions = "/api/v1/agents/sessions"
    pinned_page = f"{sessions}?limit=1&is_pinned=true"
    headers = standard_user["headers"]
    session_id = None
    try:
        created = await test_client.post(
            sessions,
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"agent_id": "default-chatbot", "title": "条件读取测试", "yuxi": {}},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        # 初次置顶使此会话独占该临时账号的置顶首页。
        detail_url = f"{sessions}/{session_id}"
        pinned = await test_client.post(detail_url, headers=headers, json={"yuxi": {"is_pinned": True}})
        assert pinned.status_code == 200, pinned.text
        initial = await test_client.get(pinned_page, headers=headers)
        assert initial.status_code == 200
        assert initial.json()["data"][0]["id"] == session_id
        etag = initial.headers["etag"]
        assert initial.headers["cache-control"] == "private, no-cache"
        assert {"authorization", "x-end-user-id"}.issubset({header.strip().lower() for header in initial.headers["vary"].split(",")})

        validators = f'"unrelated,tag", {etag.removeprefix("W/")}'
        conditional = {**headers, "If-None-Match": validators}
        stable = await test_client.get(pinned_page, headers=conditional)
        assert (stable.status_code, stable.content, stable.headers["etag"]) == (304, b"", etag)
        assert stable.headers["cache-control"] == initial.headers["cache-control"]
        assert stable.headers["vary"] == initial.headers["vary"]
        excluded = await test_client.get(f"{sessions}?limit=1&is_pinned=false", headers=conditional)
        assert excluded.status_code == 200
        assert all(row["id"] != session_id for row in excluded.json()["data"])
        assert (await test_client.get(pinned_page, headers={"If-None-Match": "*"})).status_code == 401
        foreign = await test_client.get(pinned_page, headers={**admin_headers, "If-None-Match": etag})
        assert foreign.status_code == 200
        assert all(row["id"] != session_id for row in foreign.json()["data"])

        changed = await test_client.post(detail_url, headers=headers, json={"yuxi": {"title": "条件读取更新"}})
        assert changed.status_code == 200, changed.text
        newest = await test_client.get(pinned_page, headers=conditional)
        assert newest.status_code == 200 and newest.headers["etag"] != etag
        assert newest.json()["data"][0]["yuxi"]["title"] == "条件读取更新"
        current = await test_client.get(pinned_page, headers={**headers, "If-None-Match": newest.headers["etag"]})
        assert (current.status_code, current.content) == (304, b"")
    finally:
        await delete_created_threads(session_id)
