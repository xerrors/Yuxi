from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


class TemplateResolutionError(ValueError):
    """Raised when a template placeholder cannot be resolved."""


def _lookup_path(root: Any, path: str, *, full_expression: str) -> Any:
    current = root
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
            continue
        raise TemplateResolutionError(f"Unknown template placeholder: {full_expression}")
    return current


def _resolve_placeholder(
    expression: str,
    *,
    context: Mapping[str, Any],
    secret: Mapping[str, Any],
    token: Mapping[str, Any],
    access_token: str | None,
) -> Any:
    if expression == "access_token":
        if access_token is None:
            raise TemplateResolutionError("Unknown template placeholder: access_token")
        return access_token

    if "." not in expression:
        raise TemplateResolutionError(f"Unknown template placeholder: {expression}")

    root_name, path = expression.split(".", 1)
    roots = {
        "context": context,
        "secret": secret,
        "token": token,
    }
    if root_name not in roots:
        raise TemplateResolutionError(f"Unknown template placeholder: {expression}")
    return _lookup_path(roots[root_name], path, full_expression=expression)


def resolve_template_value(
    value: Any,
    *,
    context: Mapping[str, Any],
    secret: Mapping[str, Any],
    token: Mapping[str, Any],
    access_token: str | None,
) -> Any:
    if isinstance(value, Mapping):
        return {
            key: resolve_template_value(
                item,
                context=context,
                secret=secret,
                token=token,
                access_token=access_token,
            )
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            resolve_template_value(
                item,
                context=context,
                secret=secret,
                token=token,
                access_token=access_token,
            )
            for item in value
        ]

    if not isinstance(value, str):
        return value

    matches = list(_PLACEHOLDER_PATTERN.finditer(value))
    if not matches:
        return value

    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return _resolve_placeholder(
            matches[0].group(1),
            context=context,
            secret=secret,
            token=token,
            access_token=access_token,
        )

    parts: list[str] = []
    cursor = 0
    for match in matches:
        start, end = match.span()
        if start > cursor:
            parts.append(value[cursor:start])
        resolved = _resolve_placeholder(
            match.group(1),
            context=context,
            secret=secret,
            token=token,
            access_token=access_token,
        )
        parts.append(str(resolved))
        cursor = end
    if cursor < len(value):
        parts.append(value[cursor:])
    return "".join(parts)
