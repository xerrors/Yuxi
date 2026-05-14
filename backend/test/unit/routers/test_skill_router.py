from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers.skill_router import skills
from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.storage.postgres.models_business import Skill, User


def _build_app(*, allow_admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(skills, prefix="/api")

    async def fake_db():
        return None

    async def fake_admin_user():
        if not allow_admin:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return User(
            username="admin",
            user_id="admin",
            password_hash="x",
            role="admin",
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin_user
    return app


def test_list_skills_route_returns_data(monkeypatch):
    async def fake_list_skills(_db):
        return [
            Skill(
                slug="demo",
                name="demo",
                description="demo skill",
                dir_path="skills/demo",
            )
        ]

    monkeypatch.setattr("server.routers.skill_router.list_skills", fake_list_skills)

    app = _build_app()
    client = TestClient(app)
    resp = client.get("/api/system/skills")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["slug"] == "demo"


def test_skill_management_requires_admin():
    app = _build_app(allow_admin=False)
    client = TestClient(app)

    dependency_resp = client.get("/api/system/skills/dependency-options")
    assert dependency_resp.status_code == 403

    import_resp = client.post(
        "/api/system/skills/import",
        files={"file": ("demo.zip", b"not zip", "application/zip")},
    )
    assert import_resp.status_code == 403

    delete_resp = client.delete("/api/system/skills/demo")
    assert delete_resp.status_code == 403


def test_import_skill_route_accepts_skill_md(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_import_skill_zip(_db, *, filename, file_bytes, created_by):
        captured["filename"] = filename
        captured["file_bytes"] = file_bytes.decode("utf-8")
        captured["created_by"] = created_by
        return Skill(
            slug="demo",
            name="demo",
            description="demo skill",
            dir_path="skills/demo",
            created_by=created_by,
            updated_by=created_by,
        )

    monkeypatch.setattr("server.routers.skill_router.import_skill_zip", fake_import_skill_zip)

    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/system/skills/import",
        files={"file": ("SKILL.md", b"---\nname: demo\ndescription: demo skill\n---\n", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert captured["filename"] == "SKILL.md"
    assert "name: demo" in captured["file_bytes"]
    assert captured["created_by"] == "admin"


def test_update_skill_file_passes_operator(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_update_skill_file(_db, *, slug, relative_path, content, updated_by):
        captured["slug"] = slug
        captured["relative_path"] = relative_path
        captured["content"] = content
        captured["updated_by"] = updated_by

    monkeypatch.setattr("server.routers.skill_router.update_skill_file", fake_update_skill_file)

    app = _build_app()
    client = TestClient(app)

    resp = client.put(
        "/api/system/skills/demo/file",
        json={
            "path": "SKILL.md",
            "content": "---\nname: demo\ndescription: demo\n---\n# Demo\n",
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["slug"] == "demo"
    assert captured["relative_path"] == "SKILL.md"
    assert captured["updated_by"] == "admin"


def test_dependency_options_route(monkeypatch):
    async def fake_get_skill_dependency_options(_db):
        return {
            "tools": ["calculator"],
            "mcps": ["mcp-a"],
            "skills": ["demo"],
        }

    monkeypatch.setattr("server.routers.skill_router.get_skill_dependency_options", fake_get_skill_dependency_options)

    app = _build_app()
    client = TestClient(app)
    resp = client.get("/api/system/skills/dependency-options")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"]["tools"] == ["calculator"]


def test_update_skill_dependencies_route(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_update_skill_dependencies(
        _db,
        *,
        slug,
        tool_dependencies,
        mcp_dependencies,
        skill_dependencies,
        updated_by,
    ):
        captured["slug"] = slug
        captured["tool_dependencies"] = tool_dependencies
        captured["mcp_dependencies"] = mcp_dependencies
        captured["skill_dependencies"] = skill_dependencies
        captured["updated_by"] = updated_by
        return Skill(
            slug=slug,
            name=slug,
            description="demo",
            dir_path=f"skills/{slug}",
            tool_dependencies=tool_dependencies,
            mcp_dependencies=mcp_dependencies,
            skill_dependencies=skill_dependencies,
        )

    monkeypatch.setattr("server.routers.skill_router.update_skill_dependencies", fake_update_skill_dependencies)

    app = _build_app()
    client = TestClient(app)
    resp = client.put(
        "/api/system/skills/demo/dependencies",
        json={
            "tool_dependencies": ["calculator"],
            "mcp_dependencies": ["mcp-a"],
            "skill_dependencies": ["other-skill"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["slug"] == "demo"
    assert captured["tool_dependencies"] == ["calculator"]
    assert captured["mcp_dependencies"] == ["mcp-a"]
    assert captured["skill_dependencies"] == ["other-skill"]
    assert captured["updated_by"] == "admin"


def test_list_remote_skills_route(monkeypatch):
    async def fake_list_remote_skills(source: str):
        assert source == "anthropics/skills"
        return [{"name": "frontend-design", "description": "demo"}]

    monkeypatch.setattr("server.routers.skill_router.list_remote_skills", fake_list_remote_skills)

    app = _build_app()
    client = TestClient(app)
    resp = client.post("/api/system/skills/remote/list", json={"source": "anthropics/skills"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"] == [{"name": "frontend-design", "description": "demo"}]


def test_install_remote_skill_route(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_install_remote_skill(_db, *, source, skill, created_by):
        captured["source"] = source
        captured["skill"] = skill
        captured["created_by"] = created_by
        return Skill(
            slug="frontend-design",
            name="frontend-design",
            description="demo skill",
            dir_path="skills/frontend-design",
            created_by=created_by,
            updated_by=created_by,
        )

    monkeypatch.setattr("server.routers.skill_router.install_remote_skill", fake_install_remote_skill)

    app = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/api/system/skills/remote/install",
        json={"source": "anthropics/skills", "skill": "frontend-design"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"]["slug"] == "frontend-design"
    assert captured["source"] == "anthropics/skills"
    assert captured["skill"] == "frontend-design"
    assert captured["created_by"] == "admin"
