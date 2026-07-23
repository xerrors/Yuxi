from __future__ import annotations

import json
from typing import Any

OPENAI_COMPATIBLE_REQUEST_BODY_PROVIDER_TYPES = frozenset({"openai", "openrouter"})


def _field_key(field: str) -> str:
    return "".join(char for char in field.casefold() if char.isalnum())


# OpenAI SDK 会把 extra_body 合并进最终顶层 JSON；这些标准字段应继续由调用链控制。
OPENAI_CHAT_COMPLETION_REQUEST_BODY_FIELDS = frozenset(
    {
        "audio",
        "frequency_penalty",
        "function_call",
        "functions",
        "logit_bias",
        "logprobs",
        "max_completion_tokens",
        "max_tokens",
        "messages",
        "metadata",
        "modalities",
        "model",
        "n",
        "parallel_tool_calls",
        "prediction",
        "presence_penalty",
        "prompt_cache_key",
        "prompt_cache_retention",
        "reasoning_effort",
        "response_format",
        "safety_identifier",
        "seed",
        "service_tier",
        "stop",
        "store",
        "stream",
        "stream_options",
        "temperature",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
        "user",
        "verbosity",
        "web_search_options",
    }
)

PROTECTED_REQUEST_BODY_FIELDS = OPENAI_CHAT_COMPLETION_REQUEST_BODY_FIELDS | frozenset(
    {
        "api_key",
        "authorization",
        "base_url",
        "default_headers",
        "headers",
        "input",
        "model_name",
        "openai_api_base",
        "openai_api_key",
        "url",
        "x_api_key",
    }
)
_PROTECTED_REQUEST_BODY_FIELD_KEYS = frozenset(_field_key(field) for field in PROTECTED_REQUEST_BODY_FIELDS)


def normalize_request_body_overrides(value: Any, *, model_id: str = "") -> dict[str, Any]:
    """Validate and normalize per-model request body overrides."""
    label = f"模型 {model_id} 的 request_body_overrides" if model_id else "request_body_overrides"
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    if not value:
        return {}

    invalid_keys = [key for key in value if not isinstance(key, str) or not key.strip()]
    if invalid_keys:
        raise ValueError(f"{label} 的字段名必须是非空字符串")

    protected_fields = sorted(key for key in value if _field_key(key) in _PROTECTED_REQUEST_BODY_FIELD_KEYS)
    if protected_fields:
        raise ValueError(f"{label} 不允许覆盖受保护字段: {', '.join(protected_fields)}")

    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 只能包含合法 JSON 值") from exc

    return dict(value)
