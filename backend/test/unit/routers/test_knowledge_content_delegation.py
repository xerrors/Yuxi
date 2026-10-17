from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import knowledge_router
from server.utils.auth_middleware import get_required_user
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.read_models import KnowledgeBaseDetail


def _database():
    return KnowledgeBaseDetail(
        kb_id="kb-1",
        name="授权库",
        description="",
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={
            "dify_token": "secret",
            "chunk_size": 100,
            "chunk_parser_config": {"chunk_token_num": 2048},
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {
                    "model_spec": "test/model",
                    "model_params": {
                        "temperature": 0.2,
                        "max_tokens": 1000,
                        "max_completion_tokens": 800,
                        "max_output_tokens": 600,
                        "api_key": "nested-model-secret",
                        "default_headers": {
                            "Authorization": "Bearer default-secret",
                            "X-Gateway-Credential": "custom-default-secret",
                        },
                        "extra_headers": {"X-Gateway-Credential": "custom-extra-secret"},
                        "extra_body": {"routing": [{"accessToken": "routing-secret", "region": "example"}]},
                    },
                },
            },
        },
        created_by="owner",
        created_at=None,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["editor", "reader"]},
            "manage_scope": {"access_level": "user", "user_uids": ["editor"]},
        },
    )


def _client(monkeypatch, uid="editor", role="user", database=None):
    database = database or _database()

    async def fake_database_info(*_args, **_kwargs):
        return database

    async def fake_support(*_args):
        return database, True

    async def fake_required_user():
        return SimpleNamespace(uid=uid, role=role, department_id=1)

    app = FastAPI()
    app.include_router(knowledge_router.knowledge)
    app.dependency_overrides[get_required_user] = fake_required_user
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_document_support", fake_support)
    return TestClient(app)


@pytest.mark.parametrize("endpoint", ["detail", "accessible", "graph-status"])
@pytest.mark.parametrize(
    ("uid", "role", "can_manage"),
    [("reader", "user", False), ("editor", "user", False), ("reader", "admin", False), ("editor", "admin", True)],
)
def test_read_projections_remove_nested_credentials_without_changing_stored_configuration(
    monkeypatch, endpoint, uid, role, can_manage
):
    """三个读取出口保护嵌套模型凭据，管理员仍可编辑原始配置。"""
    database = _database()
    original_params = deepcopy(database.additional_params)
    client = _client(monkeypatch, uid, role, database)

    async def list_databases(_uid):
        return [database]

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_databases_by_uid", list_databases)
    service = MilvusGraphService(
        kb_repo=SimpleNamespace(get_by_kb_id=AsyncMock(return_value=database)),
        chunk_repo=SimpleNamespace(
            count_by_kb_id=AsyncMock(return_value=0),
            count_graph_pending_by_kb_id=AsyncMock(return_value=0),
            count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
            count_graph_structure_indexed_by_kb_id=AsyncMock(return_value=0),
            count_graph_extraction_statuses_by_kb_id=AsyncMock(return_value={}),
        ),
        graph_repo=SimpleNamespace(
            count_by_kb_id=AsyncMock(return_value=(0, 0)),
            count_vector_statuses_by_kb_id=AsyncMock(return_value={}),
        ),
    )
    monkeypatch.setattr(knowledge_router, "MilvusGraphService", lambda: service)
    monkeypatch.setattr(knowledge_router, "tasker", SimpleNamespace(find_task_by_payload=AsyncMock(return_value=None)))
    path = {
        "detail": "/knowledge/databases/kb-1",
        "accessible": "/knowledge/databases/accessible",
        "graph-status": "/knowledge/databases/kb-1/graph-build/status",
    }[endpoint]

    response = client.get(path)

    assert response.status_code == 200, response.text
    body = response.json()
    if endpoint == "graph-status":
        projections = [body["config"]]
    else:
        if endpoint == "accessible":
            body = body["databases"][0]
        for field in ("additional_params", "metadata"):
            assert body[field]["chunk_parser_config"] == {"chunk_token_num": 2048}
        projections = [body[field]["graph_build_config"] for field in ("additional_params", "metadata")]
    for projection in projections:
        model_params = projection["extractor_options"]["model_params"]
        assert model_params["temperature"] == 0.2
        assert model_params["max_tokens"] == 1000
        assert model_params["max_completion_tokens"] == 800
        assert model_params["max_output_tokens"] == 600
        if can_manage and endpoint != "accessible":
            assert model_params == original_params["graph_build_config"]["extractor_options"]["model_params"]
        else:
            assert "api_key" not in model_params
            assert "default_headers" not in model_params
            assert "extra_headers" not in model_params
            assert model_params["extra_body"]["routing"] == [{"region": "example"}]
    assert database.additional_params == original_params
    runtime_options = service._runtime_extractor_options(database.additional_params["graph_build_config"])
    assert runtime_options["model_params"] == original_params["graph_build_config"]["extractor_options"]["model_params"]


@pytest.mark.parametrize(("uid", "can_edit"), [("editor", True), ("reader", False)])
def test_delegated_users_read_detail_without_configuration_credentials(monkeypatch, uid, can_edit):
    response = _client(monkeypatch, uid).get("/knowledge/databases/kb-1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_edit_documents"] is can_edit
    assert body["can_manage"] is False
    assert "dify_token" not in body["additional_params"]
    assert "dify_token" not in body["metadata"]


