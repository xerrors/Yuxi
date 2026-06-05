from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InjectEntry(BaseModel):
    name: str
    value_template: str


class InjectConfig(BaseModel):
    target: Literal["headers", "env"]
    entries: list[InjectEntry] = Field(default_factory=list)


class RefreshPolicy(BaseModel):
    pre_refresh_seconds: int = 0
    retry_once_on_401: bool = False


class MCPAuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    provider: Literal[
        "legacy_static",
        "bound_secret",
        "client_credentials",
        "custom_http_token",
        "authorization_code",
        "stdio_env",
    ]
    binding_scope: Literal["inline", "system", "department", "user"] | None = None
    manifest_scope: Literal["server", "binding"] | None = None
    inject: InjectConfig
    refresh_policy: RefreshPolicy = Field(default_factory=RefreshPolicy)
    token_request: dict[str, Any] | None = None

    @model_validator(mode="after")
    def apply_defaults_and_validate(self) -> MCPAuthConfig:
        if self.binding_scope is None:
            self.binding_scope = "inline" if self.provider == "legacy_static" else "system"
        if self.manifest_scope is None:
            self.manifest_scope = "server"
        if (
            self.provider in {"custom_http_token", "client_credentials", "authorization_code"}
            and not self.token_request
        ):
            raise ValueError("token_request is required for dynamic auth providers")
        return self

    def get_secret_fields(self) -> list[str]:
        """Extract all secret fields referenced in the configuration templates."""
        import re
        import json
        
        pattern = re.compile(r"\$\{secret\.([^\}]+)\}")
        dumped = json.dumps(self.model_dump(mode="json"))
        matches = pattern.findall(dumped)
        # Deduplicate while preserving order
        return list(dict.fromkeys(matches))
