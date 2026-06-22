from __future__ import annotations

import hashlib
import os
from datetime import timedelta

import jwt
import pytest
from yuxi.utils.datetime_utils import utc_now

from yuxi.utils.auth_utils import JWT_ALGORITHM, JWT_AUDIENCE, AuthUtils


def test_generate_api_key_returns_secret_hash_and_prefix():
    full_key, key_hash, key_prefix = AuthUtils.generate_api_key()

    assert full_key.startswith("yxkey_")
    assert len(full_key) == len("yxkey_") + 48
    assert key_prefix == full_key[:12]
    assert key_hash == hashlib.sha256(full_key.encode()).hexdigest()


def test_hash_password_uses_argon2():
    hashed = AuthUtils.hash_password("secret-password")

    assert hashed.startswith("$argon2")
    assert AuthUtils.verify_password(hashed, "secret-password") is True
    assert AuthUtils.verify_password(hashed, "wrong-password") is False


def test_access_token_contains_instance_claims(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")

    token = AuthUtils.create_access_token({"sub": "1"})
    payload = AuthUtils.verify_access_token(token)

    assert payload["sub"] == "1"
    assert payload["iss"] == "yuxi-know:pytest-instance"
    assert payload["aud"] == JWT_AUDIENCE


def test_access_token_auto_generates_dev_secret(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")

    token = AuthUtils.create_access_token({"sub": "1"})

    assert AuthUtils.verify_access_token(token)["sub"] == "1"
    assert len(os.environ["JWT_SECRET_KEY"]) == 64


def test_access_token_requires_configured_secret_in_production(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "production")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        AuthUtils.create_access_token({"sub": "1"})


def test_access_token_rejects_public_default_secret_in_production(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "yuxi_know_secure_key")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")

    with pytest.raises(ValueError, match="公开默认密钥"):
        AuthUtils.create_access_token({"sub": "1"})


def test_access_token_auto_generates_dev_instance_id(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.delenv("YUXI_INSTANCE_ID", raising=False)

    token = AuthUtils.create_access_token({"sub": "1"})

    assert AuthUtils.verify_access_token(token)["iss"].startswith("yuxi-know:instance-")
    assert os.environ["YUXI_INSTANCE_ID"].startswith("instance-")


def test_access_token_requires_instance_id_in_production(monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.delenv("YUXI_INSTANCE_ID", raising=False)

    with pytest.raises(ValueError, match="YUXI_INSTANCE_ID"):
        AuthUtils.create_access_token({"sub": "1"})


def test_verify_access_token_rejects_wrong_issuer(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")
    token = jwt.encode(
        {"sub": "1", "exp": utc_now() + timedelta(minutes=5), "iss": "yuxi-know:other", "aud": JWT_AUDIENCE},
        "test-secret-key-with-enough-randomness",
        algorithm=JWT_ALGORITHM,
    )

    assert AuthUtils.decode_token(token) is None


def test_verify_access_token_rejects_wrong_audience(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")
    token = jwt.encode(
        {"sub": "1", "exp": utc_now() + timedelta(minutes=5), "iss": "yuxi-know:pytest-instance", "aud": "other-api"},
        "test-secret-key-with-enough-randomness",
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(ValueError, match="无效的令牌"):
        AuthUtils.verify_access_token(token)


def test_verify_access_token_requires_claims(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-randomness")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-instance")
    token = jwt.encode(
        {"sub": "1", "exp": utc_now() + timedelta(minutes=5)},
        "test-secret-key-with-enough-randomness",
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(ValueError, match="无效的令牌"):
        AuthUtils.verify_access_token(token)
