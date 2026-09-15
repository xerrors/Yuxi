"""运行清单构建、脱敏与指纹规范化的单元测试。"""

from __future__ import annotations

import pytest

from yuxi.services.agent_run_manifest_service import (
    build_skill_manifest_entries,
    build_manifest_payload,
    canonical_json,
    compute_config_digest,
    compute_manifest_fingerprint,
)


def _manifest(**overrides):
    payload = {
        "run_type": "chat",
        "agent_slug": "main",
        "backend_id": "chatbot",
        "model_spec": "siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        "tool_approval_mode": "default",
        "normalized_context": {
            "model": "siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
            "tools": ["fs", "web"],
            "mcps": [],
            "skills": ["code-review"],
            "max_execution_steps": 150,
            "model_retry_times": 2,
            "system_prompt": "You are a reviewer.",
            "summary_prompt": "Summarize: {messages}",
        },
        "limits": {"max_execution_steps": 150, "model_retry_times": 2},
        "skill_entries": [{"slug": "code-review", "version": "1.2.0", "content_hash": "abc123"}],
        "code_revision": None,
    }
    payload.update(overrides)
    return build_manifest_payload(
        run_type=payload["run_type"],
        agent_slug=payload["agent_slug"],
        backend_id=payload["backend_id"],
        model_spec=payload["model_spec"],
        tool_approval_mode=payload["tool_approval_mode"],
        normalized_context=payload["normalized_context"],
        skill_entries=payload["skill_entries"],
        code_revision=payload["code_revision"],
        limits=payload["limits"],
    )


def test_same_assets_produce_same_fingerprint_regardless_of_field_order():
    first = _manifest()
    second = _manifest(
        normalized_context={
            "skills": ["code-review"],
            "mcps": [],
            "tools": ["fs", "web"],
            "summary_prompt": "Summarize: {messages}",
            "system_prompt": "You are a reviewer.",
            "model_retry_times": 2,
            "max_execution_steps": 150,
            "model": "siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        }
    )

    assert compute_manifest_fingerprint(first) == compute_manifest_fingerprint(second)
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_different_assets_produce_different_fingerprint():
    changed = _manifest(skill_entries=[{"slug": "code-review", "version": "1.3.0", "content_hash": "abc123"}])

    assert compute_manifest_fingerprint(_manifest()) != compute_manifest_fingerprint(changed)


def test_preload_skill_config_changes_config_digest():
    base_context = _manifest()["config_digest"]
    changed_context = {
        **{
            "model": "siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
            "tools": ["fs", "web"],
            "mcps": [],
            "skills": ["code-review"],
            "max_execution_steps": 150,
            "model_retry_times": 2,
            "system_prompt": "You are a reviewer.",
            "summary_prompt": "Summarize: {messages}",
        },
        "preload_skills": ["code-review"],
    }

    assert base_context != compute_config_digest(changed_context)


def test_preloaded_dependency_content_changes_manifest_fingerprint():
    config = {"skills": ["parent"], "preload_skills": ["parent"]}
    scope = {
        "preloaded_skills": ["parent", "dependency"],
        "preloaded_skill_contents": {"parent": "first", "dependency": "dependency"},
        "skill_metadata": {
            slug: {"source_scope": "shared", "version": "v1", "content_hash": "hash"}
            for slug in ("parent", "dependency")
        },
    }
    first = build_skill_manifest_entries(config, scope)
    scope["preloaded_skill_contents"]["parent"] = "changed"
    second = build_skill_manifest_entries(config, scope)
    assert [item["slug"] for item in first] == ["parent", "dependency"]
    assert compute_manifest_fingerprint(_manifest(skill_entries=first)) != compute_manifest_fingerprint(
        _manifest(skill_entries=second)
    )


def test_personal_preloaded_skill_does_not_borrow_shared_identity():
    """个人来源即使携带同名共享元数据，也不能用于其审计身份。"""
    import hashlib

    entries = build_skill_manifest_entries(
        {"skills": ["shadowed"]},
        {
            "preloaded_skills": ["shadowed"],
            "preloaded_skill_contents": {"shadowed": "personal content"},
            "skill_metadata": {
                "shadowed": {"source_scope": "personal", "version": "shared-v1", "content_hash": "shared-hash"},
            },
        },
    )
    assert entries == [
        {
            "slug": "shadowed",
            "version": None,
            "content_hash": None,
            "preload_content_hash": hashlib.sha256(b"personal content").hexdigest(),
        }
    ]


def test_manifest_excludes_prompts_and_secret_shaped_values():
    context = {
        "system_prompt": "SECRET-PROMPT-BODY",
        "summary_prompt": "SECRET-SUMMARY-BODY",
        "api_key": "sk-live-abcdef",
        "token": "tok-live-abcdef",
        "tools": ["fs"],
        "mcps": [],
        "skills": [],
    }
    manifest = build_manifest_payload(
        run_type="chat",
        agent_slug="main",
        backend_id="chatbot",
        model_spec=None,
        tool_approval_mode=None,
        normalized_context=context,
        skill_entries=[],
        code_revision=None,
        limits={},
    )
    serialized = canonical_json(manifest)

    assert "SECRET-PROMPT-BODY" not in serialized
    assert "SECRET-SUMMARY-BODY" not in serialized
    assert "sk-live-abcdef" not in serialized
    assert "tok-live-abcdef" not in serialized
    # 未列入直接字段的 context 值只能以 config_digest 摘要存在。
    assert manifest["config_digest"] == compute_config_digest(context)
    assert manifest["resources"] == {"tools": ["fs"], "mcps": [], "skills": []}


def test_missing_code_revision_is_explicitly_unresolved():
    manifest = _manifest()

    assert manifest["code_revision"] == "unresolved"
    assert manifest["manifest_version"] == 2


def test_non_string_model_spec_normalizes_to_none():
    manifest = _manifest(model_spec="")

    assert manifest["model"] == {"spec": None}


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("max_execution_steps", 150),
        ("model_retry_times", 2),
    ],
)
def test_limits_captured_from_context(field, expected):
    assert _manifest()["limits"][field] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("run_type", ["chat", "resume", "subagent"])
