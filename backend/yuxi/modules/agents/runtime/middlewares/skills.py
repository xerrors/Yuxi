"""Skill 内容披露，不改变运行能力或 checkpoint 状态。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from pathlib import PurePosixPath

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from yuxi.modules.extensions.plugins.base import is_plugin_active
from yuxi.modules.extensions.plugins.registry import get_plugin
from yuxi.modules.extensions.skills.resolved import RuntimeSkill
from yuxi.modules.workspace.runtime_paths import PERSONAL_SKILLS_PATH, SHARED_SKILLS_PATH

# 单次模型请求固定实际注入的 Skill 来源；重试不能以刷新后的权限替代此事实。
injected_skill_identity: ContextVar[dict[str, dict] | None] = ContextVar("injected_skill_identity", default=None)
_SKILL_SOURCES = [f"{SHARED_SKILLS_PATH}/", f"{PERSONAL_SKILLS_PATH}/"]


class SkillsMiddleware(AgentMiddleware):
    """注入普通 Skill 摘要、预加载正文并固定实际使用的内容来源。"""

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        """包装模型调用，披露 Skill 内容并绑定本轮来源身份"""
        runtime_context = request.runtime.context

        snapshot = runtime_context.skill_runtime_snapshot
        runtime_skills = snapshot["runtime_skills"] if snapshot is not None else {}
        preloaded_skills = snapshot["preloaded_skills"] if snapshot is not None else []
        if snapshot is not None:
            preloaded_set = set(preloaded_skills)
            prompt_sections: list[str] = []
            disclosed = set(snapshot["context_skills"])
            for plugin_id in getattr(runtime_context, "plugins", []):
                plugin = get_plugin(plugin_id)
                if is_plugin_active(plugin, runtime_context, request.state):
                    disclosed.update(slug for slug in plugin.skills if slug != plugin.entry_skill)
            lazy_skills = [slug for slug in snapshot["effective_skills"] if slug in disclosed and slug not in preloaded_set]
            if lazy_skills:
                prompt_sections.append(self._build_skills_section([runtime_skills[slug] for slug in lazy_skills]))
            if preloaded_skills:
                prompt_sections.append(self._build_preloaded_skills_section(preloaded_skills, runtime_context))
            if prompt_sections:
                system_message = append_to_system_message(getattr(request, "system_message", None), "\n\n".join(prompt_sections))
                request = request.override(system_message=system_message)

        identity = {slug: dict(metadata) for slug, metadata in (snapshot or {}).get("skill_metadata", {}).items()}
        token = injected_skill_identity.set(identity)
        try:
            return await handler(request)
        finally:
            injected_skill_identity.reset(token)

    def _build_preloaded_skills_section(self, slugs: list[str], runtime_context) -> str:
        """从完整快照构建预加载提示；未准备阶段没有正文。"""
        snapshot = runtime_context.skill_runtime_snapshot
        contents = snapshot["preloaded_skill_contents"] if snapshot is not None else {}
        sections = ["# Preloaded Skills", "The following Skill instructions are already loaded and active."]
        for slug in slugs:
            content = contents[slug]
            sections.append(f'<preloaded_skill slug="{slug}">\n{content}\n</preloaded_skill>')
        return "\n\n".join(sections)

    def _format_skills_locations(self, sources: list[str]) -> str:
        """格式化 skills 位置信息"""
        locations = []
        for i, source_path in enumerate(sources):
            name = PurePosixPath(source_path.rstrip("/")).name.capitalize()
            suffix = " (higher priority)" if i == len(sources) - 1 else ""
            locations.append(f"**{name} Skills**: `{source_path}`{suffix}")
        return "\n".join(locations)

    def _format_skills_list(self, skills_meta: list[RuntimeSkill]) -> str:
        """格式化 skills 列表"""
        if not skills_meta:
            return f"(No skills available yet. You can create skills in {' or '.join(_SKILL_SOURCES)})"

        lines = []
        for skill in skills_meta:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> Read `{skill['path']}` for full instructions")
        return "\n".join(lines)

    def _build_skills_section(self, skills_meta: list[RuntimeSkill]) -> str:
        """构建 skills 提示段"""
        skills_locations = self._format_skills_locations(_SKILL_SOURCES)
        skills_list = self._format_skills_list(skills_meta)
        return SKILLS_SYSTEM_PROMPT.format(
            skills_locations=skills_locations,
            skills_load_warnings="",
            skills_list=skills_list,
        )
