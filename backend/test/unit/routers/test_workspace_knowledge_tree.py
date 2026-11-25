from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import yuxi.api.routers.workspace.workspace as workspace_router
from yuxi.api.routers.workspace.workspace import workspace_knowledge
from yuxi.api.dependencies.auth import get_required_user
from yuxi.modules.knowledge import preview
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.identity.models import User


class FakeKnowledgeBase:
    def __init__(self, *, supports: bool = True):
        self.supports = supports
        self.list_calls = []

    async def check_accessible(self, _user, _kb_id):
        return True

    async def get_knowledge_base_document_support(self, kb_id):
        knowledge_base = KnowledgeBaseDetail(
            kb_id=kb_id,
            name="知识库",
            description=None,
            kb_type="milvus" if self.supports else "dify",
            embedding_model_spec=None,
            llm_model_spec=None,
            query_params={},
            additional_params={},
            share_config={"version": 2, "read_scope": None, "manage_scope": None},
            created_by=None,
            created_at=None,
        )
        return knowledge_base, self.supports

    async def list_document_files(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "items": [
                {
                    "file_id": "folder_1",
                    "filename": "资料",
                    "status": "done",
                    "is_folder": True,
                    "parent_id": None,
                    "created_at": None,
                    "updated_at": None,
                    "file_size": 0,
                },
                {
                    "file_id": "file_1",
                    "filename": "alpha.pdf",
                    "status": "indexed",
                    "is_folder": False,
                    "parent_id": None,
                    "created_at": "2026-06-20T00:00:00Z",
                    "updated_at": "2026-06-20T01:00:00Z",
                    "file_size": 2048,
                    "has_original_file": True,
                    "has_parsed_markdown": True,
                },
            ],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
            "total": 2,
            "has_more": False,
            "parent_id": kwargs["parent_id"],
        }


def _build_client(monkeypatch, fake_kb: FakeKnowledgeBase) -> TestClient:
    app = FastAPI()
    app.include_router(workspace_knowledge, prefix="/api")

    async def fake_required_user():
        return User(username="user", uid="user", password_hash="x", role="user", department_id=1)

    app.dependency_overrides[get_required_user] = fake_required_user
    from yuxi.modules.knowledge.services import access, documents

    monkeypatch.setattr(access, "check_accessible", fake_kb.check_accessible)
    monkeypatch.setattr(documents, "get_knowledge_base_document_support", fake_kb.get_knowledge_base_document_support)
    monkeypatch.setattr(documents, "list_document_files", fake_kb.list_document_files)
    return TestClient(app)


def test_workspace_knowledge_tree_uses_paginated_document_listing(monkeypatch):
    fake_kb = FakeKnowledgeBase()
    client = _build_client(monkeypatch, fake_kb)

    response = client.get(
        "/api/workspace/knowledge/tree",
        params={
            "kb_id": "kb_1",
            "parent_id": "folder_1",
            "page": 2,
            "page_size": 100,
            "files_only": True,
        },
    )

    assert response.status_code == 200, response.text
    assert fake_kb.list_calls == [
        {
            "kb_id": "kb_1",
            "parent_id": "folder_1",
            "page": 2,
            "page_size": 100,
            "recursive": False,
            "files_only": True,
            "include_stats": False,
        }
    ]
    payload = response.json()
    assert payload["page"] == 2
    assert payload["total"] == 2
    assert payload["entries"][0]["path"] == "/knowledge/kb_1/folder/folder_1/"
    assert payload["entries"][1]["path"] == "/knowledge/kb_1/file/file_1"
    assert payload["entries"][1]["size"] == 2048
    assert payload["entries"][1]["modified_at"] == "2026-06-20T01:00:00Z"


def test_workspace_knowledge_tree_rejects_non_document_kb(monkeypatch):
    client = _build_client(monkeypatch, FakeKnowledgeBase(supports=False))

    response = client.get("/api/workspace/knowledge/tree", params={"kb_id": "kb_1"})

    assert response.status_code == 501
    assert "不支持文件浏览" in response.json()["detail"]


def test_workspace_knowledge_file_calls_knowledge_preview_directly(monkeypatch):
    client = _build_client(monkeypatch, FakeKnowledgeBase())
    calls = []

    async def read_preview(*, kb_id: str, file_id: str, office_render: str) -> dict:
        calls.append((kb_id, file_id, office_render))
        return {
            "source": "knowledge",
            "kb_id": kb_id,
            "file_id": file_id,
            "content": "preview",
            "preview_type": "text",
            "supported": True,
        }

    monkeypatch.setattr(preview, "read_knowledge_file_preview", read_preview)

    response = client.get(
        "/api/workspace/knowledge/file",
        params={"kb_id": "kb_1", "file_id": "file_1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "preview"
    assert calls == [("kb_1", "file_1", "source")]