@pytest.mark.parametrize(("uid", "status"), [("editor", 200), ("reader", 403), ("unshared", 403)])
def test_only_delegated_editor_can_create_folder(monkeypatch, uid, status):
    calls = []

    async def fake_create_folder(kb_id, name, parent_id, operator_id):
        calls.append((kb_id, name, parent_id, operator_id))
        return {"file_id": "folder-1"}

    monkeypatch.setattr(knowledge_router.knowledge_base, "create_folder", fake_create_folder)
    response = _client(monkeypatch, uid).post(
        "/knowledge/databases/kb-1/folders", json={"folder_name": "测试", "parent_id": None}
    )

    assert response.status_code == status, response.text
    assert calls == ([("kb-1", "测试", None, "editor")] if status == 200 else [])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("delete", "/knowledge/databases/kb-1", None),
        ("put", "/knowledge/databases/kb-1", {"name": "changed", "description": "changed"}),
        ("post", "/knowledge/databases/kb-1/graph-build/reset", {}),
        ("put", "/knowledge/databases/kb-1/query-params", {}),
        ("post", "/knowledge/databases/kb-1/mindmap/generate", {"file_ids": []}),
    ],
)
def test_delegated_editor_cannot_perform_database_administration(monkeypatch, method, path, body):
    response = _client(monkeypatch).request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员权限"


def test_unshared_user_cannot_read_database(monkeypatch):
    response = _client(monkeypatch, "unshared").get("/knowledge/databases/kb-1")

    assert response.status_code == 403


@pytest.mark.parametrize("route", ["/documents", "/documents/add"])
@pytest.mark.parametrize("override_path", [False, True])
def test_delegated_editor_cannot_import_another_database_object(monkeypatch, route, override_path):
    item = "minio://knowledgebases/kb-1/upload/document.txt"
    source = "minio://knowledgebases/other-kb/upload/private.txt"
    params = {"content_hashes": {item: "hash"}}
    if override_path:
        params["_preprocessed_map"] = {item: {"content_hash": "hash", "path": source}}
    else:
        item = source
        params = {"content_hashes": {item: "hash"}}

    response = _client(monkeypatch).post(f"/knowledge/databases/kb-1{route}", json={"items": [item], "params": params})

    assert response.status_code == 403, response.text
    assert "该知识库" in response.json()["detail"]


@pytest.mark.parametrize("route", ["/files/upload", "/files/fetch-url"])
def test_ordinary_user_cannot_upload_without_a_database(monkeypatch, route):
    client = _client(monkeypatch)
    if route.endswith("upload"):
        response = client.post(f"/knowledge{route}", files={"file": ("note.txt", b"note", "text/plain")})
    else:
        response = client.post(f"/knowledge{route}", json={"url": "https://example.com"})

    assert response.status_code == 403
    assert ("知识库" if route.endswith("upload") else "管理员") in response.json()["detail"]


@pytest.mark.parametrize(
    "source",
    [
        "minio://other-bucket/kb-1/upload/private.txt",
        "minio://knowledgebases/kb-1/upload/../private.txt",
        "minio://knowledgebases/kb-1/upload/%2e%2e/private.txt",
        "minio://knowledgebases/kb-1/upload/..%5cprivate.txt",
    ],
)
def test_delegated_editor_cannot_import_outside_the_upload_prefix(monkeypatch, source):
    response = _client(monkeypatch).post(
        "/knowledge/databases/kb-1/documents/add",
        json={"items": [source], "params": {"content_hashes": {source: "hash"}}},
    )

    assert response.status_code == 403
    assert "该知识库" in response.json()["detail"]


@pytest.mark.parametrize("filename", ["../../private.txt", "..\\..\\private.txt"])
def test_delegated_upload_rejects_directory_filename_before_file_read_or_storage(monkeypatch, filename):
    calls = []

    async def unexpected_side_effect(*_args, **_kwargs):
        calls.append("side effect")
        raise AssertionError("非法文件名不能读取或上传内容")

    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", unexpected_side_effect)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", unexpected_side_effect)
    response = _client(monkeypatch).post(
        "/knowledge/files/upload?kb_id=kb-1", files={"file": (filename, b"private", "text/plain")}
    )

    assert response.status_code == 400
    assert "目录路径" in response.json()["detail"]
    assert not calls


def test_delegated_url_import_requires_admin_before_fetch_or_storage(monkeypatch):
    client = _client(monkeypatch)
    calls = []

    async def unexpected_side_effect(*_args, **_kwargs):
        calls.append("side effect")
        raise AssertionError("普通内容编辑者不能抓取或上传 URL 文档")

    monkeypatch.setattr(knowledge_router, "fetch_url_content", unexpected_side_effect)
    monkeypatch.setattr(knowledge_router, "get_minio_client", lambda: calls.append("storage"))
    response = client.post("/knowledge/files/fetch-url", json={"url": "https://example.com", "kb_id": "kb-1"})

    assert response.status_code == 403
    assert "管理员" in response.json()["detail"]
    assert not calls


def test_administrator_still_receives_database_management_permission(monkeypatch):
    response = _client(monkeypatch, "editor", "admin").get("/knowledge/databases/kb-1")

    assert response.status_code == 200
    assert response.json()["can_manage"] is True
    assert response.json()["can_edit_documents"] is True
    assert response.json()["additional_params"]["dify_token"] == "secret"
