from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import yuxi.api.dependencies.knowledge as knowledge_permissions
import yuxi.api.routers.knowledge.evaluation as knowledge_eval_router
import yuxi.api.routers.knowledge.management as knowledge_router
from yuxi.api.dependencies.auth import get_required_user
from yuxi.api.responses.knowledge import serialize_knowledge_base
from yuxi.modules.knowledge.read_models import KnowledgeBaseSummary
from yuxi.modules.identity.permissions import ResourcePermission


def test_serialize_knowledge_base_redacts_credentials_from_compatibility_fields():
    knowledge_base = KnowledgeBaseSummary(
        kb_id="kb-1",
        name="知识库",
        description=None,
        kb_type="dify",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={"dify_token": "secret", "chunk_size": 100},
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by=None,
        created_at=None,
    )

    response = serialize_knowledge_base(knowledge_base, redact_secrets=True)

    assert response["additional_params"]["chunk_size"] == 100
    assert response["metadata"]["chunk_size"] == 100
    assert "dify_token" not in response["additional_params"]
    assert "dify_token" not in response["metadata"]


@pytest.mark.parametrize(("uid", "role", "can_read"), [("admin-1", "admin", True), ("other-user", "user", False)])
@pytest.mark.asyncio
async def test_non_manager_cannot_manage_global_read_knowledge_base(monkeypatch, uid, role, can_read):
    knowledge_base = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        },
    }

    async def fake_get_knowledge_base_info(_kb_id):
        return knowledge_base

    monkeypatch.setattr(knowledge_router.knowledge_base_manager, "get_knowledge_base_info", fake_get_knowledge_base_info)
    user = SimpleNamespace(uid=uid, role=role, department_id=2)

    if can_read:
        assert await knowledge_router.require_knowledge_base_read("kb-1", user) is user

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.require_knowledge_base_manage("kb-1", user)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_query_parameter_routes_apply_knowledge_base_acl(monkeypatch):
    knowledge_base = {
        "created_by": "owner",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["admin-1"]},
            "manage_scope": None,
        },
    }

    async def fake_get_knowledge_base_info(_kb_id):
        return knowledge_base

    monkeypatch.setattr(knowledge_router.knowledge_base_manager, "get_knowledge_base_info", fake_get_knowledge_base_info)
    readonly_admin = SimpleNamespace(uid="admin-1", role="admin", department_id=2)

    assert await knowledge_router.require_knowledge_base_read("kb-1", readonly_admin) is readonly_admin

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.require_knowledge_base_read("kb-1", SimpleNamespace(uid="admin-2", role="admin", department_id=2))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_dataset_only_manage_route_checks_the_dataset_knowledge_base(monkeypatch):
    calls = []

    async def fake_get_dataset(_service, _dataset_id):
        return SimpleNamespace(kb_id="kb-1")

    async def fake_ensure_permission(kb_id, current_user, required):
        calls.append((kb_id, current_user, required))
        return {}

    monkeypatch.setattr(knowledge_eval_router.EvaluationService, "get_dataset", fake_get_dataset)
    monkeypatch.setattr(knowledge_eval_router, "ensure_knowledge_base_permission", fake_ensure_permission)
    admin = SimpleNamespace(uid="admin-1", role="admin", department_id=1)

    result = await knowledge_eval_router.require_evaluation_dataset_manage("dataset-1", admin)

    assert result is admin
    assert calls == [("kb-1", admin, ResourcePermission.MANAGE)]


@pytest.mark.asyncio
async def test_dataset_manage_route_rejects_admin_without_manage_permission(monkeypatch):
    async def fake_get_dataset(_service, _dataset_id):
        return SimpleNamespace(kb_id="kb-1")

    async def fake_get_knowledge_base_info(_kb_id):
        return {
            "created_by": "owner",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
        }

    monkeypatch.setattr(knowledge_eval_router.EvaluationService, "get_dataset", fake_get_dataset)
    monkeypatch.setattr(knowledge_permissions.knowledge_base, "get_knowledge_base_info", fake_get_knowledge_base_info)
    admin = SimpleNamespace(uid="other-admin", role="admin", department_id=1)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_eval_router.require_evaluation_dataset_manage("dataset-1", admin)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_dataset_download_rejects_admin_outside_read_scope(monkeypatch):
    """非超管且不在知识库读取范围内的管理员不能导出评估数据集。"""

    async def fake_get_dataset(_service, _dataset_id):
        return SimpleNamespace(kb_id="kb-1")

    async def fake_get_knowledge_base_info(_kb_id):
        return {
            "created_by": "owner",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["owner"]},
                "manage_scope": None,
            },
        }

    monkeypatch.setattr(knowledge_eval_router.EvaluationService, "get_dataset", fake_get_dataset)
    monkeypatch.setattr(knowledge_permissions.knowledge_base, "get_knowledge_base_info", fake_get_knowledge_base_info)
    outsider = SimpleNamespace(uid="other-admin", role="admin", department_id=1)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_eval_router.require_evaluation_dataset_read("dataset-1", outsider)

    assert exc_info.value.status_code == 403
    download = knowledge_eval_router.download_evaluation_dataset
    assert download.__defaults__[0].dependency is knowledge_eval_router.require_evaluation_dataset_read


def test_evaluation_routes_require_admin_role():
    app = FastAPI()
    app.include_router(knowledge_eval_router.evaluation)

    async def fake_required_user():
        return SimpleNamespace(uid="user-1", role="user", department_id=1)

    app.dependency_overrides[get_required_user] = fake_required_user

    response = TestClient(app).get("/evaluation/knowledge-bases/kb-1/datasets")

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员权限"
