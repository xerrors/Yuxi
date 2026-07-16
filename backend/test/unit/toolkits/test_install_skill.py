from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agents.skills import service as skill_service
from yuxi.agents.toolkits.buildin import install_skill as exported_install_skill

install_skill_module = importlib.import_module("yuxi.agents.toolkits.buildin.install_skill")
sandbox_backend_module = importlib.import_module("yuxi.agents.backends.sandbox")


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


def _runtime(**context_values):
    return SimpleNamespace(context=SimpleNamespace(**context_values))


@pytest.mark.asyncio
async def test_install_skill_from_sandbox_installs_as_current_user_private_skill(monkeypatch, tmp_path: Path):
    assert exported_install_skill.name == "install_skill"

    calls = {}
    db = SimpleNamespace()
    source_dir = tmp_path / "demo-skill"

    def prepare_skill_from_sandbox(source, thread_id, uid, staging_root):
        calls["prepare"] = {
            "source": source,
            "thread_id": thread_id,
            "uid": uid,
            "staging_root": staging_root,
        }
        return source_dir, "demo-skill"

    async def import_skill_dir(db_arg, **kwargs):
        calls["import"] = {"db": db_arg, **kwargs}
        return SimpleNamespace(slug="demo-skill")

    async def enable_skills(db_arg, thread_id, uid, skill_slugs):
        calls["enable"] = {"db": db_arg, "thread_id": thread_id, "uid": uid, "skill_slugs": skill_slugs}
        return True

    def sync_thread_readable_skills(thread_id, skills):
        calls["sync"] = {"thread_id": thread_id, "skills": skills}

    monkeypatch.setattr(
        install_skill_module,
        "_prepare_skill_from_sandbox",
        prepare_skill_from_sandbox,
    )
    monkeypatch.setattr(
        install_skill_module,
        "_enable_skills_in_current_config",
        enable_skills,
    )
    monkeypatch.setattr(
        install_skill_module.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(db),
    )
    monkeypatch.setattr(skill_service, "import_skill_dir", import_skill_dir)
    monkeypatch.setattr(skill_service, "sync_thread_readable_skills", sync_thread_readable_skills)

    result = await install_skill_module._run_install_task(
        " /home/gem/user-data/workspace/demo-skill ",
        _runtime(uid="normal-user", thread_id="thread-1", skills=["existing-skill"]),
        "tool-1",
    )

    assert result.update["activated_skills"] == ["demo-skill"]
    assert "成功安装并激活技能" in result.update["messages"][0].content
    assert calls["prepare"]["uid"] == "normal-user"
    assert calls["import"]["created_by"] == "normal-user"
    assert calls["import"]["share_config"] == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["normal-user"],
    }
    assert calls["prepare"]["source"] == "/home/gem/user-data/workspace/demo-skill"
    assert calls["enable"] == {
        "db": db,
        "thread_id": "thread-1",
        "uid": "normal-user",
        "skill_slugs": ["demo-skill"],
    }
    assert calls["sync"] == {"thread_id": "thread-1", "skills": ["existing-skill", "demo-skill"]}


@pytest.mark.asyncio
async def test_install_skill_rejects_subagent_runtime_before_install(monkeypatch):
    def fail_get_session():
        raise AssertionError("子智能体运行态不应访问数据库或执行安装")

    monkeypatch.setattr(
        install_skill_module.pg_manager,
        "get_async_session_context",
        fail_get_session,
    )

    result = await install_skill_module._run_install_task(
        "/home/gem/user-data/workspace/demo-skill",
        _runtime(uid="user-1", thread_id="child-thread", is_subagent_runtime=True),
        "tool-1",
    )

    assert "只能在主智能体中使用" in result.update["messages"][0].content
    assert "activated_skills" not in result.update


