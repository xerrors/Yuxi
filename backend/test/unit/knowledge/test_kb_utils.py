import pytest

from yuxi.knowledge.utils.kb_utils import prepare_item_metadata


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
