"""带 SSRF 防护的 URL 抓取：域名白名单、DNS 解析钉扎与大小/类型限制。"""

import asyncio
import ipaddress
import socket
import typing
from urllib.parse import urljoin

import httpcore
import httpx
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.options import knowledge_url_import_policy
from yuxi.modules.knowledge.utils.url_validator import validate_url
from yuxi.shared.errors import InvalidOperationError

# 最大允许下载大小 (例如 10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024
# 允许的 Content-Type
ALLOWED_CONTENT_TYPES = ["text/html", "application/xhtml+xml"]

# DNS rebinding 防护：解析、校验与连接必须使用同一次解析结果。
# is_global 已排除 loopback/私网/链路本地等地址，但当前解释器上组播仍可能
# is_global。云元数据服务所在的特殊网段在此显式补充，不依赖 ipaddress 版本行为。
EXTRA_BLOCKED_ADDRESSES = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "100.100.200.200",  # Alibaba Cloud metadata (CGNAT 段)
        "fd00:ec2::254",  # AWS IPv6 metadata
    )
)

ResolvedAddresses = list[ipaddress.IPv4Address | ipaddress.IPv6Address]


async def fetch_url_content(url: str, max_size: int = MAX_DOWNLOAD_SIZE) -> tuple[bytes, str]:
    """抓取 URL 内容，校验白名单、Content-Type 和大小上限，返回 (内容, 最终 URL)。"""
    policy = await knowledge_url_import_policy.get()
    whitelist = policy["allowed_hosts"]
    if not whitelist:
        raise InvalidOperationError("URL 导入未启用，请联系管理员配置域名白名单", code="url_fetch_rejected")

    is_valid, error_msg = validate_url(url, whitelist)
    if not is_valid:
        raise InvalidOperationError(f"URL 校验失败：{error_msg}", code="url_fetch_rejected")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    current_url = url
    redirect_count = 0
    max_redirects = 5

    # 手动跟随重定向，让每一跳（含重定向目标和 IP 字面量）都重新过白名单；
    # 私网拦截由 SSRFGuardBackend 在每次连接时按同一次解析结果执行。
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=SSRFGuardTransport()) as client:
            while True:
                logger.info(f"Fetching URL: {current_url}")

                # 先流式读取响应头，再决定重定向、拒绝或下载。
                async with client.stream("GET", current_url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if redirect_count >= max_redirects:
                            raise InvalidOperationError("网页重定向次数过多，最多允许 5 次", code="url_fetch_rejected")

                        redirect_count += 1
                        location = response.headers.get("Location")
                        if not location:
                            raise InvalidOperationError("网页重定向缺少目标地址", code="url_fetch_rejected")

                        current_url = urljoin(current_url, location)
                        is_valid, error_msg = validate_url(current_url, whitelist)
                        if not is_valid:
                            raise InvalidOperationError(f"重定向 URL 校验失败：{error_msg}", code="url_fetch_rejected")
                        continue

                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "").lower()
                    if not any(allowed in content_type for allowed in ALLOWED_CONTENT_TYPES):
                        raise InvalidOperationError("仅支持导入 HTML 网页，请确认网址指向网页正文", code="url_fetch_rejected")

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_size:
                            raise InvalidOperationError(f"网页内容超过 {max_size} 字节的导入上限", code="url_fetch_rejected")

                    return bytes(content), current_url

    except InvalidOperationError:
        raise
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching {url}: {e}")
        raise InvalidOperationError("网页抓取失败，请确认网址可访问后重试", code="url_fetch_failed") from e
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        raise InvalidOperationError("网页抓取失败，请稍后重试", code="url_fetch_failed") from e


# ==== 地址解析与私网拦截 ====


async def resolve_hostname_addresses(hostname: str) -> ResolvedAddresses:
    """解析主机名为去重 IP 列表，解析失败按不安全处理。"""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except Exception as e:
        logger.exception("URL DNS 解析失败")
        raise ValueError(f"DNS resolution failed for {hostname}") from e

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
    """非公网单播（含组播与已知云元数据段）返回 True。"""
    # 按实际建连的 IPv4 地址校验映射地址，避免不同 Python 版本对 IPv6 属性的判定差异。
    effective_address = getattr(address, "ipv4_mapped", None) or address
    return effective_address.is_multicast or not effective_address.is_global or effective_address in EXTRA_BLOCKED_ADDRESSES


def assert_no_blocked_address(addresses: ResolvedAddresses) -> None:
    """任一解析地址落在封锁段即拒绝连接。"""
    blocked = [str(address) for address in addresses if is_blocked_address(address)]
    if blocked:
        raise ValueError(f"Access to private IP addresses is forbidden: {', '.join(blocked)}")


# ==== httpcore/httpx 的 SSRF 防护接线 ====


class SSRFGuardBackend(httpcore.AsyncNetworkBackend):
    """只连接预校验公网 IP 的网络 backend。

    解析在 connect_tcp 内只发生一次，且连接复用同一次解析出的 IP，
    关闭 DNS rebinding 窗口；TLS SNI 与证书校验仍用原主机名，
    因为 httpcore 从请求 extensions 取 server_hostname，而非 socket 地址。
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
    """连接池使用 SSRFGuardBackend 的 httpx transport。

    httpx 0.28 不向 AsyncConnectionPool 暴露 network_backend 参数，这里在
    连接懒加载开始前替换 pool 的 backend 字段。
    """

    def __init__(self, backend: SSRFGuardBackend | None = None, **kwargs):
        super().__init__(**kwargs)
        self._pool._network_backend = backend or SSRFGuardBackend()
