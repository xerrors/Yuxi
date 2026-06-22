from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import workspace_router
from server.routers.workspace_router import workspace
from server.utils.auth_middleware import get_required_user
from yuxi.storage.postgres.models_business import User


class SupportsDocuments:
    supports_documents = True


class NoDocuments:
    supports_documents = False


class FakeKnowledgeBase:
    def __init__(self, *, supports: bool = True):
        self.supports = supports
        self.list_calls = []

    async def check_accessible(self, _user, _kb_id):
        return True

    async def get_database_info(self, kb_id):
        return {
            "kb_id": kb_id,
            "name": "知识库",
            "kb_type": "milvus" if self.supports else "dify",
        }

    async def list_document_files(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "items": [
                {
                    "file_id": "__virtual_folder__:root:资料/",
                    "filename": "资料",
                    "status": "done",
                    "is_folder": True,
                    "parent_id": None,
                    "is_virtual_folder": True,
                    "path_prefix": "资料/",
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
                    "is_virtual_folder": False,
                    "path_prefix": None,
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
            "path_prefix": kwargs["path_prefix"] or "",
        }


def _build_client(monkeypatch, fake_kb: FakeKnowledgeBase, kb_class) -> TestClient:
    app = FastAPI()
    app.include_router(workspace, prefix="/api")

    async def fake_required_user():
        return User(username="user", uid="user", password_hash="x", role="user", department_id=1)

    app.dependency_overrides[get_required_user] = fake_required_user
    monkeypatch.setattr(workspace_router, "knowledge_base", fake_kb)
    monkeypatch.setattr(
        workspace_router.KnowledgeBaseFactory,
        "get_kb_class",
        staticmethod(lambda _kb_type: kb_class),
    )
    return TestClient(app)


def test_workspace_knowledge_tree_uses_paginated_document_listing(monkeypatch):
    fake_kb = FakeKnowledgeBase()
    client = _build_client(monkeypatch, fake_kb, SupportsDocuments)

    response = client.get(
        "/api/workspace/knowledge/tree",
        params={
            "kb_id": "kb_1",
            "parent_id": "folder_1",
            "path_prefix": "资料/",
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
            "path_prefix": "资料/",
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
    assert payload["entries"][0]["is_virtual_folder"] is True
    assert payload["entries"][0]["path"] == "/knowledge/kb_1/virtual/%E8%B5%84%E6%96%99%2F"
    assert payload["entries"][1]["path"] == "/knowledge/kb_1/file/file_1"
    assert payload["entries"][1]["size"] == 2048
    assert payload["entries"][1]["modified_at"] == "2026-06-20T01:00:00Z"


def test_workspace_knowledge_tree_rejects_non_document_kb(monkeypatch):
    client = _build_client(monkeypatch, FakeKnowledgeBase(supports=False), NoDocuments)

    response = client.get("/api/workspace/knowledge/tree", params={"kb_id": "kb_1"})

    assert response.status_code == 501
    assert "不支持文件浏览" in response.json()["detail"]
