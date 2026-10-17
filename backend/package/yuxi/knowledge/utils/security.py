"""知识库配置的敏感信息处理工具。"""

from typing import Any

_SENSITIVE_PARAMETER_MARKERS = ("token", "secret", "password", "api_key", "authorization", "authentication", "cookie")
_NON_SECRET_TOKEN_PARAMETERS = {"max_tokens", "max_completion_tokens", "max_output_tokens", "chunk_token_num"}
_AUTHENTICATION_HEADER_PARAMETERS = {"default_headers", "extra_headers"}


def redact_sensitive_params(params: dict[str, Any]) -> dict[str, Any]:
    """复制知识库配置并移除各层凭据和可携带任意网关认证的请求头。"""
    redacted = {}
    for key, value in params.items():
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in _AUTHENTICATION_HEADER_PARAMETERS:
            continue
        if normalized_key not in _NON_SECRET_TOKEN_PARAMETERS and any(
            marker in normalized_key for marker in _SENSITIVE_PARAMETER_MARKERS
        ):
            continue
        redacted[key] = _redact_parameter_value(value)
    return redacted


def _redact_parameter_value(value: Any) -> Any:
    """复制配置中的对象与数组，保留非凭据参数值。"""
    if isinstance(value, dict):
        return redact_sensitive_params(value)
    if isinstance(value, list):
        return [_redact_parameter_value(item) for item in value]
    return value
