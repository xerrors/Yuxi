import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from yuxi.knowledge.utils.url_validator import is_url_parsing_enabled, validate_url
from yuxi.utils import logger

# 最大允许下载大小 (例如 10MB)
MAX_DOWNLOAD_SIZE = 10 * 1024 * 1024
# 允许的 Content-Type
ALLOWED_CONTENT_TYPES = ["text/html", "application/xhtml+xml"]


async def is_forbidden_ip(hostname: str) -> bool:
    """Check if the hostname resolves to a forbidden IP address."""
    import asyncio

    try:
        # Resolve hostname to IP in a separate thread to avoid blocking the event loop
        ip_list = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        for item in ip_list:
            ip_addr = item[4][0]
            if "%" in ip_addr:
                ip_addr = ip_addr.split("%", 1)[0]
            ip_obj = ipaddress.ip_address(ip_addr)
            if ip_obj.is_loopback or ip_obj.is_link_local:
                return True
        return False
    except Exception as e:
        logger.warning(f"Failed to resolve hostname {hostname}: {e}")
        return True


async def fetch_url_content(url: str, max_size: int = MAX_DOWNLOAD_SIZE) -> tuple[bytes, str]:
    """
    Fetch URL content with security checks (size limit, content type, loopback/link-local blocking).

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

    # Parse URL to check IP
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname
    if hostname is None:
        raise ValueError("Invalid URL: no hostname found")
    if await is_forbidden_ip(hostname):
        raise ValueError("Access to loopback or link-local IP addresses is forbidden")

    current_url = url
    redirect_count = 0
    max_redirects = 5

    # We handle redirects manually to check each target URL against whitelist/private IP
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
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

                        # Handle relative redirects
                        if location.startswith("/"):
                            parsed_current = urlparse(current_url)
                            current_url = f"{parsed_current.scheme}://{parsed_current.netloc}{location}"
                        elif not location.startswith("http"):
                            # Handle relative path without /? or other weird cases, or assume absolute
                            from urllib.parse import urljoin

                            current_url = urljoin(current_url, location)
                        else:
                            current_url = location

                        # Validate the new URL
                        is_valid, error_msg = validate_url(current_url)
                        if not is_valid:
                            raise ValueError(f"Redirected to invalid URL: {error_msg}")

                        parsed_new = urlparse(current_url)
                        new_hostname = parsed_new.hostname
                        if new_hostname is None:
                            raise ValueError("Redirected to invalid URL: no hostname found")
                        if await is_forbidden_ip(new_hostname):
                            raise ValueError("Redirected to loopback or link-local IP address")

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
