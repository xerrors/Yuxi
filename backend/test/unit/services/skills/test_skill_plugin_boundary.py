"""普通 Skill 只提供内容，运行能力只能由部署插件声明。"""

import pytest

from yuxi.migrations.agent_plugins import convert_config
from yuxi.modules.extensions.skills.package import parse_skill_markdown


@pytest.mark.parametrize("field", ["tool_dependencies", "mcp_dependencies", "skill_dependencies"])
@pytest.mark.parametrize("value", ["[]", "[example]"])
def test_skill_parser_rejects_runtime_dependency_fields(field, value):
    """空列表也不保留第二套运行依赖协议。"""
    with pytest.raises(ValueError, match="部署插件"):
        parse_skill_markdown(f"---\nname: sample\ndescription: Test\n{field}: {value}\n---\nBody")


def test_config_conversion_keeps_direct_resources_and_does_not_activate():
    """旧入口预加载只迁移选择，不隐式创建激活状态。"""
    config = {"skills": "all", "preload_skills": ["abridge"], "tools": ["present_artifacts"], "mcps": ["knowledge"]}
    result = convert_config(config, ["abridge"])
    assert result == {**config, "mcps": [], "preload_skills": [], "plugins": ["abridge", "knowledge-base"]}
    assert config["preload_skills"] == ["abridge"]


@pytest.mark.parametrize("value", ["[]", "[knowledge]"])
def test_migration_rejects_personal_legacy_dependencies_without_granting(tmp_path, monkeypatch, value):
    """迁移明确指出个人内容修复位置，既不改文件也不创建授权。"""
    from yuxi.migrations import agent_plugins

    root = tmp_path / "agents" / "skills" / "personal"
    root.mkdir(parents=True)
    path = root / "SKILL.md"
    original = f"---\nname: personal\ndescription: test\nmcp_dependencies: {value}\n---\nBody\n"
    path.write_text(original)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path)
    with pytest.raises(ValueError, match="个人 Skill 的旧依赖从未生效") as error:
        agent_plugins.require_clean_personal_skills(["owner"])
    assert str(path) in str(error.value)
    assert path.read_text() == original
    path.write_text(original.replace(f"mcp_dependencies: {value}\n", ""))
    agent_plugins.require_clean_personal_skills(["owner"])


@pytest.mark.parametrize(
    "content",
    [
        "---\n---\n",
        "---\n- list\n---\n",
        "no frontmatter",
        "---\nname: missing-description\n---\n",
        "---\nname: ignored\ndescription: test\nmcp_dependencies: scalar\n---\n",
    ],
)
def test_migration_does_not_require_repair_of_previously_invalid_personal_files(tmp_path, monkeypatch, content):
    """迁移只拦截旧依赖，不替既有无效个人内容新增修复前置条件。"""
    from yuxi.migrations import agent_plugins

    root = tmp_path / "agents" / "skills" / "ignored"
    root.mkdir(parents=True)
    path = root / "SKILL.md"
    path.write_text(content)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path)
    agent_plugins.require_clean_personal_skills(["owner"])
    assert path.read_text() == content


@pytest.mark.parametrize("tools", [None, "all", [], ["execute"]])
def test_tool_only_skill_preserves_skill_and_tool_selection(tools):
    """清除旧工具依赖时，普通 Skill 和工具选择保持原样。"""
    config = {"skills": ["deep-research"], "preload_skills": ["deep-research"]}
    if tools is not None:
        config["tools"] = tools
    assert convert_config(config, [], migrated=[]) == config
