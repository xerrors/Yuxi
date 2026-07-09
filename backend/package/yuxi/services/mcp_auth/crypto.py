from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_KEY_ENV = "MCP_CREDENTIALS_MASTER_KEY"
ENVELOPE_VERSION = 2
ENVELOPE_KEY_ID = "local"
_AAD = b"yuxi:mcp_credentials:v1"


def _get_master_key() -> str:
    value = os.getenv(MASTER_KEY_ENV, "").strip()
    if not value:
        raise ValueError(f"{MASTER_KEY_ENV} is required when storing encrypted MCP credentials")
    return value


def _derive_aes_key_v1(master_key: str) -> bytes:
    # legacy v1 key derivation (raw sha256)
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def _derive_aes_key_v2(master_key: str, salt: bytes) -> bytes:
    # v2 key derivation using HKDF
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"mcp-credentials-v2",
    )
    return hkdf.derive(master_key.encode("utf-8"))


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
    v = payload.get("v")
    if v not in (1, 2):
        return None
    if v == 2 and "salt" not in payload:
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
    salt = os.urandom(16)
    aesgcm = AESGCM(_derive_aes_key_v2(master_key, salt))
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return json.dumps(
        {
            "v": ENVELOPE_VERSION,
            "kid": ENVELOPE_KEY_ID,
            "salt": _b64encode(salt),
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
    v = payload.get("v")
    if v == 1:
        key = _derive_aes_key_v1(master_key)
    elif v == 2:
        salt = _b64decode(payload["salt"])
        key = _derive_aes_key_v2(master_key, salt)
    else:
        return blob

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(
            _b64decode(payload["nonce"]),
            _b64decode(payload["ciphertext"]),
            _AAD,
        )
    except Exception as exc:
        from yuxi.utils import logger

        logger.error(f"Failed to decrypt credential blob: {exc}")
        raise ValueError("Failed to decrypt credential blob") from exc
    return plaintext.decode("utf-8")
