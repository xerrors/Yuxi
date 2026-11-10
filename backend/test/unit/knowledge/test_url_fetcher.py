"""SSRF 防护（DNS rebinding）相关测试。"""

import ipaddress
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from yuxi.modules.knowledge.utils import url_fetcher
from yuxi.modules.knowledge.utils.url_fetcher import (
    SSRFGuardBackend,
    assert_no_blocked_address,
    is_blocked_address,
    resolve_hostname_addresses,
)
from yuxi.modules.knowledge.utils.url_validator import validate_url
from yuxi.shared.errors import InvalidOperationError


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
        "224.0.0.1",
        "ff02::1",
        "::ffff:224.0.0.1",
        "::ffff:10.0.0.5",
        "::ffff:100.100.200.200",
        "::ffff:169.254.169.254",
    ],
)
def test_is_blocked_address_rejects_non_public(address):
    assert is_blocked_address(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:8.8.8.8"],
)
def test_is_blocked_address_allows_public(address):
    assert is_blocked_address(ipaddress.ip_address(address)) is False


def test_assert_no_blocked_address_reports_offender():
    with pytest.raises(ValueError, match="10.0.0.5"):
        assert_no_blocked_address([ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.0.0.5")])


async def test_mapped_metadata_rejected_even_if_ipv6_address_claims_global(monkeypatch, recording_backend):
    """模拟 Python 3.12 的映射地址判断，元数据地址仍不能建连。"""
    monkeypatch.setattr(ipaddress.IPv6Address, "is_global", property(lambda self: True))
    _patch_getaddrinfo(monkeypatch, _addr_infos("::ffff:100.100.200.200"))
    guard = SSRFGuardBackend(recording_backend)

    with pytest.raises(ValueError, match="private IP"):
        await guard.connect_tcp("example.com", 443)
    assert recording_backend.calls == []


@pytest.mark.parametrize(
    ("resolution", "fail_hosts", "timeout", "expected_error", "expected_calls"),
    [
        pytest.param(_addr_infos("93.184.216.34"), set(), None, None, [("93.184.216.34", 443, None)], id="validated-address"),
        pytest.param(_addr_infos("10.0.0.5"), set(), None, (ValueError, "private IP"), [], id="private-address"),
        pytest.param(socket.gaierror("boom"), set(), None, (ValueError, "DNS resolution failed"), [], id="dns-error"),
        pytest.param(_addr_infos("169.254.169.254"), set(), None, (ValueError, "private IP"), [], id="metadata-address"),
        pytest.param(_addr_infos("224.0.0.1"), set(), None, (ValueError, "private IP"), [], id="multicast-address"),
        pytest.param(_addr_infos("ff02::1"), set(), None, (ValueError, "private IP"), [], id="ipv6-multicast-address"),
        pytest.param(_addr_infos("::ffff:224.0.0.1"), set(), None, (ValueError, "private IP"), [], id="mapped-multicast-address"),
        pytest.param(
            _addr_infos("93.184.216.34", "8.8.8.8"),
            {"93.184.216.34"},
            None,
            None,
            [("93.184.216.34", 443, None), ("8.8.8.8", 443, None)],
            id="next-validated-address",
        ),
        pytest.param(
            _addr_infos("93.184.216.34"),
            {"93.184.216.34"},
            None,
            (ConnectionError, None),
            [("93.184.216.34", 443, None)],
            id="no-unvalidated-fallback",
        ),
        pytest.param(
            _addr_infos("93.184.216.34", "8.8.8.8"),
            {"93.184.216.34"},
            2.0,
            None,
            [("93.184.216.34", 443, 1.0), ("8.8.8.8", 443, 1.0)],
            id="split-timeout-budget",
        ),
        pytest.param(
            _addr_infos("8.8.4.4"),
            {"8.8.4.4"},
            None,
            (ConnectionError, None),
            [("8.8.4.4", 443, None)],
            id="single-address-unreachable",
        ),
    ],
)
async def test_backend_connects_only_to_validated_resolution(
    monkeypatch,
    resolution,
    fail_hosts,
    timeout,
    expected_error,
    expected_calls,
):
    """地址校验、连接回退和超时预算按同一建连协议验证。"""
    _patch_getaddrinfo(monkeypatch, resolution)
    recording = RecordingBackend(fail_hosts=fail_hosts)
    backend = SSRFGuardBackend(default_backend=recording)

    if expected_error:
        error_type, error_message = expected_error
        with pytest.raises(error_type, match=error_message):
            await backend.connect_tcp("example.com", 443, timeout=timeout)
    else:
        await backend.connect_tcp("example.com", 443, timeout=timeout)

    assert recording.calls == expected_calls


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


@pytest.mark.parametrize(
    ("status", "headers", "content", "message"),
    [
        (302, {"Location": "https://blocked.test/private"}, b"", "重定向 URL 校验失败：网址域名不在 URL 导入白名单中，请联系管理员配置"),
        (302, {}, b"", "网页重定向缺少目标地址"),
        (200, {"Content-Type": "application/json"}, b"{}", "仅支持导入 HTML 网页，请确认网址指向网页正文"),
        (200, {"Content-Type": "text/html"}, b"12345", "网页内容超过 4 字节的导入上限"),
    ],
)
async def test_fetch_rejections_keep_public_business_reason(monkeypatch, status, headers, content, message):
    """重定向及内容限制保留公开原因，不被网络失败兜底覆盖。"""
    monkeypatch.setattr(
        url_fetcher, "knowledge_url_import_policy", SimpleNamespace(get=AsyncMock(return_value={"allowed_hosts": ["example.test"]}))
    )
    requests = []

    def respond(request):
        """固定远端协议结果，记录是否越过被拒绝的重定向。"""
        requests.append(str(request.url))
        return httpx.Response(status, headers=headers, content=content)

    monkeypatch.setattr(url_fetcher, "SSRFGuardTransport", lambda: httpx.MockTransport(respond))
    with pytest.raises(InvalidOperationError) as error:
        await url_fetcher.fetch_url_content("https://example.test/page", max_size=4)
    assert error.value.code == "url_fetch_rejected" and error.value.message == message
    assert requests == ["https://example.test/page"]


@pytest.mark.parametrize(
    ("url", "hosts", "allowed"),
    [
        ("https://example.com/a", [" EXAMPLE.COM "], True),
        ("https://docs.example.com/a", ["example.com"], True),
        ("https://example.com/a", ["*.example.com"], True),
        ("https://docs.example.com/a", ["*.example.com"], True),
        ("https://example.com./a", ["example.com."], True),
        ("https://docs.example.com./a", ["*.example.com."], True),
        ("https://badexample.com/a", ["example.com"], False),
        ("https://example.com.attacker.test/a", ["example.com"], False),
        ("https://arbitrary.test./a", [" "], False),
        ("file:///etc/passwd", ["example.com"], False),
        ("https://example.com/a", [], False),
    ],
)
def test_validate_uses_saved_domain_policy(url, hosts, allowed):
    """列表策略允许域名与子域，同时拒绝相似域名及空白项绕过。"""
    assert validate_url(url, hosts)[0] is allowed
