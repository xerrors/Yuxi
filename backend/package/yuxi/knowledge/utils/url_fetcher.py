import asyncio
import ipaddress
import socket
import typing
from urllib.parse import urljoin

import httpcore
import httpx
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream

from yuxi.knowledge.utils.url_validator import is_url_parsing_enabled, validate_url
from yuxi.utils import logger

# 最大允许下载大小 (例如 10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024
# 允许的 Content-Type
ALLOWED_CONTENT_TYPES = ["text/html", "application/xhtml+xml"]

# DNS rebinding 防护：解析、校验与连接必须使用同一次解析结果。
# is_global 已排除 loopback/私网/链路本地等地址；云元数据服务所在的
# 特殊网段在此显式补充，不依赖 ipaddress 版本行为。
EXTRA_BLOCKED_ADDRESSES = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "100.100.200.200",  # Alibaba Cloud metadata (CGNAT 段)
        "fd00:ec2::254",  # AWS IPv6 metadata
    )
)

ResolvedAddresses = list[ipaddress.IPv4Address | ipaddress.IPv6Address]


async def resolve_hostname_addresses(hostname: str) -> ResolvedAddresses:
    """Resolve a hostname to deduped IP addresses, failing closed on errors."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except Exception as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e

    addresses: ResolvedAddresses = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise ValueError(f"DNS resolution returned no usable address for {hostname}")
    return addresses


def is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any non-public address, including known cloud metadata ranges."""
    return not address.is_global or address in EXTRA_BLOCKED_ADDRESSES


def assert_no_blocked_address(addresses: ResolvedAddresses) -> None:
    blocked = [str(address) for address in addresses if is_blocked_address(address)]
    if blocked:
        raise ValueError(f"Access to private IP addresses is forbidden: {', '.join(blocked)}")


class SSRFGuardBackend(httpcore.AsyncNetworkBackend):
    """Network backend that connects only to pre-validated public IPs.

    Resolution happens once inside connect_tcp; the same resolved IP is used
    for the connection, closing the DNS rebinding window. TLS SNI and
    certificate verification keep using the original hostname because
    httpcore takes the server_hostname from request extensions, not from the
    socket address.
    """

    def __init__(self, default_backend: httpcore.AsyncNetworkBackend | None = None):
        # httpcore 的默认 backend 未在公共 API 暴露，AutoBackend 是
        # AsyncConnectionPool 缺省使用的实现。
        self._default = default_backend or _create_default_backend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        addresses = await resolve_hostname_addresses(host)
        assert_no_blocked_address(addresses)

        # 所有解析地址都已完成安全校验，按统一超时预算依次尝试，
        # 保留多地址（IPv6/IPv4、多 A 记录）的可用性回退。
        per_attempt_timeout = timeout / len(addresses) if timeout is not None else None
        last_error: OSError | None = None
        for address in addresses:
            try:
                return await self._default.connect_tcp(
                    str(address),
                    port,
                    timeout=per_attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as e:
                last_error = e

        assert last_error is not None
        raise last_error


def _create_default_backend() -> httpcore.AsyncNetworkBackend:
    from httpcore._backends.auto import AutoBackend

    return AutoBackend()


class SSRFGuardTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose connection pool uses SSRFGuardBackend.

    httpx 0.28 不向 AsyncConnectionPool 暴露 network_backend 参数，这里在
    连接懒加载开始前替换 pool 的 backend 字段。
    """

    def __init__(self, backend: SSRFGuardBackend | None = None, **kwargs):
        super().__init__(**kwargs)
        self._pool._network_backend = backend or SSRFGuardBackend()


async def fetch_url_content(url: str, max_size: int = MAX_DOWNLOAD_SIZE) -> tuple[bytes, str]:
    """
    Fetch URL content with security checks (size limit, content type, private IP blocking).

    Args:
        url: The URL to fetch.
        max_size: Maximum allowed size in bytes.

    Returns:
        tuple: (content_bytes, final_url)

    Raises:
        ValueError: If validation fails or download error occurs.
    """
    if not is_url_parsing_enabled():
        raise ValueError("URL parsing feature is disabled")

    # Initial validation
    is_valid, error_msg = validate_url(url)
    if not is_valid:
        raise ValueError(f"Invalid URL: {error_msg}")

    current_url = url
    redirect_count = 0
    max_redirects = 5

    # We handle redirects manually to check each target URL against the
    # whitelist; private-IP blocking is enforced per connection by
    # SSRFGuardBackend, so every hop (including redirect targets and IP
    # literals) is resolved once, validated, and connected to the same IP.
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=SSRFGuardTransport()) as client:
            while True:
                logger.info(f"Fetching URL: {current_url}")

                # Request headers
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/91.0.4472.124 "
                        "Safari/537.36"
                    )
                }

                # Stream the response to check headers before downloading body
                async with client.stream("GET", current_url, headers=headers) as response:
                    # Handle Redirects
                    if response.status_code in (301, 302, 303, 307, 308):
                        if redirect_count >= max_redirects:
                            raise ValueError("Too many redirects")

                        redirect_count += 1
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("Redirect response missing Location header")

                        current_url = urljoin(current_url, location)

                        # Validate the new URL
                        is_valid, error_msg = validate_url(current_url)
                        if not is_valid:
                            raise ValueError(f"Redirected to invalid URL: {error_msg}")

                        continue  # Start new request

                    response.raise_for_status()

                    # Check Content-Type
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not any(allowed in content_type for allowed in ALLOWED_CONTENT_TYPES):
                        raise ValueError(f"Unsupported Content-Type: {content_type}. Only HTML is supported.")

                    # Download content with size limit
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_size:
                            raise ValueError(f"Content size exceeds limit of {max_size} bytes")

                    return bytes(content), current_url

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching {url}: {e}")
        raise ValueError(f"Failed to fetch URL: {e}")
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        raise ValueError(f"Error fetching URL: {str(e)}")
