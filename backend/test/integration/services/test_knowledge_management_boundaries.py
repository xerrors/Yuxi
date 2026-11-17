"""管理入口以真实 HTTP、权限依赖和 PostgreSQL 验证领域服务边界。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.module_audit import serve_http
from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.auth import get_admin_user, get_required_user
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.knowledge import management as router
from yuxi.api.routers.workspace.workspace import workspace_knowledge
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.migrations.schema import create_business_tables, create_knowledge_tables
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile, KnowledgeProjectionOutbox
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.services import statistics
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def management_http(monkeypatch):
    """身份由 fixture 提供，权限、类型、事务与 HTTP 响应均走真实实现。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_kb_management")
    await create_knowledge_tables(manager)
    await create_business_tables(manager)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction():
        """保留生产 repository 的提交边界。"""
        async with sessions() as session, session.begin():
            yield session

    monkeypatch.setattr(pg_manager, "get_async_session_context", transaction)
    user = User(uid="owner", username="owner", password_hash="fixture", role="admin")
    async with sessions.begin() as session:
        session.add(user)
        session.add(User(uid="reader", username="reader", password_hash="fixture", role="admin"))
        session.add(
            KnowledgeBase(
                kb_id=schema,
                name="Original",
                kb_type="milvus",
                created_by="owner",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
            )
        )
    app = FastAPI()
    app.include_router(router.knowledge, prefix="/api")
    app.include_router(workspace_knowledge, prefix="/api")
    app.add_exception_handler(DomainError, domain_error_handler)
    app.dependency_overrides[get_admin_user] = lambda: user
    app.dependency_overrides[get_required_user] = lambda: user
    try:
        async with serve_http(app) as client:
            yield client, sessions, user, schema
    finally:
        await close_async_redis_client()
        await drop_isolated_schema(schema, admin, engine)


async def test_management_metadata_create_update_visible_list_and_delete(management_http, monkeypatch):
    """创建更新与软删除的响应来自持久行，权限列表保留读者的管理拒绝边界。"""
    client, sessions, user, kb_id = management_http
    monkeypatch.setattr(
        "yuxi.modules.models.providers.cache.model_cache.get_model_info", lambda _spec: SimpleNamespace(model_type="embedding")
    )
    monkeypatch.setattr("yuxi.modules.models.providers.cache.model_cache.get_all_specs", lambda _kind: [])
    response = await client.post(
        "/api/knowledge/knowledge-bases",
        json={
            "name": "Created",
            "description": "description",
            "embedding_model_spec": "fixture:embedding",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["reader"], "department_ids": []},
                "manage_scope": None,
            },
        },
    )
    assert response.status_code == 200, response.text
    created_id = response.json()["kb_id"]
    async with sessions() as session:
        record = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == created_id))
        assert record.name == "Created" and record.created_by == "owner"
        assert record.share_config["read_scope"]["user_uids"] == ["owner", "reader"]
    conflict = await client.post(
        "/api/knowledge/knowledge-bases", json={"name": "Created", "description": "other", "embedding_model_spec": "fixture:embedding"}
    )
    assert conflict.status_code == 409
    updated = await client.put(f"/api/knowledge/knowledge-bases/{created_id}", json={"name": "Renamed", "description": "updated"})
    assert updated.status_code == 200 and updated.json()["knowledge_base"]["name"] == "Renamed", updated.text
    user.uid = "reader"
    listed = await client.get("/api/knowledge/knowledge-bases")
    assert listed.status_code == 200, listed.text
    item = next(item for item in listed.json()["knowledge_bases"] if item["kb_id"] == created_id)
    assert item["name"] == "Renamed" and item["effective_permission"] == "read"
    denied = await client.put(f"/api/knowledge/knowledge-bases/{created_id}", json={"name": "Stolen", "description": "x"})
    assert denied.status_code == 403
    user.uid = "owner"
    deleted = await client.delete(f"/api/knowledge/knowledge-bases/{created_id}")
    assert deleted.status_code == 200, deleted.text
    missing = await client.get(f"/api/knowledge/knowledge-bases/{created_id}")
    assert missing.status_code == 404
    async with sessions() as session:
        record = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == created_id))
        assert record.name == "Renamed" and record.deleted_at is not None
        events = list(await session.scalars(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.kb_id == created_id)))
        assert len(events) == 1 and events[0].operation == "knowledge_base_deleted" and events[0].status == "pending"


