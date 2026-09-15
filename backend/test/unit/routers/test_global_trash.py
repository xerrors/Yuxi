"""统一知识回收站入口的权限和失败语义。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import knowledge_router
from server.utils.auth_middleware import get_required_user


def test_trash_databases_manage_only_and_errors(monkeypatch):
    """读权限、外部库不进入列表；存储故障不得伪装成空回收站。"""
    user = SimpleNamespace(uid="owner", role="admin", department_id=None)

    def database(kb_id, scope, kb_type="milvus"):
        return SimpleNamespace(
            kb_id=kb_id,
            name=kb_id,
            kb_type=kb_type,
            created_by="other",
            additional_params={},
            share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": scope},
        )

    global_scope = {"access_level": "global"}
    mock = AsyncMock(
        return_value=[
            database("managed", global_scope),
            database("readonly", None),
            database("external", global_scope, "dify"),
        ]
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_databases_by_uid", mock)
    app = FastAPI()
    app.include_router(knowledge_router.knowledge)
    app.dependency_overrides[get_required_user] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/knowledge/trash/databases")
        assert response.status_code == 200
        assert response.json() == {"databases": [{"kb_id": "managed", "name": "managed"}]}
        user.role = "user"
        assert client.get("/knowledge/trash/databases").json() == {"databases": []}
        mock.side_effect = RuntimeError("storage offline")
        assert client.get("/knowledge/trash/databases").status_code == 500
