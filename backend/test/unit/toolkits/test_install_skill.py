from __future__ import annotations

import importlib
import threading
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
    event_loop_thread_id = threading.get_ident()
    db = SimpleNamespace()
    source_dir = tmp_path / "demo-skill"

    def prepare_skill_from_sandbox(
        source,
        thread_id,
        uid,
        staging_root,
        workdir_relative_path,
        workdir_path,
    ):
        calls["prepare_thread_id"] = threading.get_ident()
        calls["prepare"] = {
            "source": source,
            "thread_id": thread_id,
            "uid": uid,
            "staging_root": staging_root,
            "workdir_relative_path": workdir_relative_path,
            "workdir_path": workdir_path,
        }
        return source_dir

    async def install_personal_skill_dir(uid, source_dir_arg, **kwargs):
        calls["install"] = {"uid": uid, "source_dir": source_dir_arg, **kwargs}
        return SimpleNamespace(
            slug="demo-skill",
            name="Demo Skill",
            description="demo description",
            source_scope="personal",
            tool_dependencies=[],
            mcp_dependencies=[],
            skill_dependencies=[],
            source_dir=source_dir,
        )

    async def enable_skills(db_arg, *, thread_id, uid, skill_slugs):
        calls["enable"] = {"db": db_arg, "thread_id": thread_id, "uid": uid, "skill_slugs": skill_slugs}
        return True

    monkeypatch.setattr(
        install_skill_module,
        "_prepare_skill_from_sandbox",
        prepare_skill_from_sandbox,
    )
    monkeypatch.setattr(
        skill_service,
        "enable_personal_skills_for_agent_config",
        enable_skills,
    )
    monkeypatch.setattr(
        install_skill_module.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(db),
    )
    monkeypatch.setattr(skill_service, "install_personal_skill_dir", install_personal_skill_dir)
    runtime = _runtime(
        uid="normal-user",
        thread_id="thread-1",
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
        workdir_path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
        skills=["existing-skill"],
    )
    result = await install_skill_module._run_install_task(
        " /home/gem/user-data/demo-skill ",
        runtime,
        "tool-1",
    )

    assert "activated_skills" not in result.update
    assert calls["prepare"]["uid"] == "normal-user"
    assert calls["prepare_thread_id"] != event_loop_thread_id
    assert calls["install"] == {"uid": "normal-user", "source_dir": source_dir}
    assert calls["prepare"]["source"] == "/home/gem/user-data/demo-skill"
    assert calls["enable"] == {
        "db": db,
        "thread_id": "thread-1",
        "uid": "normal-user",
        "skill_slugs": ["demo-skill"],
    }
    assert result.update["messages"][0].content.splitlines() == [
        "已安装 Skill: demo-skill",
        "Skill 路径: /home/gem/user-data/agents/skills/demo-skill/SKILL.md",
    ]
    assert runtime.context.skills == ["existing-skill"]


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
        "/home/gem/user-data/demo-skill",
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
@pytest.mark.parametrize(
    ("configured_skills", "expected_skills"),
    [
        ([], ["existing-skill", "new-skill"]),
        (["existing-skill"], ["existing-skill", "new-skill"]),
    ],
)
async def test_enable_skills_updates_explicit_agent_selection(monkeypatch, configured_skills, expected_skills):
    conv = SimpleNamespace(uid="user-1", agent_id="agent-1")
    agent = SimpleNamespace(
        created_by="user-1",
        config_json={"context": {"skills": configured_skills, "model": "provider:model"}},
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

    monkeypatch.setattr(
        "yuxi.repositories.conversation_repository.ConversationRepository",
        FakeConversationRepository,
    )
    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", FakeAgentRepository)

    result = await skill_service.enable_personal_skills_for_agent_config(
        SimpleNamespace(),
        thread_id="thread-1",
        uid="user-1",
        skill_slugs=["existing-skill", "new-skill"],
    )

    assert result is True
    assert calls["thread_id"] == "thread-1"
    assert calls["agent_slug"] == "agent-1"
    assert calls["update"]["updated_by"] == "user-1"
    assert calls["update"]["config_json"] == {"context": {"skills": expected_skills}}
    assert calls["update"]["config_resource_access"] == {"skills": {"existing-skill", "new-skill"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [{}, {"skills": "all"}, {"skills": ["new-skill"]}])
async def test_enable_skills_skips_update_for_all_mode_or_unchanged_selection(monkeypatch, context):
    conv = SimpleNamespace(uid="user-1", agent_id="agent-1")
    agent = SimpleNamespace(
        created_by="user-1",
        config_json={"context": context},
    )

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conv

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, _slug):
            return agent

        async def update(self, *_args, **_kwargs):
            raise AssertionError("无需更新 Agent 配置")

    monkeypatch.setattr(
        "yuxi.repositories.conversation_repository.ConversationRepository",
        FakeConversationRepository,
    )
    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", FakeAgentRepository)

    assert await skill_service.enable_personal_skills_for_agent_config(
        SimpleNamespace(),
        thread_id="thread-1",
        uid="user-1",
        skill_slugs=["new-skill"],
    )
    assert agent.config_json == {"context": context}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_uid", "agent", "must_not_call"),
    [
        ("user-1", SimpleNamespace(created_by="admin", config_json={"context": {}}), "update"),
        ("other-user", None, "get_by_slug"),
    ],
)
async def test_enable_skills_does_not_update_unowned_agent(monkeypatch, runtime_uid: str, agent, must_not_call: str):
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

        async def get_by_slug(self, _slug):
            calls["get_by_slug"] = True
            return agent

        async def update(self, *_args, **_kwargs):
            calls["update"] = True

    monkeypatch.setattr(
        "yuxi.repositories.conversation_repository.ConversationRepository",
        FakeConversationRepository,
    )
    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", FakeAgentRepository)

    result = await skill_service.enable_personal_skills_for_agent_config(
        SimpleNamespace(),
        thread_id="thread-1",
        uid=runtime_uid,
        skill_slugs=["new-skill"],
    )

    assert result is False
    assert must_not_call not in calls


