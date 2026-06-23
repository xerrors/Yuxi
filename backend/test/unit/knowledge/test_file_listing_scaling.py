from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager

pytestmark = pytest.mark.asyncio


class FakeKnowledgeBaseClass:
    @classmethod
    def normalize_additional_params(cls, additional_params):
        return dict(additional_params or {})


class FakeKnowledgeBaseRepository:
    async def get_by_kb_id(self, kb_id):
        if kb_id != "kb_1":
            return None
        return SimpleNamespace(
            kb_id="kb_1",
            name="知识库",
            description="desc",
            kb_type="milvus",
            embedding_model_spec="embedding:model",
            llm_model_spec="llm:model",
            query_params={"options": {}},
            additional_params={"chunk_preset_id": "general"},
            share_config=None,
            mindmap=None,
            sample_questions=[],
            created_at=None,
        )


class FakeKnowledgeFileRepository:
    list_calls = []
    exists_calls = []
    action_id_calls = []

    def __init__(self):
        self.records = [
            SimpleNamespace(
                file_id="folder_1",
                kb_id="kb_1",
                parent_id=None,
                filename="资料",
                file_type=None,
                status="done",
                is_folder=True,
                path=None,
                minio_url=None,
                markdown_file=None,
                created_at=None,
                updated_at=None,
                file_size=0,
            ),
            SimpleNamespace(
                file_id="file_1",
                kb_id="kb_1",
                parent_id=None,
                filename="alpha.pdf",
                file_type="pdf",
                status="indexed",
                is_folder=False,
                path="minio://bucket/file",
                minio_url="minio://bucket/file",
                markdown_file="minio://bucket/parsed",
                created_at=None,
                updated_at=None,
                file_size=1024,
            ),
        ]

    async def get_kb_file_stats(self, kb_id):
        return {
            "row_count": 3,
            "file_count": 2,
            "folder_count": 1,
            "total_size": 1024,
            "chunk_count": 9,
            "token_count": 128,
            "pending_parse_count": 1,
            "pending_index_count": 0,
            "processing_count": 0,
        }

    async def get_by_file_id(self, file_id):
        return next((record for record in self.records if record.file_id == file_id), None)

    async def list_documents(self, **kwargs):
        self.__class__.list_calls.append(kwargs)
        return self.records, 2

    async def list_file_ids_by_exact_statuses(self, **kwargs):
        self.__class__.action_id_calls.append(kwargs)
        return ["file_2"]

    async def exists_by_filename(self, *, kb_id, filename):
        self.__class__.exists_calls.append({"kb_id": kb_id, "filename": filename})
        return filename == "docs/Guide.md"

    async def count_children_by_parent_ids(self, *, kb_id, parent_ids):
        return {"folder_1": 1}


@pytest.fixture(autouse=True)
def patch_repositories(monkeypatch):
    FakeKnowledgeFileRepository.list_calls = []
    FakeKnowledgeFileRepository.exists_calls = []
    FakeKnowledgeFileRepository.action_id_calls = []
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeKnowledgeFileRepository,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        staticmethod(lambda _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.get_kb_class",
        staticmethod(lambda _kb_type: FakeKnowledgeBaseClass),
    )


async def test_get_database_info_omits_files_by_default():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.get_database_info("kb_1")

    assert result["kb_id"] == "kb_1"
    assert "files" not in result
    assert result["stats"]["file_count"] == 2
    assert result["stats"]["total_size"] == 1024


async def test_list_document_files_returns_lightweight_paginated_items():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.list_document_files(
        "kb_1",
        parent_id="folder_1",
        status="indexed",
        page=2,
        page_size=50,
    )

    assert result["page"] == 2
    assert result["page_size"] == 50
    assert result["total"] == 2
    assert FakeKnowledgeFileRepository.list_calls == [
        {
            "kb_id": "kb_1",
            "parent_id": "folder_1",
            "path_prefix": None,
            "status": "indexed",
            "page": 2,
            "page_size": 50,
            "recursive": False,
            "files_only": False,
        }
    ]
    assert result["items"][0]["has_children"] is True
    assert result["items"][1]["file_size"] == 1024
    assert result["items"][1]["has_original_file"] is True
    assert result["items"][1]["has_parsed_markdown"] is True

    returned_keys = set(result["items"][1])
    assert "path" not in returned_keys
    assert "markdown_file" not in returned_keys
    assert "chunk_count" not in returned_keys
    assert "token_count" not in returned_keys
    assert "processing_params" not in returned_keys


async def test_list_document_files_keeps_virtual_folder_contract():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")
    virtual_record = SimpleNamespace(
        file_id="__virtual_folder__:root:资料/",
        kb_id="kb_1",
        parent_id=None,
        filename="资料",
        file_type="folder",
        status="done",
        is_folder=True,
        is_virtual_folder=True,
        path_prefix="资料/",
        virtual_children_count=3,
        path=None,
        minio_url=None,
        markdown_file=None,
        created_at=None,
        updated_at=None,
        file_size=0,
    )

    item = manager._file_record_list_item(virtual_record)

    assert item["is_folder"] is True
    assert item["is_virtual_folder"] is True
    assert item["path_prefix"] == "资料/"
    assert item["has_children"] is True
    assert item["children_count"] == 3


async def test_list_document_files_passes_files_only_and_can_omit_stats():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.list_document_files("kb_1", files_only=True, include_stats=False)

    assert "stats" not in result
    assert FakeKnowledgeFileRepository.list_calls == [
        {
            "kb_id": "kb_1",
            "parent_id": None,
            "path_prefix": None,
            "status": None,
            "page": 1,
            "page_size": 100,
            "recursive": False,
            "files_only": True,
        }
    ]


async def test_list_document_files_ignores_recursive_without_status_filter():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.list_document_files("kb_1", recursive=True, include_stats=False)

    assert result["recursive"] is False
    assert FakeKnowledgeFileRepository.list_calls == [
        {
            "kb_id": "kb_1",
            "parent_id": None,
            "path_prefix": None,
            "status": None,
            "page": 1,
            "page_size": 100,
            "recursive": False,
            "files_only": False,
        }
    ]


async def test_document_file_exists_delegates_exact_filename_to_repository():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    assert await manager.document_file_exists("kb_1", " docs/Guide.md ") is True
    assert await manager.document_file_exists("kb_1", "docs/guide.md") is False
    assert FakeKnowledgeFileRepository.exists_calls == [
        {"kb_id": "kb_1", "filename": "docs/Guide.md"},
        {"kb_id": "kb_1", "filename": "docs/guide.md"},
    ]


async def test_list_document_file_ids_by_statuses_delegates_to_repository():
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.list_document_file_ids_by_statuses(
        "kb_1",
        statuses=["parsed", "error_indexing"],
        after_file_id="file_1",
        limit=500,
    )

    assert result == ["file_2"]
    assert FakeKnowledgeFileRepository.action_id_calls == [
        {
            "kb_id": "kb_1",
            "statuses": ["parsed", "error_indexing"],
            "after_file_id": "file_1",
            "limit": 500,
        }
    ]