@pytest.mark.parametrize("empty_config", [False, True])
async def test_manifest_uses_prepared_context_and_persisted_overrides(monkeypatch, run_type, empty_config):
    """配置覆盖、默认值、工作区提示词与 Skill 摘要来自同一执行对象。"""
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from yuxi.agents.buildin.subagent.context import SubAgentContext
    from yuxi.services import agent_run_manifest_service as service

    agent = SimpleNamespace(
        backend_id="backend",
        config_json={
            "context": {
                "model": "old",
                "system_prompt": "base",
                "parent_thread_id": "forged",
                "is_subagent_runtime": True,
                "uid": "forged",
                "worker_id": "forged",
            }
        },
    )
    if empty_config:
        agent.config_json["context"] = None
    expected_prompt = "You are a helpful assistant." if empty_config else "base"
    monkeypatch.setattr(
        service, "AgentRepository", lambda db: SimpleNamespace(get_visible_by_slug=AsyncMock(return_value=agent))
    )
    monkeypatch.setattr(
        service.agent_manager, "get_agent", lambda name: SimpleNamespace(context_schema=SubAgentContext)
    )
    monkeypatch.setattr("yuxi.agents.context._load_workspace_agent_context", lambda uid: "workspace policy")
    seen = []

    async def prepare(context):
        """模拟边界解析结果，manifest 只能读取这个对象。"""
        from yuxi.agents.context import _append_workspace_agent_prompt

        await _append_workspace_agent_prompt(context)
        seen.append(context)
        context.tools = ["read_file"]
        context.skills = ["skill-a"]
        context.preload_skills = ["skill-a"]
        context._runtime_prepared = True
        context._skill_runtime_snapshot = {
            "preloaded_skills": ["skill-a"],
            "preloaded_skill_contents": {"skill-a": "frozen skill"},
            "skill_metadata": {"skill-a": {"source_scope": "shared", "version": "v1", "content_hash": "hash"}},
        }
        return context

    monkeypatch.setattr(service, "prepare_agent_runtime_context", prepare)
    run = SimpleNamespace(
        id="run",
        request_id="request",
        agent_slug="agent",
        run_type=run_type,
        runtime_scope_id="root",
        conversation_thread_id="thread",
        input_payload={
            "model_spec": "chosen",
            "tool_approval_mode": "always_trust",
            "runtime": {"parent_thread_id": "parent"},
        },
    )
    binding = SimpleNamespace(workdir_path="projects/project")
    result = await service.prepare_run_execution(
        run=run, user=SimpleNamespace(uid="user"), db=object(), workdir_binding=binding, worker_id="owner"
    )
    assert result.context is seen[0]
    assert result.context.model == result.manifest["model"]["spec"] == "chosen"
    assert result.context.tool_approval_mode == result.manifest["tool_approval_mode"] == "always_trust"
    assert result.context.system_prompt == f"{expected_prompt}\n\nworkspace policy"
    assert result.manifest["limits"]["model_retry_times"] == result.context.model_retry_times == 2
    assert (
        result.manifest["resources"]["skills"][0]["preload_content_hash"] == hashlib.sha256(b"frozen skill").hexdigest()
    )
    assert result.context.is_subagent_runtime is (run_type == "subagent")
    assert result.context.parent_thread_id == ("parent" if run_type == "subagent" else None)
    assert result.context.uid == "user"
    assert (result.context.run_id, result.context.request_id, result.context.worker_id) == ("run", "request", "owner")

    first_digest = result.manifest["config_digest"]
    run.id, run.request_id = "different-run", "different-request"
    same_config = await service.prepare_run_execution(
        run=run, user=SimpleNamespace(uid="user"), db=object(), workdir_binding=binding, worker_id="different-owner"
    )
    assert same_config.manifest["config_digest"] == first_digest
    monkeypatch.setattr("yuxi.agents.context._load_workspace_agent_context", lambda uid: "changed policy")
    changed = await service.prepare_run_execution(
        run=run, user=SimpleNamespace(uid="user"), db=object(), workdir_binding=binding, worker_id="owner"
    )
    assert changed.manifest["config_digest"] != first_digest
    assert result.context.system_prompt == f"{expected_prompt}\n\nworkspace policy"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["agent", "backend", "user", "parent"])