def test_prepare_skill_from_sandbox_uses_sandbox_api_without_host_path_resolution(monkeypatch, tmp_path: Path):
    remote_dir = "/home/gem/user-data/demo-skill"

    class FakeProvisionerSandboxBackend:
        def __init__(self, *, thread_id, uid, workdir_path, create_if_missing):
            assert thread_id == "thread-1"
            assert uid == "user-1"
            assert workdir_path is None
            assert create_if_missing is True

        def ls(self, path):
            assert path == remote_dir
            return SimpleNamespace(
                error=None,
                entries=[{"path": f"{remote_dir}/SKILL.md", "is_dir": False, "size": 6}],
            )

        def download_files(self, paths):
            assert paths == [f"{remote_dir}/SKILL.md"]
            return [SimpleNamespace(error=None, content=b"# demo")]

    monkeypatch.setattr(sandbox_backend_module, "ProvisionerSandboxBackend", FakeProvisionerSandboxBackend)

    staging = install_skill_module._prepare_skill_from_sandbox(
        remote_dir,
        "thread-1",
        "user-1",
        tmp_path / "staging",
    )

    assert (staging / "SKILL.md").read_text(encoding="utf-8") == "# demo"


def test_prepare_skill_from_sandbox_preserves_download_error_message(monkeypatch, tmp_path: Path):
    remote_dir = "/home/gem/user-data/demo-skill"

    class FakeProvisionerSandboxBackend:
        def __init__(self, *, thread_id, uid, workdir_path, create_if_missing):
            assert thread_id == "thread-1"
            assert uid == "user-1"
            assert workdir_path is None
            assert create_if_missing is True

        def ls(self, _path):
            return SimpleNamespace(
                error=None,
                entries=[{"path": f"{remote_dir}/SKILL.md", "is_dir": False, "size": 1}],
            )

        def download_files(self, _paths):
            return [SimpleNamespace(error="read_failed", content=None)]

    monkeypatch.setattr(sandbox_backend_module, "ProvisionerSandboxBackend", FakeProvisionerSandboxBackend)

    with pytest.raises(ValueError, match="下载沙盒文件失败"):
        install_skill_module._prepare_skill_from_sandbox(
            remote_dir,
            "thread-1",
            "user-1",
            tmp_path / "staging",
        )
