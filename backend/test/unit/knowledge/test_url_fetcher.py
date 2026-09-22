"""SSRF 防护（DNS rebinding）相关测试。"""

import ipaddress
import socket

import httpcore
import httpx
import pytest
from yuxi.knowledge.utils import url_fetcher
from yuxi.knowledge.utils.url_fetcher import (
    SSRFGuardBackend,
    assert_no_blocked_address,
    is_blocked_address,
    resolve_hostname_addresses,
)


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """记录 connect_tcp 目标的假 backend，不发起真实连接。

    fail_hosts 中的地址会抛出 ConnectionError，用于模拟不可达。
    """

    def __init__(self, fail_hosts: set[str] | None = None):
        self.calls: list[tuple[str, int, float | None]] = []
        self.fail_hosts = fail_hosts or set()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port, timeout))
        if host in self.fail_hosts:
            raise ConnectionError(f"unreachable: {host}")
        return object()


def _addr_infos(*addresses: str) -> list:
    infos = []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        if ip.version == 4:
            sockaddr = (address, 0)
        else:
            sockaddr = (address, 0, 0, 0)
        infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return infos


@pytest.fixture
def recording_backend():
    return RecordingBackend()


def _patch_getaddrinfo(monkeypatch, result):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(url_fetcher.socket, "getaddrinfo", fake_getaddrinfo)


async def test_resolve_dedupes_addresses(monkeypatch):
    _patch_getaddrinfo(
        monkeypatch,
        _addr_infos("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"),
    )
    resolved = await resolve_hostname_addresses("example.com")
    assert resolved == [
        ipaddress.ip_address("93.184.216.34"),
        ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"),
    ]


async def test_resolve_fails_closed_on_dns_error(monkeypatch):
    _patch_getaddrinfo(monkeypatch, socket.gaierror("boom"))
    with pytest.raises(ValueError, match="DNS resolution failed"):
        await resolve_hostname_addresses("example.com")


async def test_resolve_fails_closed_on_empty_result(monkeypatch):
    _patch_getaddrinfo(monkeypatch, [])
    with pytest.raises(ValueError, match="no usable address"):
        await resolve_hostname_addresses("example.com")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.9",
        "192.168.1.1",
        "0.0.0.0",
        "169.254.169.254",
        "100.100.200.200",
        "fe80::1",
        "::1",
        "fc00::1",
        "fd00:ec2::254",
    ],
)
def test_is_blocked_address_rejects_non_public(address):
    assert is_blocked_address(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"])
def test_is_blocked_address_allows_public(address):
    assert is_blocked_address(ipaddress.ip_address(address)) is False


def test_assert_no_blocked_address_reports_offender():
    with pytest.raises(ValueError, match="10.0.0.5"):
        assert_no_blocked_address([ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.0.0.5")])


async def test_backend_connects_to_resolved_public_ip(monkeypatch, recording_backend):
    _patch_getaddrinfo(monkeypatch, _addr_infos("93.184.216.34"))
    backend = SSRFGuardBackend(default_backend=recording_backend)

    await backend.connect_tcp("example.com", 443)

    # 连接目标必须是校验过的 IP，而不是再解析一次 hostname
    assert recording_backend.calls == [("93.184.216.34", 443, None)]


async def test_backend_blocks_private_resolution_without_connecting(monkeypatch, recording_backend):
    _patch_getaddrinfo(monkeypatch, _addr_infos("10.0.0.5"))
    backend = SSRFGuardBackend(default_backend=recording_backend)

    with pytest.raises(ValueError, match="private IP"):
        await backend.connect_tcp("internal.example.com", 443)

    assert recording_backend.calls == []


async def test_backend_fails_closed_when_resolution_fails(monkeypatch, recording_backend):
    _patch_getaddrinfo(monkeypatch, socket.gaierror("boom"))
    backend = SSRFGuardBackend(default_backend=recording_backend)

    with pytest.raises(ValueError, match="DNS resolution failed"):
        await backend.connect_tcp("example.com", 443)

    assert recording_backend.calls == []


async def test_backend_blocks_metadata_address(monkeypatch, recording_backend):
    _patch_getaddrinfo(monkeypatch, _addr_infos("169.254.169.254"))
    backend = SSRFGuardBackend(default_backend=recording_backend)

    with pytest.raises(ValueError, match="private IP"):
        await backend.connect_tcp("metadata.example.com", 80)

    assert recording_backend.calls == []


async def test_backend_falls_back_to_next_validated_address(monkeypatch, recording_backend):
    """首个已校验地址不可达时，依次回退到下一个已校验地址。"""
    _patch_getaddrinfo(monkeypatch, _addr_infos("93.184.216.34", "8.8.8.8"))
    recording_backend.fail_hosts = {"93.184.216.34"}
    backend = SSRFGuardBackend(default_backend=recording_backend)

    await backend.connect_tcp("example.com", 443)

    assert [host for host, _, _ in recording_backend.calls] == ["93.184.216.34", "8.8.8.8"]


async def test_backend_never_falls_back_to_unvalidated_address(monkeypatch, recording_backend):
    """回退只能在已通过校验的地址集合内进行。"""
    _patch_getaddrinfo(monkeypatch, _addr_infos("93.184.216.34"))
    recording_backend.fail_hosts = {"93.184.216.34"}
    backend = SSRFGuardBackend(default_backend=recording_backend)

    with pytest.raises(ConnectionError):
        await backend.connect_tcp("example.com", 443)

    assert [host for host, _, _ in recording_backend.calls] == ["93.184.216.34"]


async def test_backend_splits_timeout_budget_across_addresses(monkeypatch):
    """超时预算按地址数量均分到每次尝试。"""
    _patch_getaddrinfo(monkeypatch, _addr_infos("93.184.216.34", "8.8.8.8"))
    recording = RecordingBackend(fail_hosts={"93.184.216.34"})
    backend = SSRFGuardBackend(default_backend=recording)

    await backend.connect_tcp("example.com", 443, timeout=2.0)

    assert recording.calls == [("93.184.216.34", 443, 1.0), ("8.8.8.8", 443, 1.0)]


async def test_backend_rejects_without_connecting_when_single_address_unreachable(monkeypatch, recording_backend):
    """单地址且不可达时抛出最后一次连接错误。"""
    _patch_getaddrinfo(monkeypatch, _addr_infos("8.8.4.4"))
    recording_backend.fail_hosts = {"8.8.4.4"}
    backend = SSRFGuardBackend(default_backend=recording_backend)

    with pytest.raises(ConnectionError):
        await backend.connect_tcp("example.com", 443)

    assert recording_backend.calls == [("8.8.4.4", 443, None)]


async def test_transport_wires_ssrf_backend_into_pool(monkeypatch):
    """接线测试：经 SSRFGuardTransport 的请求必须走到 SSRFGuardBackend.connect_tcp。"""
    _patch_getaddrinfo(monkeypatch, _addr_infos("93.184.216.34"))
    recording = RecordingBackend()
    transport = url_fetcher.SSRFGuardTransport(backend=SSRFGuardBackend(default_backend=recording))

    request = httpx.Request("GET", "https://example.com/")
    with pytest.raises(Exception):
        # 假 backend 返回的流不支持 HTTP 握手，请求必然失败；
        # 断言点在于建连必须先经过 SSRF 校验层
        await transport.handle_async_request(request)

    assert recording.calls, "SSRFGuardTransport 未接入 SSRFGuardBackend"
    assert recording.calls[0][0] == "93.184.216.34"
