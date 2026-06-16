from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.config_models import MCPAuthConfig


def test_mcp_auth_config_applies_legacy_static_defaults():
    config = MCPAuthConfig.model_validate(
        {
            "version": 1,
            "provider": "legacy_static",
            "inject": {
                "target": "headers",
                "entries": [],
            },
        }
    )

    assert config.binding_scope == "inline"
    assert config.manifest_scope == "server"
    assert config.refresh_policy.pre_refresh_seconds == 0
    assert config.refresh_policy.retry_once_on_401 is False


def test_mcp_auth_config_requires_token_request_for_dynamic_http_provider():
    with pytest.raises(ValidationError, match="token_request"):
        MCPAuthConfig.model_validate(
            {
                "version": 1,
                "provider": "custom_http_token",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
                },
            }
        )
