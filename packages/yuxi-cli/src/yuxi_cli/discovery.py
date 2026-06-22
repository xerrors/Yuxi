from __future__ import annotations

from packaging.version import InvalidVersion, Version

MIN_SERVER_VERSION = "0.7.1"


class ServerCompatibilityError(Exception):
    pass


def is_server_version_supported(version: str, minimum: str = MIN_SERVER_VERSION) -> bool:
    try:
        parsed = Version(version)
        required = Version(minimum)
    except InvalidVersion:
        return False

    if parsed >= required:
        return True

    if parsed.is_devrelease:
        return parsed.release >= required.release

    return False


def ensure_server_compatible(discovery: dict, required_capability: str) -> None:
    version = str(discovery.get("version") or "")
    if not is_server_version_supported(version):
        raise ServerCompatibilityError(f"当前 Yuxi 服务版本 {version or 'unknown'} 低于 CLI 要求 {MIN_SERVER_VERSION}")

    if not _capability_enabled(discovery, required_capability):
        raise ServerCompatibilityError(f"当前 Yuxi 服务未声明支持 {required_capability}")


def _capability_enabled(discovery: dict, capability: str) -> bool:
    current = discovery.get("capabilities")
    for part in capability.split("."):
        if not isinstance(current, dict):
            return False
        current = current.get(part)
    return current is True
