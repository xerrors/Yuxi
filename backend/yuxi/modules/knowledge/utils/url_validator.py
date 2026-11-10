"""基于域名白名单的 URL 解析校验。"""

from urllib.parse import urlparse


def validate_url(url: str, whitelist: list[str]) -> tuple[bool, str]:
    """校验 URL 是否命中白名单，返回 (是否有效, 失败原因)。"""
    if not url:
        return False, "URL 不能为空"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL 格式不正确"

    if not parsed.scheme:
        return False, "URL 必须以 http:// 或 https:// 开头"
    if parsed.scheme not in ("http", "https"):
        return False, "URL 必须使用 HTTP 或 HTTPS 协议"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL 缺少有效域名"

    if not whitelist:
        return False, "URL 导入未启用，请联系管理员配置域名白名单"

    for allowed in whitelist:
        allowed = allowed.strip().lower()
        if not allowed:
            continue
        # 通配符 *.example.com 允许域本身及其子域；精确项同样放行子域。
        domain = allowed[2:] if allowed.startswith("*.") else allowed
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True, ""

    return False, "网址域名不在 URL 导入白名单中，请联系管理员配置"
