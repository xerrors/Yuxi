from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.template_resolver import TemplateResolutionError, resolve_template_value


def test_resolve_template_value_supports_nested_structures():
    resolved = resolve_template_value(
        {
            "headers": {
                "Authorization": "Bearer ${access_token}",
                "X-User-Id": "${context.user_id}",
            },
            "body": {
                "client_id": "${secret.client_id}",
                "tenant": "${secret.extra.tenant_code}",
                "department_id": "${context.department_id}",
            },
            "args": ["--user=${context.user_id}", "--refresh=${token.refresh_token}"],
        },
        context={"user_id": "u-100", "department_id": "d-9"},
        secret={"client_id": "cid-1", "extra": {"tenant_code": "finance"}},
        token={"refresh_token": "refresh-1"},
        access_token="access-1",
    )

    assert resolved == {
        "headers": {
            "Authorization": "Bearer access-1",
            "X-User-Id": "u-100",
        },
        "body": {
            "client_id": "cid-1",
            "tenant": "finance",
            "department_id": "d-9",
        },
        "args": ["--user=u-100", "--refresh=refresh-1"],
    }


def test_resolve_template_value_raises_for_unknown_placeholder():
    with pytest.raises(TemplateResolutionError, match="context.missing"):
        resolve_template_value(
            {"user": "${context.missing}"},
            context={"user_id": "u-100"},
            secret={},
            token={},
            access_token="access-1",
        )
