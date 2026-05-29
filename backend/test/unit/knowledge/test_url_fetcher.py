from __future__ import annotations

import pytest

from yuxi.knowledge.utils import url_fetcher

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_forbidden_ip_allows_private_network_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_fetcher.socket,
        "getaddrinfo",
        lambda hostname, port: [(None, None, None, None, ("172.27.86.91", 0))],
    )

    assert await url_fetcher.is_forbidden_ip("172.27.86.91") is False


@pytest.mark.parametrize("ip_addr", ["127.0.0.1", "169.254.169.254", "::1", "fe80::1", "fe80::1%lo0"])
async def test_forbidden_ip_blocks_loopback_and_link_local(
    monkeypatch: pytest.MonkeyPatch, ip_addr: str
) -> None:
    monkeypatch.setattr(
        url_fetcher.socket,
        "getaddrinfo",
        lambda hostname, port: [(None, None, None, None, (ip_addr, 0))],
    )

    assert await url_fetcher.is_forbidden_ip(ip_addr) is True


def test_whitelist_allows_private_ip_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YUXI_URL_WHITELIST", "alidocs.dingtalk.com,172.27.86.91")

    is_valid, error_msg = url_fetcher.validate_url("http://172.27.86.91:8090/api/pages/34")

    assert is_valid is True
    assert error_msg == ""
