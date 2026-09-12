import pytest

from yuxi.knowledge.utils.kb_utils import build_kb_image_proxy_url, prepare_item_metadata


def test_build_kb_image_proxy_url_uses_private_bucket_proxy_path():
    url = build_kb_image_proxy_url("db_1/kb-images/1710000000000_diagram.png")

    assert url == "/api/knowledge/databases/db_1/images/kb-images/1710000000000_diagram.png"


def test_build_kb_image_proxy_url_encodes_special_chars_but_keeps_slashes():
    url = build_kb_image_proxy_url("db_1/kb-images/1710000000000_user 1.png")

    assert url == "/api/knowledge/databases/db_1/images/kb-images/1710000000000_user%201.png"


@pytest.mark.parametrize("object_name", ["db_1/images/a.png", "/kb-images/a.png", "db_1"])
def test_build_kb_image_proxy_url_rejects_invalid_object_name(object_name):
    with pytest.raises(ValueError, match="知识库图片对象名"):
        build_kb_image_proxy_url(object_name)


async def test_prepare_item_metadata_preserves_uploaded_file_size():
    item = "minio://knowledgebases/db/upload/demo.txt"
    params = {
        "content_hashes": {item: "hash"},
        "file_sizes": {item: 1234},
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 1234
    assert "file_sizes" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_uses_source_path_as_display_filename():
    item = "minio://knowledgebases/db/upload/intro_1710000000000.md"
    params = {
        "content_hashes": {item: "hash"},
        "source_path": "guides/setup/Intro.MD",
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["filename"] == "guides/setup/Intro.MD"
    assert metadata["file_type"] == "md"
    assert metadata["path"] == item


async def test_prepare_item_metadata_preserves_preprocessed_file_size():
    item = "minio://knowledgebases/db/upload/page.html"
    params = {
        "_preprocessed_map": {
            item: {
                "path": item,
                "content_hash": "hash",
                "filename": "https://example.com",
                "file_size": 5678,
            }
        }
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 5678
    assert "_preprocessed_map" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_rejects_direct_url_content_type():
    with pytest.raises(ValueError, match="Unsupported content_type"):
        await prepare_item_metadata("https://example.com", "url", "db")


@pytest.mark.parametrize(
    "source",
    [
        "minio://knowledgebases/other/upload/private.txt",
        "minio://knowledgebases/db-other/upload/private.txt",
        "minio://kb-images/db/upload/private.txt",
        "minio://knowledgebases/db/parsed/private.txt",
        "minio://knowledgebases/db/upload/%2e%2e/other.txt",
        "http://minio:9000/knowledgebases/other/upload/private.txt",
    ],
)
async def test_prepare_item_metadata_rejects_foreign_document_sources(source):
    with pytest.raises(ValueError, match="当前知识库的上传目录"):
        await prepare_item_metadata(source, "file", "db", params={"content_hashes": {source: "hash"}})


async def test_prepare_item_metadata_rejects_preprocessed_foreign_path():
    item = "minio://knowledgebases/db/upload/page.html"
    params = {
        "_preprocessed_map": {
            item: {
                "path": "minio://knowledgebases/other/upload/private.html",
                "content_hash": "hash",
            }
        }
    }
    with pytest.raises(ValueError, match="当前知识库的上传目录"):
        await prepare_item_metadata(item, "file", "db", params=params)


async def test_prepare_item_metadata_accepts_http_uploaded_document_source():
    item = "http://minio:9000/knowledgebases/db/upload/my%20notes.md"
    metadata = await prepare_item_metadata(item, "file", "db", params={"content_hashes": {item: "hash"}})
    assert metadata["path"] == item
    assert metadata["kb_id"] == "db"
    assert metadata["filename"] == "my notes.md"
