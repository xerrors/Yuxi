from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_ENV = "MCP_CREDENTIALS_MASTER_KEY"
ENVELOPE_VERSION = 1
ENVELOPE_KEY_ID = "local"
_AAD = b"yuxi:mcp_credentials:v1"


def _get_master_key() -> str:
    value = os.getenv(MASTER_KEY_ENV, "").strip()
    if not value:
        raise ValueError(f"{MASTER_KEY_ENV} is required when storing encrypted MCP credentials")
    return value


def _derive_aes_key(master_key: str) -> bytes:
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _parse_envelope(blob: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    required_keys = {"v", "kid", "nonce", "ciphertext"}
    if not required_keys.issubset(payload.keys()):
        return None
    if payload.get("v") != ENVELOPE_VERSION:
        return None
    return payload


def is_encrypted_credential_blob(blob: str | None) -> bool:
    if not blob or not isinstance(blob, str):
        return False
    return _parse_envelope(blob) is not None


def encrypt_credential_blob(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    if is_encrypted_credential_blob(plaintext):
        return plaintext

    master_key = _get_master_key()
    aesgcm = AESGCM(_derive_aes_key(master_key))
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return json.dumps(
        {
            "v": ENVELOPE_VERSION,
            "kid": ENVELOPE_KEY_ID,
            "nonce": _b64encode(nonce),
            "ciphertext": _b64encode(ciphertext),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def decrypt_credential_blob(blob: str | None) -> str | None:
    if blob is None or not isinstance(blob, str):
        return blob

    payload = _parse_envelope(blob)
    if payload is None:
        return blob

    master_key = _get_master_key()
    aesgcm = AESGCM(_derive_aes_key(master_key))
    plaintext = aesgcm.decrypt(
        _b64decode(payload["nonce"]),
        _b64decode(payload["ciphertext"]),
        _AAD,
    )
    return plaintext.decode("utf-8")