async def test_execution_preparation_rejects_missing_dependencies(monkeypatch, missing):
    """缺少执行依赖必须失败，不能固化空配置并进入执行。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from yuxi.agents.buildin.subagent.context import SubAgentContext
    from yuxi.services import agent_run_manifest_service as service

    agent = None if missing == "agent" else SimpleNamespace(backend_id="backend", config_json={})
    backend = None if missing == "backend" else SimpleNamespace(context_schema=SubAgentContext)
    monkeypatch.setattr(
        service, "AgentRepository", lambda db: SimpleNamespace(get_visible_by_slug=AsyncMock(return_value=agent))
    )
    monkeypatch.setattr(service.agent_manager, "get_agent", lambda name: backend)
    monkeypatch.setattr("yuxi.agents.context._load_workspace_agent_context", lambda uid: "")
    monkeypatch.setattr(service, "prepare_agent_runtime_context", AsyncMock(side_effect=lambda context: context))
    run = SimpleNamespace(
        id="run",
        request_id="req",
        agent_slug="agent",
        run_type="subagent",
        runtime_scope_id="root",
        conversation_thread_id="child",
        input_payload={"runtime": {} if missing == "parent" else {"parent_thread_id": "parent"}},
    )
    with pytest.raises(ValueError):
        await service.prepare_run_execution(
            run=run,
            user=SimpleNamespace(uid="user"),
            db=object(),
            workdir_binding=SimpleNamespace(workdir_path="projects/project"),
            worker_id="owner",
        )