async def test_directory_list_search_and_cross_library_rejection(management_http):
    """目录身份在改名移动后稳定；分页、搜索和跨库拒绝可由真实行独立确认。"""
    client, sessions, user, kb_id = management_http
    base = f"/api/knowledge/knowledge-bases/{kb_id}"
    created = await client.post(base + "/folders", json={"folder_name": "Reports"})
    assert created.status_code == 200, created.text
    folder = created.json()["file_id"]
    renamed = await client.put(base + f"/folders/{folder}/rename", json={"folder_name": "资料"})
    assert renamed.status_code == 200 and renamed.json()["file_id"] == folder
    repo = KnowledgeFileRepository()
    await repo.upsert("report", {"kb_id": kb_id, "filename": "Report.md", "status": "indexed", "created_by": "owner"})
    await repo.upsert("draft", {"kb_id": kb_id, "filename": "Draft.md", "status": "uploaded", "created_by": "owner"})
    moved = await client.put(base + "/documents/report/move", json={"new_parent_id": folder})
    assert moved.status_code == 200 and moved.json()["parent_id"] == folder
    listed = await client.get(base + "/documents", params={"parent_id": folder, "status": "indexed", "page_size": 1})
    assert listed.status_code == 200, listed.text
    assert [item["file_id"] for item in listed.json()["items"]] == ["report"]
    assert listed.json()["total"] == 1 and not listed.json()["has_more"]
    found = await client.get(base + "/documents/search", params={"query": " REPORT "})
    assert found.status_code == 200, found.text
    assert [(item["file_id"], item["parent_id"]) for item in found.json()["files"]] == [("report", folder)]
    async with sessions.begin() as session:
        session.add(KnowledgeBase(kb_id="foreign-kb", name="Foreign", kb_type="milvus"))
        await session.flush()
        session.add(KnowledgeFile(file_id="foreign-folder", kb_id="foreign-kb", filename="Other", is_folder=True))
    rejected = await client.put(base + "/documents/report/move", json={"new_parent_id": "foreign-folder"})
    assert rejected.status_code == 404, rejected.text
    foreign_listing = await client.get(base + "/documents", params={"parent_id": "foreign-folder"})
    assert foreign_listing.status_code == 400
    duplicate = await client.post(base + "/folders", json={"folder_name": "资料"})
    assert duplicate.status_code == 409
    cycle = await client.put(base + f"/documents/{folder}/move", json={"new_parent_id": folder})
    assert cycle.status_code == 400
    async with sessions() as session:
        report = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "report"))
        assert report.parent_id == folder
        assert (
            await session.scalar(
                select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.is_folder.is_(True))
            )
            == 1
        )
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
        assert kb.additional_params["stats"]["folder_count"] == 1


@pytest.mark.parametrize("rejection", ["permission", "read_only", "stats_failure"])
async def test_folder_failure_reports_only_committed_facts(management_http, monkeypatch, rejection):
    """权限和类型拒绝无新节点；已提交创建后的统计失败不伪装成功或回滚。"""
    client, sessions, user, kb_id = management_http
    if rejection == "permission":
        user.uid = "reader"
    elif rejection == "read_only":
        async with sessions.begin() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            kb.kb_type = "dify"
            kb.additional_params = {"dify_api_url": "https://example.test/v1", "dify_token": "fixture", "dify_dataset_id": "fixture"}
    else:
        monkeypatch.setattr(statistics, "refresh_stats", AsyncMock(side_effect=RuntimeError("private diagnostic")))
    response = await client.post(f"/api/knowledge/knowledge-bases/{kb_id}/folders", json={"folder_name": "New"})
    assert response.status_code == {"permission": 403, "read_only": 400, "stats_failure": 500}[rejection], response.text
    assert "private diagnostic" not in response.text
    async with sessions() as session:
        rows = list(await session.scalars(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id)))
        assert [row.filename for row in rows] == (["New"] if rejection == "stats_failure" else [])
        assert await session.scalar(select(func.count()).select_from(KnowledgeProjectionOutbox)) == 0


async def test_workspace_download_keeps_read_only_type_boundary(management_http, monkeypatch):
    """工作区下载保留原类型准入，不把限制扩展到内部正文读取函数。"""
    from yuxi.modules.knowledge.services import documents

    client, sessions, _user, kb_id = management_http
    async with sessions.begin() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
        kb.kb_type = "dify"
        kb.additional_params = {"dify_api_url": "https://example.test/v1", "dify_token": "fixture", "dify_dataset_id": "fixture"}
    monkeypatch.setattr(documents, "get_file_download", AsyncMock(side_effect=AssertionError("unsupported type reached content reader")))
    response = await client.get("/api/workspace/knowledge/download", params={"kb_id": kb_id, "file_id": "remote-document"})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Original 只支持检索，不支持文件下载"
