from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.knowledge.read_models import KnowledgeBaseDetail
from yuxi.knowledge.utils import sample_question_utils as sq


def _database_detail(files: dict | None = None, *, name: str = "测试知识库") -> KnowledgeBaseDetail:
    return KnowledgeBaseDetail(
        kb_id="kb_1",
        name=name,
        description=None,
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by=None,
        created_at=None,
        files=files,
    )


class FakeKnowledgeBase:
    def __init__(self, detail: KnowledgeBaseDetail):
        self.detail = detail

    async def get_database_info(self, kb_id: str, include_files: bool = False) -> KnowledgeBaseDetail:
        return self.detail


def test_parse_sample_questions_content_strips_json_fence():
    questions = sq.parse_sample_questions_content('```json\n{"questions": ["什么是测试？"]}\n```')

    assert questions == ["什么是测试？"]


def test_parse_sample_questions_content_rejects_invalid_payload():
    with pytest.raises(ValueError, match="问题格式"):
        sq.parse_sample_questions_content('{"items": []}')


@pytest.mark.asyncio
async def test_generate_database_sample_questions_rejects_empty_files(monkeypatch):
    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase(_database_detail({}, name="空知识库")))
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        await sq.generate_database_sample_questions("kb_1")

    assert exc_info.value.status_code == 400
    assert "没有文件" in exc_info.value.detail


@pytest.mark.asyncio
async def test_generate_database_sample_questions_saves_and_returns_questions(monkeypatch):
    saved: dict = {}

    class FakeModel:
        async def call(self, messages, stream: bool = False):
            assert messages[0]["role"] == "system"
            assert "demo.md" in messages[1]["content"]
            return SimpleNamespace(content='{"questions": ["如何使用 demo？"]}')

    class FakeRepository:
        async def update(self, kb_id: str, data: dict):
            """返回已保存的知识库，与 repository 契约一致。"""
            saved[kb_id] = data["sample_questions"]
            return await self.get_by_kb_id(kb_id)

        async def get_by_kb_id(self, kb_id: str):
            return SimpleNamespace(name="测试知识库", sample_questions=saved.get(kb_id))

    monkeypatch.setattr(
        sq,
        "knowledge_base",
        FakeKnowledgeBase(_database_detail({"file_1": {"filename": "demo.md", "file_type": "md"}})),
    )
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )
    monkeypatch.setattr(sq, "select_model", lambda model_spec: FakeModel())
    monkeypatch.setattr(sq, "KnowledgeBaseRepository", lambda: FakeRepository())

    async def get_system_options(_option, _db=None):
        return {"default_model": "test-provider:test-model"}

    monkeypatch.setattr(type(sq.system_options), "get", get_system_options)

    generated = await sq.generate_database_sample_questions("kb_1", count=1)
    stored = await sq.get_database_sample_questions("kb_1")

    assert generated["questions"] == ["如何使用 demo？"]
    assert generated["count"] == 1
    assert stored["questions"] == ["如何使用 demo？"]


@pytest.mark.asyncio
async def test_generate_database_sample_questions_maps_invalid_json(monkeypatch):
    class FakeModel:
        async def call(self, messages, stream: bool = False):
            return SimpleNamespace(content="not json")

    monkeypatch.setattr(
        sq,
        "knowledge_base",
        FakeKnowledgeBase(_database_detail({"file_1": {"filename": "demo.md", "file_type": "md"}})),
    )
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )
    monkeypatch.setattr(sq, "select_model", lambda model_spec: FakeModel())

    async def get_system_options(_option, _db=None):
        return {"default_model": "test-provider:test-model"}

    monkeypatch.setattr(type(sq.system_options), "get", get_system_options)

    with pytest.raises(HTTPException) as exc_info:
        await sq.generate_database_sample_questions("kb_1")

    assert exc_info.value.status_code == 500
    assert "AI返回格式错误" in exc_info.value.detail


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.asyncio
async def test_generated_questions_require_successful_save(monkeypatch, missing):
    """保存失败或目标消失时不能声称问题已生成并保存。"""
    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase(_database_detail({"file_1": {"filename": "demo.md"}})))
    monkeypatch.setattr(sq.KnowledgeBaseFactory, "get_kb_class", lambda _: SimpleNamespace(supports_documents=True))
    monkeypatch.setattr(
        sq, "system_options", SimpleNamespace(get=AsyncMock(return_value={"default_model": "test:model"}))
    )
    model = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content='{"questions":["测试？"]}')))
    monkeypatch.setattr(sq, "select_model", lambda **_: model)
    error = RuntimeError("database unavailable")
    repository = SimpleNamespace(update=AsyncMock(return_value=None, side_effect=None if missing else error))
    monkeypatch.setattr(sq, "KnowledgeBaseRepository", lambda: repository)

    with pytest.raises(HTTPException if missing else RuntimeError) as caught:
        await sq.generate_database_sample_questions("kb_1")

    if missing:
        assert caught.value.status_code == 404
    else:
        assert caught.value is error
