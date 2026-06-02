from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.crypto import decrypt_credential_blob, encrypt_credential_blob


pytestmark = [pytest.mark.unit]


def test_encrypt_and_decrypt_credential_blob_round_trip(monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    plaintext = json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}, ensure_ascii=False)

    encrypted = encrypt_credential_blob(plaintext)
    decrypted = decrypt_credential_blob(encrypted)

    assert encrypted != plaintext
    assert json.loads(encrypted)["v"] == 1
    assert decrypted == plaintext


def test_decrypt_credential_blob_keeps_legacy_plaintext_payload(monkeypatch):
    monkeypatch.delenv("MCP_CREDENTIALS_MASTER_KEY", raising=False)
    plaintext = '{"secrets":{"access_token":"legacy-token"}}'

    assert decrypt_credential_blob(plaintext) == plaintext


def test_encrypt_credential_blob_requires_master_key(monkeypatch):
    monkeypatch.delenv("MCP_CREDENTIALS_MASTER_KEY", raising=False)

    with pytest.raises(ValueError, match="MCP_CREDENTIALS_MASTER_KEY"):
        encrypt_credential_blob('{"secrets":{"access_token":"token"}}')
