from types import SimpleNamespace

import pytest

from yuxi.knowledge.base import FolderNameConflictError, KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}


def make_folder_record(**overrides):
    data = {
        "file_id": "folder-1",
        "kb_id": "db",
        "parent_id": None,
        "filename": "资料",
        "file_type": "folder",
        "path": "资料",
        "markdown_file": None,
        "status": "done",
        "content_hash": None,
        "file_size": None,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": None,
        "processing_params": None,
        "is_folder": True,
        "error_message": None,
        "created_by": "user",
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
        "original_filename": None,
        "minio_url": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeFileRepo:
    def __init__(self, conflict: SimpleNamespace | None = None):
        self.conflict = conflict
        self.persisted = []
        self.updated = []

    async def get_by_file_id(self, file_id: str):
        return make_folder_record(file_id=file_id)

    async def find_folder_by_name(self, *, kb_id: str, parent_id: str | None, filename: str):
        return self.conflict

    async def upsert(self, *, file_id: str, data: dict):
        self.persisted.append((file_id, data))

    async def update_fields(self, *, file_id: str, kb_id: str | None = None, data: dict):
        self.updated.append((file_id, kb_id, data))
        return make_folder_record(file_id=file_id, filename=data["filename"], path=data["path"])


def install_repo(monkeypatch, repo: FakeFileRepo):
    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: repo)


@pytest.mark.asyncio
async def test_create_folder_rejects_duplicate_sibling_name(monkeypatch, tmp_path):
    repo = FakeFileRepo(conflict=make_folder_record(file_id="folder-existing"))
    install_repo(monkeypatch, repo)
    kb = FakeKnowledgeBase(str(tmp_path))

    with pytest.raises(FolderNameConflictError, match="同名文件夹"):
        await kb.create_folder("db", "资料")

    assert repo.persisted == []


@pytest.mark.asyncio
async def test_create_folder_normalizes_and_persists_unique_name(monkeypatch, tmp_path):
    repo = FakeFileRepo()
    install_repo(monkeypatch, repo)
    kb = FakeKnowledgeBase(str(tmp_path))

    meta = await kb.create_folder("db", "  新建文件夹  ", operator_id="user-9")

    assert meta["filename"] == "新建文件夹"
    assert meta["path"] == "新建文件夹"
    assert meta["is_folder"] is True
    assert meta["created_by"] == "user-9"
    assert len(repo.persisted) == 1


@pytest.mark.asyncio
async def test_create_folder_rejects_empty_name_and_separators(monkeypatch, tmp_path):
    repo = FakeFileRepo()
    install_repo(monkeypatch, repo)
    kb = FakeKnowledgeBase(str(tmp_path))

    for invalid in ("", "   ", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            await kb.create_folder("db", invalid)

    assert repo.persisted == []


@pytest.mark.asyncio
async def test_rename_folder_rejects_duplicate_but_allows_self(monkeypatch, tmp_path):
    repo = FakeFileRepo(conflict=make_folder_record(file_id="folder-other"))
    install_repo(monkeypatch, repo)
    kb = FakeKnowledgeBase(str(tmp_path))

    with pytest.raises(FolderNameConflictError, match="同名文件夹"):
        await kb.rename_folder("db", "folder-1", "资料")

    assert repo.updated == []

    repo.conflict = make_folder_record(file_id="folder-1")
    meta = await kb.rename_folder("db", "folder-1", " 资料 ")

    assert meta["filename"] == "资料"
    assert len(repo.updated) == 1
