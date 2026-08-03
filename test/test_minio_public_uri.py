import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest


def _load_client_module():
    """Load the storage client without triggering the app's eager graph setup."""
    src_module = types.ModuleType("src")
    utils_module = types.ModuleType("src.utils")
    utils_module.logger = logging.getLogger("test.minio")
    sys.modules.setdefault("src", src_module)
    sys.modules.setdefault("src.utils", utils_module)

    source = Path(__file__).parents[1] / "src" / "storage" / "minio" / "client.py"
    spec = importlib.util.spec_from_file_location("minio_client_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


client_module = _load_client_module()
MinIOClient = client_module.MinIOClient
StorageError = client_module.StorageError


def test_minio_upload_uses_configured_public_reverse_proxy_uri(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_URI", "http://minio:9000")
    monkeypatch.setenv("MINIO_PUBLIC_URI", "https://example.test/storage/")
    client = MinIOClient()

    class FakeMinio:
        def put_object(self, **_kwargs):
            return object()

    client._client = FakeMinio()
    monkeypatch.setattr(client, "ensure_bucket_exists", lambda bucket_name: True)

    result = client.upload_file("avatar", "user.png", b"image")

    assert result.url == "https://example.test/storage/avatar/user.png"


@pytest.mark.asyncio
async def test_minio_public_uri_maps_proxy_path_back_to_bucket(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_URI", "http://minio:9000")
    monkeypatch.setenv("MINIO_PUBLIC_URI", "https://example.test/storage")
    client = MinIOClient()
    downloaded = []

    async def fake_download(bucket_name: str, object_name: str) -> bytes:
        downloaded.append((bucket_name, object_name))
        return b"image"

    monkeypatch.setattr(client, "adownload_file", fake_download)

    async with client.temp_file_from_url(
        "https://example.test/storage/avatar/user.png",
        allowed_extensions=[".png"],
    ) as temp_file:
        assert Path(temp_file).read_bytes() == b"image"

    assert downloaded == [("avatar", "user.png")]
    assert not Path(temp_file).exists()


@pytest.mark.asyncio
async def test_minio_public_uri_rejects_external_host_and_wrong_prefix(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_URI", "http://minio:9000")
    monkeypatch.setenv("MINIO_PUBLIC_URI", "https://example.test/storage")
    client = MinIOClient()

    with pytest.raises(StorageError, match="不允许的外部 URL"):
        async with client.temp_file_from_url("https://evil.example/avatar/user.png"):
            pass

    with pytest.raises(StorageError, match="不属于配置的 MinIO 公开路径"):
        async with client.temp_file_from_url("https://example.test/other/avatar/user.png"):
            pass


def test_minio_public_uri_requires_valid_http_url(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_PUBLIC_URI", "https://")

    with pytest.raises(StorageError, match="有效的 http/https URL"):
        MinIOClient()