@pytest.mark.asyncio
async def test_install_skill_git_source_requires_skill_names():
    result = await install_skill_module._run_install_task(
        "owner/repo",
        _runtime(uid="user-1", thread_id="thread-1"),
        "tool-1",
    )

    assert "必须通过 skill_names 指定技能名称" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_install_skill_rejects_empty_source():
    result = await install_skill_module._run_install_task(
        " ",
        _runtime(uid="user-1", thread_id="thread-1"),
        "tool-1",
    )

    assert "Skill 来源不能为空" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_enable_skills_updates_current_user_owned_agent_config(monkeypatch):
    conv = SimpleNamespace(uid="user-1", agent_id="agent-1")
    agent = SimpleNamespace(
        created_by="user-1",
        config_json={"context": {"skills": ["existing-skill"], "model": "provider:model"}},
    )
    calls = {}

    class FakeConversationRepository:
        def __init__(self, db):
            self.db = db

        async def get_conversation_by_thread_id(self, thread_id):
            calls["thread_id"] = thread_id
            return conv

    class FakeAgentRepository:
        def __init__(self, db):
            self.db = db

        async def get_by_slug(self, slug):
            calls["agent_slug"] = slug
            return agent

        async def update(self, agent_arg, **kwargs):
            calls["update"] = {"agent": agent_arg, **kwargs}
            return agent_arg

    monkeypatch.setattr(install_skill_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(install_skill_module, "AgentRepository", FakeAgentRepository)

    result = await install_skill_module._enable_skills_in_current_config(
        SimpleNamespace(),
        "thread-1",
        "user-1",
        ["existing-skill", "new-skill"],
    )

    assert result is True
    assert calls["thread_id"] == "thread-1"
    assert calls["agent_slug"] == "agent-1"
    assert calls["update"]["updated_by"] == "user-1"
    assert calls["update"]["config_json"] == {
        "context": {"skills": ["existing-skill", "new-skill"], "model": "provider:model"}
    }


@pytest.mark.asyncio
async def test_enable_skills_does_not_update_agent_not_owned_by_current_user(monkeypatch):
    conv = SimpleNamespace(uid="user-1", agent_id="shared-agent")
    agent = SimpleNamespace(created_by="admin", config_json={"context": {}})
    calls = {}

    class FakeConversationRepository:
        def __init__(self, db):
            self.db = db

        async def get_conversation_by_thread_id(self, _thread_id):
            return conv

    class FakeAgentRepository:
        def __init__(self, db):
            self.db = db

        async def get_by_slug(self, _slug):
            return agent

        async def update(self, *_args, **_kwargs):
            calls["updated"] = True

    monkeypatch.setattr(install_skill_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(install_skill_module, "AgentRepository", FakeAgentRepository)

    result = await install_skill_module._enable_skills_in_current_config(
        SimpleNamespace(),
        "thread-1",
        "user-1",
        ["new-skill"],
    )

    assert result is False
    assert "updated" not in calls


@pytest.mark.asyncio
async def test_enable_skills_does_not_update_mismatched_runtime_uid(monkeypatch):
    conv = SimpleNamespace(uid="user-1", agent_id="agent-1")
    calls = {}

    class FakeConversationRepository:
        def __init__(self, db):
            self.db = db

        async def get_conversation_by_thread_id(self, _thread_id):
            return conv

    class FakeAgentRepository:
        def __init__(self, db):
            self.db = db

        async def get_by_slug(self, *_args):
            calls["loaded_agent"] = True

    monkeypatch.setattr(install_skill_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(install_skill_module, "AgentRepository", FakeAgentRepository)

    result = await install_skill_module._enable_skills_in_current_config(
        SimpleNamespace(),
        "thread-1",
        "other-user",
        ["new-skill"],
    )

    assert result is False
    assert "loaded_agent" not in calls


def test_prepare_skill_invalid_virtual_path_does_not_fallback_to_sandbox(monkeypatch, tmp_path: Path):
    calls = {}

    def resolve_virtual_path(*_args, **_kwargs):
        raise ValueError("path traversal detected")

    class FakeProvisionerSandboxBackend:
        def __init__(self, *_args, **_kwargs):
            calls["fallback"] = True

    monkeypatch.setattr(sandbox_backend_module, "resolve_virtual_path", resolve_virtual_path)
    monkeypatch.setattr(sandbox_backend_module, "ProvisionerSandboxBackend", FakeProvisionerSandboxBackend)
    monkeypatch.setattr(skill_service, "is_valid_skill_slug", lambda _slug: True)

    with pytest.raises(ValueError, match="path traversal detected"):
        install_skill_module._prepare_skill_from_sandbox(
            "/home/gem/user-data/workspace/demo-skill",
            "thread-1",
            "user-1",
            tmp_path,
        )

    assert "fallback" not in calls


def test_collect_sandbox_file_paths_rejects_more_than_1000_files():
    class FakeBackend:
        def ls(self, _remote_dir):
            return SimpleNamespace(
                error=None,
                entries=[{"path": f"/skill/file-{idx}.txt", "is_dir": False} for idx in range(1001)],
            )

    with pytest.raises(ValueError, match="最多 1000 个文件"):
        install_skill_module._collect_sandbox_file_paths(FakeBackend(), "/skill")
