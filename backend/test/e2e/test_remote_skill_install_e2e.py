"""没有聊天 Run 的远程发现、草稿与个人安装验收。"""

from __future__ import annotations

import shutil
import uuid

import httpx
import pytest

from test.support.postgres import postgres_dsn
from yuxi.modules.agents.runtime.sandbox.container import SandboxRunnerClient
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.paths import user_workspace_dir, workspace_uid_dirname

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.fixture
def remote_skill_user():
    """只创建并回收本例的登录身份与个人文件。"""
    import psycopg

    uid = f"pytest-remote-{uuid.uuid4().hex}"
    password = f"Test!{uuid.uuid4().hex}"
    with psycopg.connect(postgres_dsn()) as conn:
        department_id = conn.execute("INSERT INTO departments(name,created_at) VALUES (%s,now()) RETURNING id", (uid,)).fetchone()[0]
        conn.execute(
            "INSERT INTO users(uid,username,password_hash,role,user_kind,login_failed_count,is_deleted,created_at,department_id) "
            "VALUES (%s,%s,%s,'user','human',0,0,now(),%s)",
            (uid, uid, AuthUtils.hash_password(password), department_id),
        )
    try:
        yield uid, password
    finally:
        with psycopg.connect(postgres_dsn()) as conn:
            conn.execute("DELETE FROM users WHERE uid=%s", (uid,))
            conn.execute("DELETE FROM departments WHERE id=%s", (department_id,))
        shutil.rmtree(user_workspace_dir(uid).parent, ignore_errors=True)


@pytest.mark.parametrize(
    ("source", "names"),
    [
        (
            "https://modelscope.cn/collections/MiniMax/MiniMax-Office-skills",
            {"minimax-docx", "minimax-pdf", "minimax-xlsx", "pptx-generator"},
        ),
        ("mattpocock/skills", {"grill-me"}),
    ],
)
async def test_remote_skill_install_without_chat_run(e2e_client: httpx.AsyncClient, remote_skill_user, source, names):
    """真实来源经 HTTP 安装后，列表、预览和个人文件字节一致。"""
    uid, password = remote_skill_user
    login = await e2e_client.post("/api/auth/token", data={"username": uid, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    listed = await e2e_client.post("/api/skills/remote/list", headers=headers, json={"source": source})
    assert listed.status_code == 200, listed.text
    assert names <= {item["name"] for item in listed.json()["data"]}

    prepared = await e2e_client.post("/api/skills/remote/prepare", headers=headers, json={"source": source, "skills": sorted(names)})
    assert prepared.status_code == 200, prepared.text
    draft = prepared.json()["data"]
    try:
        assert draft["failures"] == [], draft
        assert names == {item["slug"] for item in draft["items"]}, draft
        confirmed = await e2e_client.post(
            f"/api/skills/personal/install-drafts/{draft['draft_id']}/confirm",
            headers=headers,
            json={"slugs": sorted(names)},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert all(item["success"] for item in confirmed.json()["data"]), confirmed.text

        cards = await e2e_client.get("/api/skills", headers=headers)
        assert cards.status_code == 200, cards.text
        assert names <= {item["slug"] for item in cards.json()["data"] if item["source_scope"] == "personal"}
        for name in names:
            preview = await e2e_client.get(f"/api/skills/personal/{name}/file", headers=headers, params={"path": "SKILL.md"})
            assert preview.status_code == 200, preview.text
            content = preview.json()["data"]["content"]
            installed = user_workspace_dir(uid) / "agents" / "skills" / name / "SKILL.md"
            assert installed.read_text(encoding="utf-8") == content
            assert f"name: {name}" in content
        assert list((user_workspace_dir(uid) / ".remote-skills").iterdir()) == []
        containers = SandboxRunnerClient().list()
        assert not any(
            item.get("labels", {}).get("yuxi.purpose") == "skill-fetch" and item["labels"].get("yuxi.uid") == workspace_uid_dirname(uid)
            for item in containers
        )
    finally:
        response = await e2e_client.delete(f"/api/skills/install-drafts/{draft['draft_id']}", headers=headers)
        assert response.status_code in {200, 404}, response.text
