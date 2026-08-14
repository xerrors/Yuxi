from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.permissions import ResourcePermission

pytestmark = pytest.mark.asyncio


class FakeKnowledgeBaseClass:
    @classmethod
    def normalize_additional_params(cls, additional_params):
        return dict(additional_params or {})


class FakeKnowledgeBaseRepository:
    async def get_all(self):
        record = await self.get_by_kb_id("kb_1")
        record.additional_params = {
            "chunk_preset_id": "general",
            "stats": {"file_count": 2, "folder_count": 1, "row_count": 3},
        }
        record.created_by = "user_1"
        return [record]

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
            created_by="user_1",
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
                chunk_count=0,
                token_count=0,
                created_by="user_1",
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
                chunk_count=9,
                token_count=128,
                created_by="user_1",
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


class FakeUserRepository:
    async def list_by_uids(self, uids):
        if "user_1" not in uids:
            return []
        return [
            SimpleNamespace(
                uid="user_1",
                username="测试用户",
                to_dict=lambda: {"avatar": "https://example.com/avatar.png"},
            )
        ]


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
        "yuxi.repositories.user_repository.UserRepository",
        FakeUserRepository,
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

    assert result.kb_id == "kb_1"
    assert result.files is None
    assert result.file_count == 2
    assert result.total_size == 1024


async def test_get_databases_does_not_initialize_knowledge_backend(monkeypatch):
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    def fail_if_initialized(_kb_type):
        pytest.fail("知识库列表不应初始化 Milvus 等后端实例")

    monkeypatch.setattr(manager, "_get_or_create_kb_instance", fail_if_initialized)

    result = await manager.get_databases()

    database = result[0]
    assert database.kb_id == "kb_1"
    assert database.name == "知识库"
    assert database.row_count == 3
    assert database.file_count == 2
    assert database.additional_params["chunk_preset_id"] == "general"
    assert "stats" not in database.additional_params
    assert database.created_by == "user_1"


async def test_get_databases_skips_rows_with_invalid_metadata(monkeypatch):
    class BrokenKnowledgeBaseClass:
        @classmethod
        def normalize_additional_params(cls, _additional_params):
            raise ValueError("Notion 参数缺失: notion_data_source_id")

    class MultiKnowledgeBaseRepository:
        async def get_all(self):
            return [
                SimpleNamespace(
                    kb_id="kb_bad",
                    name="坏配置",
                    description="",
                    kb_type="notion",
                    embedding_model_spec=None,
                    llm_model_spec=None,
                    query_params=None,
                    additional_params={},
                    share_config=None,
                    created_at=None,
                    created_by="user_1",
                ),
                SimpleNamespace(
                    kb_id="kb_good",
                    name="可用库",
                    description="",
                    kb_type="milvus",
                    embedding_model_spec=None,
                    llm_model_spec=None,
                    query_params=None,
                    additional_params={"chunk_preset_id": "general"},
                    share_config=None,
                    created_at=None,
                    created_by="user_1",
                ),
            ]

    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.get_kb_class",
        staticmethod(lambda kb_type: BrokenKnowledgeBaseClass if kb_type == "notion" else FakeKnowledgeBaseClass),
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        MultiKnowledgeBaseRepository,
    )

    manager = KnowledgeBaseManager("/tmp/yuxi-test")
    result = await manager.get_databases()

    assert [db.kb_id for db in result] == ["kb_good"]


async def test_get_databases_by_user_sets_permission_and_redacts_readonly_secrets(monkeypatch):
    class SecretKnowledgeBaseRepository(FakeKnowledgeBaseRepository):
        async def get_all(self):
            record = await self.get_by_kb_id("kb_1")
            record.additional_params = {"dify_token": "secret", "chunk_preset_id": "general"}
            record.created_by = "user_1"
            return [record]

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        SecretKnowledgeBaseRepository,
    )
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    readonly = await manager.get_databases_by_user({"uid": "user_2", "role": "admin", "department_id": None})
    owner = await manager.get_databases_by_user({"uid": "user_1", "role": "admin", "department_id": None})

    assert readonly[0].effective_permission == ResourcePermission.READ
    assert readonly[0].can_manage is False
    assert "dify_token" not in readonly[0].additional_params
    assert owner[0].effective_permission == ResourcePermission.MANAGE
    assert owner[0].can_manage is True
    assert owner[0].additional_params["dify_token"] == "secret"


@pytest.mark.parametrize(
    ("scenario", "kwargs", "expected_call"),
    [
        (
            "paginated",
            {"parent_id": "folder_1", "status": "indexed", "page": 2, "page_size": 50},
            {
                "kb_id": "kb_1",
                "parent_id": "folder_1",
                "path_prefix": None,
                "status": "indexed",
                "page": 2,
                "page_size": 50,
                "recursive": False,
                "files_only": False,
            },
        ),
        (
            "files_only",
            {"files_only": True, "include_stats": False},
            {
                "kb_id": "kb_1",
                "parent_id": None,
                "path_prefix": None,
                "status": None,
                "page": 1,
                "page_size": 100,
                "recursive": False,
                "files_only": True,
            },
        ),
        (
            "recursive",
            {"recursive": True, "include_stats": False},
            {
                "kb_id": "kb_1",
                "parent_id": None,
                "path_prefix": None,
                "status": None,
                "page": 1,
                "page_size": 100,
                "recursive": False,
                "files_only": False,
            },
        ),
    ],
)
async def test_list_document_files_passes_expected_parameters(scenario, kwargs, expected_call):
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    result = await manager.list_document_files("kb_1", **kwargs)

    assert FakeKnowledgeFileRepository.list_calls == [expected_call]
    if scenario == "paginated":
        assert result["page"] == 2
        assert result["page_size"] == 50
        assert result["total"] == 2
        assert result["items"][0]["has_children"] is True
        assert result["items"][1]["file_size"] == 1024
        assert result["items"][1]["chunk_count"] == 9
        assert result["items"][1]["token_count"] == 128
        assert result["items"][1]["created_by"] == "user_1"
        assert result["items"][1]["created_by_name"] == "测试用户"
        assert result["items"][1]["created_by_avatar"] == "https://example.com/avatar.png"
        assert result["items"][1]["has_original_file"] is True
        assert result["items"][1]["has_parsed_markdown"] is True

        returned_keys = set(result["items"][1])
        assert "path" not in returned_keys
        assert "markdown_file" not in returned_keys
        assert "processing_params" not in returned_keys
    elif scenario == "files_only":
        assert "stats" not in result
    else:
        assert result["recursive"] is False


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
