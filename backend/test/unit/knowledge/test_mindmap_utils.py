from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.knowledge.utils import mindmap_utils as mm


def make_kb(**overrides):
    data = {
        "kb_id": "kb_1",
        "name": "知识库",
        "mindmap": {"content": "知识库", "children": [{"content": "tracked.pdf", "children": []}]},
        "mindmap_file_ids": {"tracked": "tracked.pdf"},
        "mindmap_metadata": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_file(file_id: str, filename: str, *, kb_id: str = "kb_1"):
    return SimpleNamespace(
        file_id=file_id,
        kb_id=kb_id,
        filename=filename,
        file_type="pdf",
        status="indexed",
        is_folder=False,
        created_at=None,
    )


class FakeKnowledgeBaseRepository:
    def __init__(self, kb):
        self.kb = kb
        self.updates = []

    async def get_by_kb_id(self, kb_id):
        return self.kb if kb_id == self.kb.kb_id else None

    async def update(self, kb_id, data):
        self.updates.append((kb_id, data))


@pytest.mark.asyncio
async def test_get_mindmap_diff_keeps_tracked_file_outside_first_page(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb())

    class FakeFileRepository:
        async def search_files(self, **kwargs):
            assert kwargs["limit"] == mm.MINDMAP_FILE_PAGE_SIZE
            return [make_file("new", "new.pdf")], 100

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["tracked"]
            return [make_file("tracked", "tracked.pdf")]

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )

    result = await mm.get_mindmap_diff("kb_1")

    assert result["removed_file_ids"] == []
    assert result["unchanged_count"] == 1
    assert result["added_files"] == [{"file_id": "new", "filename": "new.pdf", "type": "pdf"}]
    assert result["current_files_truncated"] is True


@pytest.mark.asyncio
async def test_generate_database_mindmap_loads_selected_file_ids_directly(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def list_documents(self, **kwargs):
            raise AssertionError("selected file generation should query by file id")

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["outside-page"]
            return [make_file("outside-page", "outside.pdf")]

    class FakeModel:
        async def call(self, messages, stream):
            assert "outside.pdf" in messages[1]["content"]
            return SimpleNamespace(content='{"content":"知识库","children":[{"content":"outside.pdf","children":[]}]}')

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )
    monkeypatch.setattr(mm, "select_model", lambda model_spec: FakeModel())

    result = await mm.generate_database_mindmap("kb_1", file_ids=["outside-page"])

    assert result["file_count"] == 1
    assert result["original_file_count"] == 1
    assert kb_repo.updates[0][1]["mindmap_file_ids"] == {"outside-page": "outside.pdf"}


@pytest.mark.asyncio
async def test_generate_database_mindmap_includes_nested_files_when_root_is_empty(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def search_files(self, **kwargs):
            assert kwargs == {
                "kb_id": "kb_1",
                "offset": 0,
                "limit": mm.MINDMAP_GENERATION_FILE_LIMIT,
                "files_only": True,
            }
            return [make_file("nested", "nested.pdf")], 1

    class FakeModel:
        async def call(self, messages, stream):
            assert "nested.pdf" in messages[1]["content"]
            return SimpleNamespace(content='{"content":"知识库","children":[{"content":"nested.pdf","children":[]}]}')

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )
    monkeypatch.setattr(mm, "select_model", lambda model_spec: FakeModel())

    result = await mm.generate_database_mindmap("kb_1")

    assert result["file_count"] == 1
    assert result["original_file_count"] == 1
    assert kb_repo.updates[0][1]["mindmap_file_ids"] == {"nested": "nested.pdf"}


@pytest.mark.asyncio
async def test_generate_database_mindmap_rejects_missing_selected_files(monkeypatch):
    kb_repo = FakeKnowledgeBaseRepository(make_kb(mindmap=None, mindmap_file_ids=None))

    class FakeFileRepository:
        async def list_by_file_ids(self, file_ids):
            return []

    monkeypatch.setattr(mm, "KnowledgeBaseRepository", lambda: kb_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        FakeFileRepository,
    )

    with pytest.raises(HTTPException, match="选择的文件不存在"):
        await mm.generate_database_mindmap("kb_1", file_ids=["missing"])
