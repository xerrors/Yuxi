"""URL transport fixtures: no external endpoint or SSRF policy changes."""

from unittest.mock import AsyncMock

import httpx
import pytest

from yuxi.knowledge.utils import url_fetcher


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [None, "2", "999999"])
@pytest.mark.parametrize("overflow", [False, True])
async def test_url_transfer_counts_actual_bytes(monkeypatch, declared, overflow):
    monkeypatch.setenv("YUXI_URL_WHITELIST", "public.example")
    monkeypatch.setattr(url_fetcher, "is_private_ip", AsyncMock(return_value=False))

    class Stream(httpx.AsyncByteStream):
        closed = False
        consumed = 0

        async def __aiter__(self):
            for chunk in [b"ab", b"cd" if overflow else b"c"]:
                self.consumed += 1
                yield chunk
            if overflow:
                pytest.fail("transfer continued after the accepted byte limit")

        async def aclose(self):
            self.closed = True

    stream = Stream()
    headers = {"Content-Type": "text/html"}
    if declared is not None:
        headers["Content-Length"] = declared
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        url_fetcher.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    if overflow:
        with pytest.raises(ValueError, match="limit of 3 bytes"):
            await url_fetcher.fetch_url_content("https://public.example/page", max_size=3)
    else:
        body, url = await url_fetcher.fetch_url_content("https://public.example/page", max_size=3)
        assert body == b"abc" and url == "https://public.example/page"
    assert stream.closed and stream.consumed == 2


@pytest.mark.asyncio
async def test_url_stays_disabled_without_whitelist(monkeypatch):
    monkeypatch.delenv("YUXI_URL_WHITELIST", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        await url_fetcher.fetch_url_content("http://127.0.0.1/private", max_size=3)


@pytest.mark.asyncio
async def test_whitelisted_private_address_remains_blocked(monkeypatch):
    monkeypatch.setenv("YUXI_URL_WHITELIST", "127.0.0.1")
    with pytest.raises(ValueError, match="private IP"):
        await url_fetcher.fetch_url_content("http://127.0.0.1/private", max_size=3)


@pytest.mark.asyncio
async def test_url_route_passes_server_snapshot_to_fetcher(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from server.routers import knowledge_router

    monkeypatch.setattr(knowledge_router, "_require_manage_permission_if_kb_id", AsyncMock())
    monkeypatch.setattr(
        knowledge_router,
        "snapshot_document_limits",
        AsyncMock(return_value={"max_file_bytes": 3, "max_ocr_pages": 1, "version": 0}),
    )
    fetch = AsyncMock(side_effect=ValueError("transport fixture stop"))
    monkeypatch.setattr(knowledge_router, "fetch_url_content", fetch)
    with pytest.raises(HTTPException) as error:
        await knowledge_router.fetch_url(
            url="https://public.example/page", kb_id=None, current_user=SimpleNamespace(uid=1)
        )
    assert error.value.status_code == 400
    fetch.assert_awaited_once_with("https://public.example/page", max_size=3)
